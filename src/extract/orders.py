"""Order tracking + 取件码 pickup digest (READ-ONLY). CLAUDE.md §0 daily-ops.

Reads 已买到的宝贝 for order#/status/item, then for active orders navigates directly to
the logistics page (…pc-trade-logistics/home.html?orderId=<id>) and parses the dinamic
frame for carrier, tracking#, latest status, station, and the 取件码 (pickup OTP). No
writes, no purchasing — the buyer forwards the digest to the China agent who collects.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from src.config import load_config
from src.dates import today_cn
from src.errors import CacheCoverageError, CaptchaError, SelectorDriftError
from src.log import get_logger
from src.models import OrderStatus

CARRIERS = ("顺丰", "中通", "圆通", "韵达", "申通", "邮政", "京东", "极兔", "德邦", "百世", "菜鸟")
ACTIVE_STATUSES = ("待发货", "待收货", "运输中", "待取件", "派送中")
_LOGISTICS_URL = "https://market.m.taobao.com/app/dinamic/pc-trade-logistics/home.html?orderId={oid}"
_BOUGHT_LIST_URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"

# Once-per-day cap (anti-detection): the first run each day fetches live and caches the
# result; same-day re-calls serve the cache (zero extra Taobao traffic) unless force=True.
# Stored under the gitignored output dir — it holds order PII (tracking#/取件码), local only.
# The cache is only used when anti_risk.track_cache is true; request filters (only_active,
# max_drill) are RE-APPLIED to the cached list, so a cache fetched with other params is
# still served correctly.

_DONE_STATUSES = ("已签收", "交易成功", "交易关闭")

# 列表页即可判定为终态、无需物流页演练的状态(用户 2026-09-10 指示: 先查状态 —
# 交易关闭的订单自然没有物流; 交易成功的包裹已签收、取件码已失效)。
_TERMINAL_LIST_STATUSES = ("交易成功", "交易关闭")

# Enumeration-stage budget (seconds): goto/captcha/scroll/evaluate must ALL finish
# within this window or the run fails loud instead of wedging the browser lock
# (2026-09-10 real-machine lesson: an unbounded evaluate on the order list hung the
# run for 8+ minutes with zero logs until the bridge's downstream timeout fired).
_ENUM_BUDGET_S = 90.0

# Drill-stage budgets (2026-09-10 第二次实机教训): fr.evaluate 没有默认超时 — 单帧
# JS 卡死把整轮演练楔死 16+ 分钟。单帧取文本 2.5s 有界; 每单整体 360s 兜底
# (goto 30 + 验证码人工上限 300 + 轮询 ~12 + 节奏 ~10, 不掐断合法的人工清除等待)。
_FRAME_EVAL_BUDGET_S = 2.5
_DRILL_BUDGET_S = 360.0

# Sane anti-block ceiling on logistics drills per run (each drill = one well-paced
# navigation on the ONE reused logistics tab). max_drill is clamped into [1, _MAX_DRILL]
# so a typo cannot ask for an unbounded drill burst or a meaningless 0.
_MAX_DRILL = 30

# Strong refs for detached drill tasks that outlived a cancelled tool task (shield) —
# keeps them alive for GC until they finish stamping the cache.
_DETACHED_DRILLS: set = set()

# ── 2026-09-10 实机取证(order_probe, 订单 3316828549329009188 / 3316830061160018370) ──
# 未发货订单的物流页不含任何 快递/驿站/承运商 字样, 只有:
#   页眉+时间线 "已下单" / "商品已经下单" / "暂无单号" → 真实状态是已下单未发货;
# 未生成包裹的订单物流页显式报 "对不起，查不到包裹信息"。
# 两者此前都不被解析词表覆盖 → 摘要里显示误导性的 "未知"。
_NOPACKAGE_STATUS = "查不到包裹信息"
_NOSHIP_STATUS = "已下单"
# 物流正文之后的推荐区("猜你喜欢…")商品标题常含 "顺丰包邮" 等字样, 会污染
# carrier/tracking 识别 — 解析前在第一个推荐区标记处截断。
_JUNK_MARKER = "猜你喜欢"
# 物流内容所在帧的 URL 提示(dinamic 页的主帧 URL 含 pc-trade-logistics; 搜索联想
# iframe 是 150KB 的 JS 文本, 绝不能当物流正文)。
_LOGISTICS_FRAME_HINT = "pc-trade-logistics"


def _clip_junk(text: str) -> str:
    """Truncate logistics text at the first recommendation-section marker."""
    i = text.find(_JUNK_MARKER)
    return text[:i] if i >= 0 else text


def _qualifies(text: str) -> bool:
    """Does this flattened text plausibly carry logistics/order-shipment state?

    Accepts shipped pages (快递/驿站/取件码/承运商) AND unshipped/no-package pages
    (已下单/暂无单号/查不到包裹信息) — the old gate dropped unshipped pages entirely,
    which is how two active orders surfaced as 未知 in the digest.
    """
    if not text:
        return False
    return ("快递" in text or "驿站" in text or "取件码" in text or "单号" in text
            or _NOSHIP_STATUS in text or _NOPACKAGE_STATUS in text
            or any(c in text for c in CARRIERS))


async def _frame_text(fr) -> str:
    """One frame's body innerText, bounded. fr.evaluate has NO default timeout —
    a wedged frame's JS used to hang the whole drill (2026-09-10 live lesson)."""
    try:
        return await asyncio.wait_for(
            fr.evaluate("() => document.body ? document.body.innerText : ''"),
            timeout=_FRAME_EVAL_BUDGET_S,
        )
    except Exception:
        return ""


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

