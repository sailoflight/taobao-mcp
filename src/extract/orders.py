"""Order tracking + 取件码 pickup digest (READ-ONLY). CLAUDE.md §0 daily-ops.

Reads 已买到的宝贝 for order#/status/item, then for active orders navigates directly to
the logistics page (…pc-trade-logistics/home.html?orderId=<id>) and parses the dinamic
frame for carrier, tracking#, latest status, station, and the 取件码 (pickup OTP). No
writes, no purchasing — the buyer forwards the digest to the China agent who collects.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import load_config
from src.dates import today_cn
from src.errors import CacheCoverageError, CaptchaError
from src.log import get_logger
from src.models import OrderStatus

CARRIERS = ("顺丰", "中通", "圆通", "韵达", "申通", "邮政", "京东", "极兔", "德邦", "百世", "菜鸟")
ACTIVE_STATUSES = ("待发货", "待收货", "运输中", "待取件", "派送中")
_LOGISTICS_URL = "https://market.m.taobao.com/app/dinamic/pc-trade-logistics/home.html?orderId={oid}"

# Once-per-day cap (anti-detection): the first run each day fetches live and caches the
# result; same-day re-calls serve the cache (zero extra Taobao traffic) unless force=True.
# Stored under the gitignored output dir — it holds order PII (tracking#/取件码), local only.
# The cache is only used when anti_risk.track_cache is true; request filters (only_active,
# max_drill) are RE-APPLIED to the cached list, so a cache fetched with other params is
# still served correctly.

_DONE_STATUSES = ("已签收", "交易成功")

# Sane anti-block ceiling on logistics drills per run (each drill = one well-paced
# navigation on the ONE reused logistics tab). max_drill is clamped into [1, _MAX_DRILL]
# so a typo cannot ask for an unbounded drill burst or a meaningless 0.
_MAX_DRILL = 30


def _effective_drill(max_drill) -> int:
    """Pure: clamp max_drill into [1, _MAX_DRILL]. None/0/negative → 1 (drill at least
    one); an out-of-range or unparseable value is capped at _MAX_DRILL."""
    try:
        n = int(max_drill)
    except (TypeError, ValueError):
        return _MAX_DRILL
    return max(1, min(_MAX_DRILL, n))


def _validate_drill(max_drill) -> int:
    """Pure: validate the requested drill depth BEFORE any navigation.

    Rejects an invalid request with a clear ValueError instead of silently clamping —
    a silent clamp of 0/negative would let a caller stamp an under-drilled/empty cache for
    the whole day. `None` means 'everything' → the full-cap coverage depth. Valid range:
    1.._MAX_DRILL (inclusive).
    """
    if max_drill is None:
        return _MAX_DRILL
    try:
        n = int(max_drill)
    except (TypeError, ValueError):
        n = -1
    if n < 1 or n > _MAX_DRILL:
        raise ValueError(
            f"max_drill must be an integer 1..{_MAX_DRILL} (None = everything), "
            f"got {max_drill!r}. Refusing to run with an invalid depth (would cache "
            f"an under-drilled result for the day)."
        )
    return n


def _cache_covers(cached_drilled, max_drill) -> bool:
    """Pure: can a cache that drilled `cached_drilled` orders serve a `max_drill` request?

    The once-per-day cache is only correct when it covers what the request asks for;
    a larger max_drill than was drilled would silently under-serve (a missed parcel
    pickup code). None for `cached_drilled` (legacy cache with no coverage metadata) is
    treated as NOT covered → a live refetch re-stamps coverage. None for `max_drill`
    ('everything') needs full-cap coverage.
    """
    if cached_drilled is None:
        return False
    return int(cached_drilled) >= _effective_drill(max_drill)


def _cache_enabled() -> bool:
    """True when anti_risk.track_cache is on (once-per-day cache honored)."""
    try:
        return bool(load_config().anti_risk.track_cache)
    except Exception:
        return True


def _state_file() -> Path:
    return Path(load_config().output.dir) / ".track_state.json"


def _load_cached_today() -> list[OrderStatus] | None:
    """Return today's cached orders if the digest already ran today AND caching is enabled.

    Honors anti_risk.track_cache: when it is off, no cache is ever served (always live).
    """
    if not _cache_enabled():
        return None
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        if data.get("date") == today_cn():
            return [OrderStatus(**o) for o in data.get("orders", [])]
    except Exception:
        pass
    return None


def _filter_orders(orders: list[OrderStatus], only_active: bool, max_drill: int) -> list[OrderStatus]:
    """Re-apply the caller's request filters to a fetched or cached order list.

    only_active drops already-collected (已签收/交易成功) orders; max_drill keeps the
    newest N (order ids are newest-first), CLAMPED into [1, _MAX_DRILL] so a 0/negative/
    absurd value never disables the cap or asks for an unbounded burst. Applied on BOTH
    the live fetch and the cache serve so the result matches the request regardless of
    how the cache was built.
    """
    out = list(orders or [])
    if only_active:
        out = [o for o in out if o.status not in _DONE_STATUSES]
    return out[:_effective_drill(max_drill)]


def has_cached_today() -> bool:
    """True if today's digest already ran (so a re-call would serve cache, not fetch)."""
    return _load_cached_today() is not None


