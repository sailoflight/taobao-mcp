"""Order tracking + 取件码 pickup digest (READ-ONLY). CLAUDE.md §0 daily-ops.

Reads 已买到的宝贝 for order#/status/item, then for active orders navigates directly to
the logistics page (…pc-trade-logistics/home.html?orderId=<id>) and parses the dinamic
frame for carrier, tracking#, latest status, station, and the 取件码 (pickup OTP). No
writes, no purchasing — the buyer forwards the digest to the China agent who collects.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import OrderStatus

CARRIERS = ("顺丰", "中通", "圆通", "韵达", "申通", "邮政", "京东", "极兔", "德邦", "百世", "菜鸟")
ACTIVE_STATUSES = ("待发货", "待收货", "运输中", "待取件", "派送中")
_LOGISTICS_URL = "https://market.m.taobao.com/app/dinamic/pc-trade-logistics/home.html?orderId={oid}"

# Once-per-day cap (anti-detection): the first run each day fetches live and caches the
# result; same-day re-calls serve the cache (zero extra Taobao traffic) unless force=True.
# Stored under the gitignored output dir — it holds order PII (tracking#/取件码), local only.
_CN_TZ = timezone(timedelta(hours=8))  # China is UTC+8 year-round (no DST); no tzdata needed


def _today_cn() -> str:
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d")


def _state_file() -> Path:
    from src.config import load_config
    return Path(load_config().output.dir) / ".track_state.json"


def _load_cached_today() -> list[OrderStatus] | None:
    """Return today's cached orders if the digest already ran today, else None."""
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        if data.get("date") == _today_cn():
            return [OrderStatus(**o) for o in data.get("orders", [])]
    except Exception:
        pass
    return None


def has_cached_today() -> bool:
    """True if today's digest already ran (so a re-call would serve cache, not fetch)."""
    return _load_cached_today() is not None


def _save_cache(orders: list[OrderStatus]) -> None:
    try:
        p = _state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"date": _today_cn(), "orders": [o.model_dump() for o in orders]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # caching is best-effort; never fail the digest over it

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
    result; later same-day calls return the cache with NO Taobao traffic. Pass force=True
    only when you genuinely need a same-day refresh (e.g. a parcel just arrived).
    """
    if not force:
        cached = _load_cached_today()
        if cached is not None:
            return cached   # already ran today — serve cache, hit Taobao zero times

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

    orders: list[OrderStatus] = []
    # ONE dedicated logistics tab, REUSED across all orders. Do NOT open a fresh tab per
    # order — rapid repeated tab-opening is a flag/block risk. We navigate this single tab
    # sequentially, well-paced (human_delay between orders), and only recreate it at most
    # once if it wedges (Appendix B), never in a burst.
    lp = await session.context.new_page()
    try:
        for oid in ids[:max_drill]:
            o = OrderStatus(order_id=oid, title="", status="未知")
            try:
                await lp.goto(_LOGISTICS_URL.format(oid=oid), wait_until="domcontentloaded")
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
            except Exception:
                # the reused tab may have wedged — recreate it ONCE (spaced by the
                # human_delay below, so still no burst) so the next order has a live tab.
                try:
                    await lp.close()
                except Exception:
                    pass
                lp = await session.context.new_page()
            if not (only_active and o.status in ("已签收", "交易成功")):
                orders.append(o)   # drop already-collected orders when only_active
            await human_delay(4.0, 7.0)   # space logistics navigations — never burst
    finally:
        try:
            await lp.close()
        except Exception:
            pass
    _save_cache(orders)   # stamp today's run so same-day re-calls serve the cache
    return orders