# Collect distinct order#s (newest first) plus each card's item title. The new orders
# page renders the title as the line ending in " [交易快照]" inside the same card
# (2026-09-10 实机取证); titles used to be always empty because only ids were read.
ORDER_LIST_JS = r"""() => {
  const txt = document.body.innerText || '';
  const out = []; const seen = new Set();
  const re = /订单号[:：]?\s*(\d{15,})/g;    // Taobao order ids are ~19 digits
  const tre = /\n([^\n]{4,120})\s*\[交易快照\]/;   // \s*: 分隔符可能是 NBSP/全角空格/换行
  const sre = /\n订单详情\n([^\n]{2,12})/;   // 卡片状态行紧跟 订单详情 标签
  let m;
  while ((m = re.exec(txt)) !== null) {
    if (seen.has(m[1])) continue;
    seen.add(m[1]);
    const card = txt.slice(m.index, m.index + 1200);
    const tm = tre.exec(card);
    const sm = sre.exec(card);
    out.push({id: m[1], title: tm ? tm[1].trim() : '', status: sm ? sm[1].trim() : ''});
  }
  return out.slice(0, 60);
}"""


def parse_logistics(text: str) -> dict:
    """Pure parse of a logistics page's flattened text → carrier/tracking/取件码/station/latest.

    推荐区截断(猜你喜欢…)在所有字段匹配之前 — 商品标题里的 "顺丰包邮" 不得污染
    carrier/tracking(2026-09-10 实机取证)。未发货/无包裹页返回显式状态(已下单 /
    查不到包裹信息)而不是 None → 摘要不再显示误导性的 "未知"。
    """
    text = _clip_junk(text or "")
    if _NOPACKAGE_STATUS in text:
        return {
            "carrier": None, "tracking_no": None, "pickup_code": None,
            "station": None, "latest": _NOPACKAGE_STATUS,
        }
    carrier = next((c for c in CARRIERS if c in text), None)
    tm = re.search(r"(?:顺丰|中通|圆通|韵达|申通|邮政|京东|极兔|德邦|百世)\S*?\s*([0-9A-Za-z]{8,24})", text)
    pm = re.search(r"取(?:件|货)码[:：]?\s*([0-9A-Za-z][0-9A-Za-z\-]{1,})", text)
    sm = re.search(r"([一-龥A-Za-z0-9]{2,16}?(?:菜鸟驿站|驿站|快递柜|代收点|自提点))", text)
    # 在途/签收态优先于 "已下单"(时间线历史节点里也有已下单 — 已发货页必须报在途态)
    st = re.search(r"(待取件|已签收|派送中|运输中|已揽收|已发货|运输途中|已收货|配送中)", text)
    if not st:
        st = re.search(_NOSHIP_STATUS, text)
    return {
        "carrier": carrier,
        "tracking_no": tm.group(1) if tm else None,
        "pickup_code": pm.group(1) if pm else None,
        "station": sm.group(1) if sm else None,
        "latest": st.group(0) if st else None,
    }


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

    List-status-first (用户 2026-09-10): each card's own status line (订单详情 下一行)
    is read during enumeration; terminal list statuses (交易成功/交易关闭) skip the
    logistics navigation entirely — a closed order never has logistics, and skipping
    also avoids loading pages that contain the recipient address (privacy minimization:
    this tool carries order PII; logs keep only order#/status/carrier, tracking#/取件码
    live only in the gitignored local cache).

    only_active drops orders whose logistics status is already 已签收/交易成功/交易关闭.

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
    from src.browser.session import get_session, track_temporary_page

    session = get_session()
    page = await session.start()

    # 枚举段整体有界(2026-09-10 实机教训): goto 自带超时, 但 evaluate/滚动等待无超时 —
    # 订单列表页(全部订单标签)曾把整个运行楔死 8+ 分钟且零日志, 直到桥 downstream
    # timeout 才暴露。整段 90s 预算, 超时 fail loud 为 SelectorDriftError; 缓存不落盘,
    # 当日可重试或 force 重跑。
    async def _enumerate_cards() -> list[dict]:
        await page.goto(_BOUGHT_LIST_URL,
                        wait_until="domcontentloaded")
        await session.guard_captcha(page)
        await human_scroll(page, 3)
        await human_delay(2.0, 3.0)
        return await page.evaluate(ORDER_LIST_JS)

    try:
        cards = await asyncio.wait_for(_enumerate_cards(), timeout=_ENUM_BUDGET_S)
    except asyncio.TimeoutError as exc:
        raise SelectorDriftError(
            step="track: 已买到的宝贝 枚举段(90s 预算内未完成 — 页面可能改版或未空闲)",
        ) from exc
    cards = [c for c in cards if isinstance(c, dict) and c.get("id")]
    ids = [str(c["id"]) for c in cards]
    titles = {str(c["id"]): str(c.get("title") or "")[:60] for c in cards}
    list_statuses = {str(c["id"]): str(c.get("status") or "") for c in cards}
    get_logger().info("track: enumerated %d order ids (%d titled, %d with list status) from 已买到的宝贝",
                      len(ids), sum(1 for t in titles.values() if t),
                      sum(1 for s in list_statuses.values() if s))
    if ids and not any(titles.values()):
        get_logger().warning(
            "track: %d ids but 0 titles — [交易快照] card-title selector may have drifted",
            len(ids))
    if not ids:
        # Nothing parsed (page didn't render / soft block) — do NOT stamp an empty digest
        # as "today's run" (would serve an all-day-empty cache). Retry next call.
        get_logger().warning("track: no order ids parsed from 已买到的宝贝 — not caching an empty digest")
        return []

    # ── 演练段在独立的 shielded task 中运行(2026-09-10 第四次实机教训) ──
    # 桥的 downstream timeout 会取消在途的工具任务: 12 单演练+枚举+登录恰好压线,
    # 取消总是落在 _save_cache 之前 → 当日缓存永不落盘, 每一次 force 都白跑。
    # 演练+落盘放进 detach task: 外层任务被取消时演练继续跑完并照常落缓存
    # (客户端早已超时, 结果本身已无关紧要 — 缓存才是恢复通道)。
    async def _drill_and_cache() -> list[OrderStatus]:
        # Collect ALL orders (active + delivered) so the cache is re-filterable; the caller's
        # only_active/max_drill are applied on return.
        all_orders: list[OrderStatus] = []
        # ONE dedicated logistics tab, REUSED across all orders. Do NOT open a fresh tab per
        # order — rapid repeated tab-opening is a flag/block risk. We navigate this single tab
        # sequentially, well-paced (human_delay between orders), and recreate it AT MOST ONCE
        # if it wedges (Appendix B), never in a burst.
        lp = await session.context.new_page()
        recreated = False
        # 共享库临时页登记(ADAPTATION_GUIDE §10): 复用+至多一次重建策略保持不变;
        # 重建出的新页重新登记, scope 退出关闭仍存活的物流页(取代原 finally close)。
        async with session.temporary_pages() as tp_scope:
            track_temporary_page(tp_scope, lp)

            async def _wedge_recover(reason_oid: str) -> bool:
                """Recreate the reused logistics tab AT MOST ONCE per run (spaced by the
                human_delay below — never a burst). False → stop the drill.

                close()/new_page() both bounded: playwright 的 goto 超时是它自己的
                TimeoutError(Exception 路径, 不是 asyncio.TimeoutError), 该路径曾静默调用
                无界的 lp.close() — 楔死页的 close 永不返回且零日志(2026-09-10 第三次实机
                教训)。现在关闭 5s / 新建 30s 有界且两分支都落日志。
                """
                nonlocal lp, recreated
                if recreated:
                    # already recreated once this run — do NOT open another tab in a burst.
                    # Stop drilling; keep what we have and hand the rest back for a retry.
                    get_logger().warning("track: logistics tab wedged twice — stopping drill at order %s", reason_oid)
                    return False
                recreated = True
                get_logger().warning("track: logistics tab wedged at order %s — recreating it once", reason_oid)
                try:
                    await asyncio.wait_for(lp.close(), timeout=5.0)
                except Exception:
                    pass   # the stale page stays scope-tracked — budgeted cleanup on exit
                try:
                    lp = await asyncio.wait_for(session.context.new_page(), timeout=30.0)
                except Exception:
                    get_logger().error("track: cannot recreate the logistics tab — stopping drill at order %s", reason_oid)
                    return False
                track_temporary_page(tp_scope, lp)
                return True

            async def _drill_one(order: OrderStatus, tab) -> None:
                await tab.goto(_LOGISTICS_URL.format(oid=order.order_id), wait_until="domcontentloaded")
                await session.guard_captcha(tab)   # a slider on the logistics page → human handoff
                ltext = ""
                for _ in range(6):  # the dinamic frame renders async + slowly — poll ~12s
                    await human_delay(1.4, 2.0)
                    texts: list[tuple[str, str]] = []
                    for fr in tab.frames:
                        t = await _frame_text(fr)
                        if t:
                            texts.append((fr.url or "", t))
                    # 优先 pc-trade-logistics 主帧(搜索联想 iframe 是 150KB 的 JS 文本);
                    # 兜底才接受任意合格帧。未发货/无包裹页也合格(_qualifies)。
                    main = [t for u, t in texts
                            if _LOGISTICS_FRAME_HINT in u and _qualifies(t)]
                    ltext = main[0] if main else next(
                        (t for _, t in texts if _qualifies(t)), "")
                    if ltext:
                        break
                info = parse_logistics(ltext)
                order.carrier, order.tracking_no = info["carrier"], info["tracking_no"]
                order.pickup_code, order.station = info["pickup_code"], info["station"]
                order.status = info["latest"] or "未知"
                order.latest = info["latest"]

            for i, oid in enumerate(ids[:drill_n], 1):
                o = OrderStatus(order_id=oid, title=titles.get(oid, ""), status="未知")
                if list_statuses.get(oid, "") in _TERMINAL_LIST_STATUSES:
                    # 列表状态已是终态: 交易关闭永远不会有物流; 交易成功包裹已签收、
                    # 取件码已失效 — 都不值得一次物流页导航(少一次含收件地址的页面
                    # 加载, 也是隐私最小化)。状态直接采信列表页。
                    o.status = list_statuses[oid]
                    all_orders.append(o)
                    get_logger().info("track: order %s → %s (list status; logistics drill skipped)",
                                      oid, o.status)
                    await human_delay(1.0, 2.0)   # 轻节奏, 无导航
                    continue
                get_logger().info("track: drilling order %s (%d/%d)", oid, i, len(ids[:drill_n]))
                try:
                    # 每单整体预算兜底: fr.evaluate 已单帧有界, 这里再防 goto/captcha 段的
                    # 意外悬挂; 360s 不掐断合法的人工清除等待(captcha 上限 300s)。
                    await asyncio.wait_for(_drill_one(o, lp), timeout=_DRILL_BUDGET_S)
                except asyncio.TimeoutError:
                    get_logger().warning(
                        "track: order %s drill exceeded %.0fs budget — treating as a wedge",
                        oid, _DRILL_BUDGET_S)
                    if not await _wedge_recover(oid):
                        break
                except CaptchaError:
                    raise  # real slider the human hasn't cleared → propagate, do NOT treat as a wedge
                except Exception:
                    # the reused tab may have wedged — recreate it ONCE so the next order
                    # has a live tab (never a burst; second wedge stops the drill).
                    if not await _wedge_recover(oid):
                        break
                all_orders.append(o)
                # 隐私最小化(用户 2026-09-10: tracking 属高隐私工具): 日志只留
                # 订单号+状态+承运商, 运单号/取件码只进 gitignored 缓存, 不落日志。
                get_logger().info("track: order %s → %s | %s", oid, o.status,
                                  o.carrier or "-")
                await human_delay(4.0, 7.0)   # space logistics navigations — never burst
        # 物流页关闭由 tp_scope 退出负责(仍存活的页关闭; 重建前的旧页报 already_closed)。
        _save_cache(all_orders)   # stamp today's run so same-day re-calls serve the cache
        return _filter_orders(all_orders, only_active, drill_n)
    drill_task = asyncio.get_running_loop().create_task(_drill_and_cache())
    try:
        return await asyncio.shield(drill_task)
    except asyncio.CancelledError:
        get_logger().warning(
            "track: tool task cancelled (downstream timeout) — the drill continues "
            "detached and will stamp today's cache when it completes")
        _DETACHED_DRILLS.add(drill_task)
        drill_task.add_done_callback(_DETACHED_DRILLS.discard)
        raise


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