def _save_cache(orders: list[OrderStatus]) -> None:
    try:
        p = _state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"date": today_cn(), "drilled": len(orders or []),
                        "orders": [o.model_dump() for o in orders or []]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # caching is best-effort; never fail the digest over it


def _cached_drilled() -> int | None:
    """How many orders today's cache drilled (None = no valid today-cache / legacy)."""
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        if data.get("date") == today_cn():
            d = data.get("drilled")
            return int(d) if isinstance(d, (int, float)) else None
    except Exception:
        pass
    return None

# Collect distinct order#s in document order (newest first). The new orders page doesn't
# expose clean per-card status, so we read each order's real status from its logistics page.
ORDER_LIST_JS = r"""() => {
  const txt = document.body.innerText || '';
  const ids = []; const seen = new Set();
  const re = /订单号[:：]?\s*(\d{15,})/g;     // Taobao order ids are ~19 digits
  let m;
  while ((m = re.exec(txt)) !== null) { if (!seen.has(m[1])) { seen.add(m[1]); ids.push(m[1]); } }
  return ids.slice(0, 60);
}"""


def parse_logistics(text: str) -> dict:
    """Pure parse of a logistics page's flattened text → carrier/tracking/取件码/station/latest."""
    text = text or ""
    carrier = next((c for c in CARRIERS if c in text), None)
    tm = re.search(r"(?:顺丰|中通|圆通|韵达|申通|邮政|京东|极兔|德邦|百世)\S*?\s*([0-9A-Za-z]{8,24})", text)
    pm = re.search(r"取(?:件|货)码[:：]?\s*([0-9A-Za-z][0-9A-Za-z\-]{1,})", text)
    sm = re.search(r"([一-龥A-Za-z0-9]{2,16}?(?:菜鸟驿站|驿站|快递柜|代收点|自提点))", text)
    st = re.search(r"(待取件|已签收|派送中|运输中|已揽收|已发货|运输途中|已收货|配送中)", text)
    return {
        "carrier": carrier,
        "tracking_no": tm.group(1) if tm else None,
        "pickup_code": pm.group(1) if pm else None,
        "station": sm.group(1) if sm else None,
        "latest": st.group(1) if st else None,
    }


def parse_order_title(card_text: str) -> str:
    """Best-effort item descriptor from an order card's flattened text."""
    t = re.sub(r"^.*?(?:交易成功|待收货|待发货|待评价|待取件|派送中|已签收|运输中)\s*", "", card_text or "")
    t = re.sub(r"订单号[:：]?\s*\d+", "", t).strip()
    return (t[:60] or (card_text or "")[:60]).strip()


def order_digest(orders: list[OrderStatus]) -> str:
    """Markdown table + a ready-to-forward Chinese message listing pickups (取件码)."""
    lines = ["| Order# | Item | Status | Carrier+Tracking | 取件码 | Station |",
             "|---|---|---|---|---|---|"]
    pickups: list[OrderStatus] = []
    for o in orders:
        ct = f"{o.carrier or ''} {o.tracking_no or ''}".strip() or "—"
        lines.append(f"| {o.order_id} | {(o.title or '')[:18]} | {o.status} | {ct} | {o.pickup_code or '—'} | {o.station or '—'} |")
        if o.pickup_code:
            pickups.append(o)
    md = "\n".join(lines)
    if pickups:
        msg = "今日待取件：\n" + "\n".join(
            f"{i+1}）订单{o.order_id}，{o.carrier or ''}{o.tracking_no or ''}，取件码 {o.pickup_code}，{o.station or ''}".strip()
            for i, o in enumerate(pickups)
        ) + "\n麻烦帮忙取一下，谢谢！"
        md += "\n\n**Forward to agent (Chinese):**\n" + msg
    return md