async def probe_orders_evidence(order_id: str = "") -> dict:
    """[DEBUG order_probe] Read-only DOM-evidence dump for orders selector-drift work.

    Navigates the bought-items list page (and, when order_id is given, that order's
    logistics page) and returns bounded evidence: today's card snippets around each
    订单号 match, per-frame innerText of the logistics page, and the CURRENT parser's
    hit counts against that text. No writes; the logistics tab is a temporary-pages
    scope member and closes on exit (guide §6). Diagnostics only — never part of the
    digest path.
    """
    from src.browser.pacing import human_delay
    from src.browser.session import get_session, track_temporary_page

    session = get_session()
    page = await session.start()
    out: dict = {"list": {}, "logistics": None}

    # --- 列表页: 每个订单号匹配附近的卡片文本(今日卡片结构/状态措辞证据) ---
    await page.goto(_BOUGHT_LIST_URL, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_delay(2.0, 3.0)
    list_js = r"""() => {
      const txt = document.body.innerText || '';
      const cards = [];
      const re = /订单号[:：]?\s*(\d{15,})/g; let m;
      while ((m = re.exec(txt)) !== null && cards.length < 3) {
        cards.push({id: m[1], around: txt.slice(Math.max(0, m.index - 300), m.index + 140)});
      }
      return {total_len: txt.length, n_ids: cards.length, cards};
    }"""
    try:
        out["list"] = await page.evaluate(list_js)
    except Exception as exc:  # noqa: BLE001
        out["list"] = {"error": str(exc)[:120]}

    # 生产枚举 JS 的实机验证: 直接跑 ORDER_LIST_JS 本体, 确认 id/title/status
    # 三元组提取(状态行在 订单详情 下一行)与列表页当前渲染一致 — 只读, 无导航。
    try:
        parsed = await page.evaluate(ORDER_LIST_JS)
        out["cards_parsed"] = [c for c in parsed if isinstance(c, dict)][:5]
    except Exception as exc:  # noqa: BLE001
        out["cards_parsed"] = {"error": str(exc)[:120]}

    # --- 物流页: 分帧文本 + 当前解析器命中(活跃包裹漂移现场) ---
    if order_id:
        lp = await session.context.new_page()
        async with session.temporary_pages() as scope:
            track_temporary_page(scope, lp)
            try:
                await lp.goto(_LOGISTICS_URL.format(oid=order_id), wait_until="domcontentloaded")
                await session.guard_captcha(lp)
                frames: list[dict] = []
                for _ in range(6):  # dinamic 渲染慢 — 轮询 ~12s, 与演练段同参数
                    await human_delay(1.4, 2.0)
                    frames = []
                    for fr in lp.frames:
                        try:
                            t = await fr.evaluate("() => document.body ? document.body.innerText : ''")
                        except Exception:
                            t = ""
                        if t:
                            frames.append({"url": (fr.url or "")[:140], "len": len(t),
                                           "text": t[:1500]})
                    joined = " ".join(f["text"] for f in frames)
                    if _qualifies(joined):
                        break
                out["logistics"] = {
                    "url": (lp.url or "")[:160],
                    "n_frames": len(lp.frames),
                    "frames": frames[:4],
                    "parse_hits": parse_logistics(" ".join(f["text"] for f in frames)),
                }
            except CaptchaError:
                raise
            except Exception as exc:  # noqa: BLE001
                out["logistics"] = {"error": str(exc)[:160]}
    return out