async def track_orders(
    only_active: bool = True, max_drill: int = 10, force: bool = False
) -> list[OrderStatus]:
    """Live: read order#s from 已买到的宝贝, then drill the newest `max_drill` orders'
    logistics for real status + carrier/tracking# + 取件码 + station (read-only).

    only_active drops orders whose logistics status is already 已签收/交易成功.

    ONCE-PER-DAY cap (anti-detection): the first call each day fetches live and caches the
    result (with the drilled-coverage metadata); later same-day calls return the cache with
    NO Taobao traffic — but only when the cache's drilled coverage is >= the requested
    max_drill. A request for MORE orders than were drilled raises CacheCoverageError (an
    explicit coverage-limited signal) instead of silently under-serving or auto-refetching,
    preserving the one-live-run/day cap. Pass force=True only when you genuinely need an
    extra same-day live run (e.g. a parcel just arrived) — then the refetch re-stamps the
    cache with the deeper coverage.
    max_drill is VALIDATED before any navigation: an integer 1.._MAX_DRILL (or None =
    everything); 0/negative/out-of-range/non-numeric is rejected with ValueError so a bad
    depth can never stamp an under-drilled/empty cache for the day.
    The reused logistics tab is recreated at most ONCE (if it wedges); a second wedge stops
    the drill rather than opening a fresh tab in a burst. Each logistics page is
    captcha-guarded (a real slider hands off to the human; CaptchaError is propagated,
    never swallowed as a wedge).
    """
    drill_n = _validate_drill(max_drill)   # reject <1 / >cap BEFORE any navigation/cache read
    if (not force) and _cache_enabled():
        cached = _load_cached_today()
        if cached is not None:
            cached_drilled = _cached_drilled()
            if _cache_covers(cached_drilled, drill_n):
                return _filter_orders(cached, only_active, drill_n)  # serve cache, zero traffic
            # Cache exists but doesn't cover the request → do NOT auto-refetch (would run a
            # second live flow in one day and silently stamp a fresh cache). Surface an
            # explicit coverage-limited error; the caller may force=True for an extra run.
            raise CacheCoverageError(cached_drilled, drill_n)

    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    await page.goto("https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm",
                    wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_scroll(page, 3)
    await human_delay(2.0, 3.0)
    ids = await page.evaluate(ORDER_LIST_JS)
    if not ids:
        # Nothing parsed (page didn't render / soft block) — do NOT stamp an empty digest
        # as "today's run" (would serve an all-day-empty cache). Retry next call.
        get_logger().warning("track: no order ids parsed from 已买到的宝贝 — not caching an empty digest")
        return []

    # Collect ALL orders (active + delivered) so the cache is re-filterable; the caller's
    # only_active/max_drill are applied on return.
    all_orders: list[OrderStatus] = []
    # ONE dedicated logistics tab, REUSED across all orders. Do NOT open a fresh tab per
    # order — rapid repeated tab-opening is a flag/block risk. We navigate this single tab
    # sequentially, well-paced (human_delay between orders), and recreate it AT MOST ONCE
    # if it wedges (Appendix B), never in a burst.
    lp = await session.context.new_page()
    recreated = False
    try:
        for oid in ids[:drill_n]:
            o = OrderStatus(order_id=oid, title="", status="未知")
            try:
                await lp.goto(_LOGISTICS_URL.format(oid=oid), wait_until="domcontentloaded")
                await session.guard_captcha(lp)   # a slider on the logistics page → human handoff
                ltext = ""
                for _ in range(6):  # the dinamic frame renders async + slowly — poll ~12s
                    await human_delay(1.4, 2.0)
                    for fr in lp.frames:
                        try:
                            t = await fr.evaluate("() => document.body ? document.body.innerText : ''")
                        except Exception:
                            t = ""
                        if t and ("快递" in t or "驿站" in t or any(c in t for c in CARRIERS)):
                            ltext = t
                            break
                    if ltext:
                        break
                info = parse_logistics(ltext)
                o.carrier, o.tracking_no = info["carrier"], info["tracking_no"]
                o.pickup_code, o.station = info["pickup_code"], info["station"]
                o.status = info["latest"] or "未知"
                o.latest = info["latest"]
            except CaptchaError:
                raise  # real slider the human hasn't cleared → propagate, do NOT treat as a wedge
            except Exception:
                if not recreated:
                    # the reused tab may have wedged — recreate it ONCE (spaced by the
                    # human_delay below, so still no burst) so the next order has a live tab.
                    recreated = True
                    try:
                        await lp.close()
                    except Exception:
                        pass
                    lp = await session.context.new_page()
                else:
                    # already recreated once this run — do NOT open another tab in a burst.
                    # Stop drilling; keep what we have and hand the rest back for a retry.
                    get_logger().warning("track: logistics tab wedged twice — stopping drill at order %s", oid)
                    break
            all_orders.append(o)
            await human_delay(4.0, 7.0)   # space logistics navigations — never burst
    finally:
        try:
            await lp.close()
        except Exception:
            pass
    _save_cache(all_orders)   # stamp today's run so same-day re-calls serve the cache
    return _filter_orders(all_orders, only_active, drill_n)


def _tracking_markdown(orders: list) -> str:
    """Pure: 把今日订单物流摘要渲染成可读 markdown 表(代购转发用).

    有取件码的订单状态标 "📦待取件"(醒目, 代购优先收件).
    """
    lines = [f"### 今日物流摘要({len(orders)} 单)", "",
             "| 订单号 | 状态 | 物流 | 单号 | 取件码 | 驿站 |", "|---|---|---|---|---|---|"]
    for o in orders:
        oid = str(getattr(o, "order_id", "") or "")
        status = getattr(o, "status", "") or "-"
        if getattr(o, "pickup_code", None):
            status = "📦待取件" if status == "-" else f"📦{status}"
        lines.append(f"| {oid} | {status} | {getattr(o, 'carrier', '') or '-'} "
                     f"| {getattr(o, 'tracking_no', '') or '-'} | {getattr(o, 'pickup_code', '') or '-'} "
                     f"| {getattr(o, 'station', '') or '-'} |")
    return "\n".join(lines)
