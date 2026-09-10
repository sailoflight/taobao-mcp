"""Tests for order-tracking pure parsers (logistics text → fields; digest). No live data."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.extract import orders as O
from src.extract.orders import order_digest, parse_logistics
from src.models import OrderStatus

# A parcel sitting at a pickup station with an ACTIVE 取件码:
PICKUP_TEXT = "已揽收 运输中 中通快递 78912345678901 复制 文化路菜鸟驿站，请凭取货码 1-2-3456 取件"
# A delivered parcel (code already used → none shown):
DONE_TEXT = "已签收 武汉市 中通快递 79007243724230 交诚B区店店菜鸟驿站，感谢使用菜鸟驿站"


# 2026-09-10 实机取证(order_probe, 订单 3316828549329009188)脱敏: 未发货订单的物流页
# — 页眉+时间线 "已下单/暂无单号/商品已经下单", 其后紧跟推荐区(商品标题常含 "顺丰包邮")。
UNSHIPPED_TEXT = (
    "物流详情\n已下单\nUSB3.0转Type-c转接头转换器通用连接U盘鼠标键\n联系商家\n暂无单号\n"
    "已下单\n昨天 18:54\n商品已经下单\n李，186****6185，广东省 深圳市 南山区\n"
    "猜你喜欢\n碧云泉G3台式净饮一体机 顺丰包邮 ¥2410.8\nSovol干燥箱 ¥196.91"
)
# 2026-09-10 实机取证(订单 3316830061160018370)脱敏: 未生成包裹的物流页。
NOPACKAGE_TEXT = "物流详情\n搜淘宝\n对不起，查不到包裹信息"


def test_parse_logistics_pickup_code():
    info = parse_logistics(PICKUP_TEXT)
    assert info["carrier"] == "中通"
    assert info["tracking_no"] == "78912345678901"
    assert info["pickup_code"] == "1-2-3456"
    assert "驿站" in (info["station"] or "")


def test_parse_logistics_delivered_has_no_active_code():
    info = parse_logistics(DONE_TEXT)
    assert info["carrier"] == "中通"
    assert info["tracking_no"] == "79007243724230"
    assert info["pickup_code"] is None
    assert info["latest"] == "已签收"


def test_parse_logistics_empty():
    assert parse_logistics("") == {
        "carrier": None, "tracking_no": None, "pickup_code": None, "station": None, "latest": None
    }


def test_parse_logistics_unshipped_order_reports_ordered():
    """未发货页(已下单/暂无单号): 状态必须是 已下单 而不是 未知; 推荐区里的
    "顺丰包邮" 不得污染 carrier/tracking(截断于 猜你喜欢)。"""
    info = parse_logistics(UNSHIPPED_TEXT)
    assert info["latest"] == "已下单"
    assert info["carrier"] is None
    assert info["tracking_no"] is None
    assert info["pickup_code"] is None


def test_parse_logistics_no_package_page_is_explicit():
    """"查不到包裹信息" 页要如实上报该状态, 不能落到 未知。"""
    info = parse_logistics(NOPACKAGE_TEXT)
    assert info["latest"] == "查不到包裹信息"
    assert info["carrier"] is None and info["tracking_no"] is None


def test_parse_logistics_truncates_recommendation_junk():
    """推荐区标题里的 承运商+单号 不得盖过正文解析(截断于第一个 猜你喜欢)。"""
    polluted = DONE_TEXT + "\n猜你喜欢\n特斯拉P100 顺丰包邮 SF78999999CN 顺丰 1234567890"
    info = parse_logistics(polluted)
    assert info["carrier"] == "中通"
    assert info["tracking_no"] == "79007243724230"
    assert info["latest"] == "已签收"


def test_parse_logistics_in_transit_wins_over_timeline_ordered_node():
    """"已下单" 也会出现在已发货订单时间线的历史节点里 — 在途态必须优先。"""
    text = "已下单 昨天 18:54 商品已经下单\n运输中 中通快递 78531122334455 复制"
    assert parse_logistics(text)["latest"] == "运输中"


def test_qualifies_gate_accepts_unshipped_and_rejects_empty():
    """旧门槛只认 快递/驿站/承运商, 未发货页全部落空 → ltext 为空 → 未知。
    新门槛必须放行 未发货/无包裹/暂无单号 页, 且继续拒绝空文本。"""
    assert O._qualifies(UNSHIPPED_TEXT) is True
    assert O._qualifies(NOPACKAGE_TEXT) is True
    assert O._qualifies(DONE_TEXT) is True
    assert O._qualifies("") is False
    assert O._qualifies("随便什么无关文本") is False


def test_order_digest_emits_pickup_message():
    orders = [
        OrderStatus(order_id="3304", title="P100", status="待取件", carrier="中通",
                    tracking_no="78912345678901", pickup_code="1-2-3456", station="文化路菜鸟驿站"),
        OrderStatus(order_id="3305", title="x", status="待收货"),
    ]
    md = order_digest(orders)
    assert "1-2-3456" in md and "3304" in md
    assert "今日待取件" in md          # ready-to-forward Chinese agent message


def test_track_orders_once_per_day_cache(tmp_path, monkeypatch):
    """The once-per-day cap: today's cache is served; a stale (past-date) cache is ignored."""
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    assert O.has_cached_today() is False          # nothing cached yet → would fetch live
    assert O._load_cached_today() is None

    sample = [OrderStatus(order_id="X1", title="t", status="待取件", carrier="顺丰",
                          tracking_no="SF123456", pickup_code="8-2-1234", station="菜鸟驿站")]
    O._save_cache(sample)                          # stamps today's date
    assert O.has_cached_today() is True            # same-day re-call serves cache (no Taobao hit)
    got = O._load_cached_today()
    assert got is not None and len(got) == 1 and got[0].pickup_code == "8-2-1234"

    # a cache from a previous day must NOT count as today's run
    state.write_text(json.dumps({"date": "2000-01-01", "orders": []}), encoding="utf-8")
    assert O.has_cached_today() is False
    assert O._load_cached_today() is None


def test_load_cache_honors_track_cache_flag(tmp_path, monkeypatch):
    """anti_risk.track_cache=false ⇒ no cache is ever served, even if one exists."""
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)
    monkeypatch.setattr(O, "_cache_enabled", lambda: False)
    O._save_cache([OrderStatus(order_id="X1", title="t", status="待取件")])
    assert O._load_cached_today() is None
    assert O.has_cached_today() is False


def test_filter_orders_applies_request_params():
    orders = [
        OrderStatus(order_id="1", title="a", status="已签收"),
        OrderStatus(order_id="2", title="b", status="待取件"),
        OrderStatus(order_id="3", title="c", status="运输中"),
        OrderStatus(order_id="4", title="d", status="未知"),
    ]
    # only_active drops 已签收; max_drill keeps the newest N
    got = O._filter_orders(orders, only_active=True, max_drill=2)
    assert [o.order_id for o in got] == ["2", "3"]
    # only_active=False keeps everything up to max_drill
    got = O._filter_orders(orders, only_active=False, max_drill=10)
    assert len(got) == 4
    # bad max_drill falls back to no cap
    assert len(O._filter_orders(orders, only_active=False, max_drill=None)) == 4


def test_track_orders_serves_cache_with_request_filters(tmp_path, monkeypatch):
    """The once-per-day cache is re-filtered by the caller's only_active/max_drill
    when the cache's drilled coverage is >= the request (no browser involved)."""
    import asyncio

    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)
    # cache stores the FULL (unfiltered) set, including delivered orders (drilled=3)
    O._save_cache([
        OrderStatus(order_id="1", title="a", status="已签收"),
        OrderStatus(order_id="2", title="b", status="待取件", pickup_code="1-2-3456"),
        OrderStatus(order_id="3", title="c", status="运输中"),
    ])
    got = asyncio.run(O.track_orders(only_active=True, max_drill=2))
    assert [o.order_id for o in got] == ["2", "3"]   # 已签收 dropped, capped to 2
    got_all = asyncio.run(O.track_orders(only_active=False, max_drill=3))  # covered: 3 <= drilled 3
    assert len(got_all) == 3                          # full set when nothing filtered
    got_force = asyncio.run(O.track_orders(only_active=True, max_drill=1))
    assert [o.order_id for o in got_force] == ["2"]


def test_cache_covers_uncovered_request():
    """A request for MORE orders than the cache drilled is NOT covered (no silent
    under-serve); the caller gets an explicit CacheCoverageError at track_orders time."""
    assert O._cache_covers(1, 1) is True
    assert O._cache_covers(1, 5) is False   # 5 > drilled → not covered


def test_effective_drill_clamps_1_to_cap():
    assert O._effective_drill(1) == 1
    assert O._effective_drill(10) == 10
    assert O._effective_drill(0) == 1                       # 0 → drill at least one
    assert O._effective_drill(-5) == 1
    assert O._effective_drill(999) == O._MAX_DRILL == 30    # sane cap ceiling
    assert O._effective_drill(None) == O._MAX_DRILL
    assert O._effective_drill("abc") == O._MAX_DRILL


def test_filter_orders_clamps_max_drill():
    orders = [OrderStatus(order_id=str(i), title="t", status="待取件") for i in range(40)]
    assert len(O._filter_orders(orders, only_active=False, max_drill=999)) == O._MAX_DRILL == 30
    assert len(O._filter_orders(orders, only_active=False, max_drill=0)) == 1
    assert len(O._filter_orders(orders, only_active=False, max_drill=-3)) == 1


def test_cache_covers_semantics():
    assert O._cache_covers(3, 1) is True
    assert O._cache_covers(3, 3) is True
    assert O._cache_covers(3, 4) is False        # request exceeds cache coverage → not covered
    assert O._cache_covers(3, None) is False     # 'everything' needs full-cap coverage
    assert O._cache_covers(O._MAX_DRILL, None) is True
    assert O._cache_covers(None, 2) is False     # legacy cache, no coverage metadata → not covered


# ── max_drill validation happens BEFORE any navigation (no empty-cache poisoning) ──
def test_validate_drill_accepts_valid_and_rejects_invalid():
    for good in (1, 2, 10, O._MAX_DRILL):
        assert O._validate_drill(good) == good
    assert O._validate_drill(None) == O._MAX_DRILL          # 'everything' → full-cap depth
    for bad in (0, -1, O._MAX_DRILL + 1, 9999, "abc", ""):
        with pytest.raises(ValueError):
            O._validate_drill(bad)


def test_track_orders_rejects_invalid_max_drill_without_navigation(monkeypatch):
    """max_drill=0/negative/out-of-range is rejected BEFORE any browser is touched —
    a bad depth must never stamp an under-drilled/empty cache for the day."""
    import src.browser.session as S

    def _never_called():
        raise AssertionError("browser must not be touched for an invalid max_drill")

    monkeypatch.setattr(S, "get_session", _never_called)
    for bad in (0, -1, 31, 999, "abc"):
        with pytest.raises(ValueError):
            asyncio.run(O.track_orders(only_active=True, max_drill=bad))


# ── under-covered cache → explicit CacheCoverageError, never an auto-refetch ──
def test_track_orders_undercovered_cache_raises_without_refetch(tmp_path, monkeypatch):
    """A cache drilled 1 order cannot serve max_drill=5: raise CacheCoverageError and do
    NOT auto-refetch (one-live-run/day preserved — the browser is never touched)."""
    from src.errors import CacheCoverageError
    import src.browser.session as S

    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)
    O._save_cache([OrderStatus(order_id="1", title="a", status="待取件")])   # drilled=1

    def _never_called():
        raise AssertionError("must NOT auto-refetch when the cache under-covers")

    monkeypatch.setattr(S, "get_session", _never_called)
    with pytest.raises(CacheCoverageError) as ei:
        asyncio.run(O.track_orders(only_active=True, max_drill=5))
    assert "force=True" in str(ei.value) and "max_drill" in str(ei.value)


def test_track_orders_undercovered_cache_force_allows_live(tmp_path, monkeypatch):
    """force=True explicitly authorizes an extra same-day live run (bypasses the coverage
    error and re-stamps the cache) — the live path is entered, not the coverage signal."""
    import src.browser.session as S

    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)
    O._save_cache([OrderStatus(order_id="1", title="a", status="待取件")])   # drilled=1

    class _LiveEntered(Exception):
        pass

    def _enter_live():
        raise _LiveEntered("live path entered (force=True)")

    monkeypatch.setattr(S, "get_session", _enter_live)
    with pytest.raises(_LiveEntered):
        asyncio.run(O.track_orders(only_active=True, max_drill=5, force=True))


def test_track_orders_enumeration_budget_fails_loud(tmp_path, monkeypatch):
    """2026-09-10 实机教训(桥上冒烟): 订单列表页 evaluate 无超时 → 运行楔死 8+ 分钟
    且零日志。枚举段现在整体有界(_ENUM_BUDGET_S, 测试收缩到 0.2s), 超时 fail loud
    为 SelectorDriftError, 缓存绝不落盘, browser 锁随工具返回而释放。"""
    import asyncio as aio

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod
    from src.errors import SelectorDriftError
    from src.extract import orders as O

    class _HangPage:
        url = ""

        async def goto(self, *a, **k):
            return None

        async def evaluate(self, js):
            await aio.sleep(999)  # 楔死现场复现: evaluate 永不返回

    class _HangSession:
        human_action_required = False

        async def start(self):
            return _HangPage()

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):  # pragma: no cover — 楔死时不应到达 logistics 阶段
            raise AssertionError("enumeration wedge must not reach the logistics stage")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: _HangSession())
    monkeypatch.setattr(pacing_mod, "human_scroll", _noop)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    monkeypatch.setattr(O, "_ENUM_BUDGET_S", 0.2)
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    with pytest.raises(SelectorDriftError, match="枚举段"):
        asyncio.run(O.track_orders(only_active=True, max_drill=5, force=True))
    assert not state.exists(), "wedge path must NOT stamp a cache"


def test_track_orders_plumbs_enumeration_titles_into_orders(tmp_path, monkeypatch):
    """2026-09-10 实机取证: 标题曾恒为空串(设计缺口) — ORDER_LIST_JS 现在返回
    [{id,title}], 演练段必须把列表页标题接到 OrderStatus.title 上。物流页用空帧
    模拟(不产状态), 断言点只在标题与订单号一一对应。"""
    from contextlib import asynccontextmanager

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    class _Page:
        url = ""

        async def goto(self, *a, **k):
            return None

        async def evaluate(self, js):
            return [{"id": "3301", "title": "USB3.0转Type-c转接头转换器"},
                    {"id": "3302", "title": "迷你烙铁小锡锅锡炉"},
                    "junk-non-dict"]   # 防御: 非 dict 条目必须被丢弃

    class _LP:
        url = ""
        frames: list = []

        async def goto(self, *a, **k):
            return None

        async def close(self):
            return None

    class _Ctx:
        async def new_page(self):
            return _LP()

    class _Scope:
        async def try_track(self, page):
            return True

    class _TempPages:
        @asynccontextmanager
        async def _cm(self):
            yield _Scope()

        def __call__(self):
            return self._cm()

    class _Session:
        human_action_required = False
        context = _Ctx()

        async def start(self):
            return _Page()

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):
            return _TempPages()()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: _Session())
    monkeypatch.setattr(session_mod, "track_temporary_page", lambda scope, page: page)
    monkeypatch.setattr(pacing_mod, "human_scroll", _noop)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    orders = asyncio.run(O.track_orders(only_active=False, max_drill=5, force=True))
    assert [o.order_id for o in orders] == ["3301", "3302"]
    assert [o.title for o in orders] == ["USB3.0转Type-c转接头转换器", "迷你烙铁小锡锅锡炉"]
    assert state.exists(), "正常完成的一轮必须落当日缓存"


def test_frame_text_budget_prevents_hung_frame_wedge(tmp_path, monkeypatch):
    """2026-09-10 第二次实机教训: fr.evaluate 无默认超时, 单帧 JS 卡死把整轮演练
    楔死 16+ 分钟。单帧取文本现在有 _FRAME_EVAL_BUDGET_S 上界 — 卡死帧按空文本
    跳过, 演练完成并正常落缓存(状态未知, 不再永久悬挂)。"""
    import asyncio as aio

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    class _HangFrame:
        url = "https://market.m.taobao.com/app/dinamic/pc-trade-logistics/home.html"

        async def evaluate(self, js):
            await aio.sleep(999)   # 卡死帧现场复现

    class _Page:
        url = ""

        async def goto(self, *a, **k):
            return None

        async def evaluate(self, js):
            return [{"id": "3401", "title": "弹簧钢定制"}, {"id": "3402", "title": ""}]

    class _LP:
        url = ""
        frames = [_HangFrame()]

        async def goto(self, *a, **k):
            return None

        async def close(self):
            return None

    class _Ctx:
        async def new_page(self):
            return _LP()

    class _Scope:
        async def try_track(self, page):
            return True

    class _TempPages:
        from contextlib import asynccontextmanager as _acm

        @_acm
        async def _cm(self):
            yield _Scope()

        def __call__(self):
            return self._cm()

    class _Session:
        human_action_required = False
        context = _Ctx()

        async def start(self):
            return _Page()

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):
            return _TempPages()()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: _Session())
    monkeypatch.setattr(session_mod, "track_temporary_page", lambda scope, page: page)
    monkeypatch.setattr(pacing_mod, "human_scroll", _noop)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    monkeypatch.setattr(O, "_FRAME_EVAL_BUDGET_S", 0.1)
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    orders = asyncio.run(O.track_orders(only_active=False, max_drill=5, force=True))
    assert [o.order_id for o in orders] == ["3401", "3402"]
    assert [o.status for o in orders] == ["未知", "未知"]   # 卡死帧→空文本, 不再楔死
    assert state.exists()


def test_drill_budget_stops_run_on_second_wedge(tmp_path, monkeypatch):
    """每单 _DRILL_BUDGET_S 兜底: goto 悬挂超时按楔子处理 — 至多重建一次物流页,
    第二次楔子停止演练, 保留已完成订单并落缓存(绝不无限悬挂)。"""
    import asyncio as aio

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    class _Page:
        url = ""

        async def goto(self, *a, **k):
            return None

        async def evaluate(self, js):
            return [{"id": "3501", "title": "A"}, {"id": "3502", "title": "B"}]

    class _HangingLP:
        url = ""
        frames: list = []

        async def goto(self, *a, **k):
            await aio.sleep(0.6)   # 超出收缩后的每单预算

        async def close(self):
            return None

    class _Ctx:
        new_page_calls = 0

        async def new_page(self):
            _Ctx.new_page_calls += 1
            return _HangingLP()

    class _Scope:
        async def try_track(self, page):
            return True

    class _TempPages:
        from contextlib import asynccontextmanager as _acm

        @_acm
        async def _cm(self):
            yield _Scope()

        def __call__(self):
            return self._cm()

    class _Session:
        human_action_required = False
        context = _Ctx()

        async def start(self):
            return _Page()

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):
            return _TempPages()()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: _Session())
    monkeypatch.setattr(session_mod, "track_temporary_page", lambda scope, page: page)
    monkeypatch.setattr(pacing_mod, "human_scroll", _noop)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    monkeypatch.setattr(O, "_DRILL_BUDGET_S", 0.2)
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    orders = asyncio.run(O.track_orders(only_active=False, max_drill=5, force=True))
    assert [o.order_id for o in orders] == ["3501"]   # 第二次楔子 → 停止, 保留第一单
    assert _Ctx.new_page_calls == 2                   # 初始 + 至多一次重建(绝不爆发)
    assert state.exists()


def test_drill_survives_tool_task_cancellation_and_stamps_cache(tmp_path, monkeypatch):
    """2026-09-10 第四次实机教训: 桥 downstream timeout 取消在途工具任务, 取消总在
    _save_cache 之前落地 → 缓存永不落盘。演练段现在在 shielded detach task 中运行:
    外层取消后演练继续跑完并落缓存(当日重调即可取回摘要)。"""
    import asyncio as aio

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    class _Page:
        url = ""

        async def goto(self, *a, **k):
            return None

        async def evaluate(self, js):
            return [{"id": f"360{i}", "title": f"t{i}"} for i in range(1, 4)]

    class _SlowLP:
        url = ""
        frames: list = []

        async def goto(self, *a, **k):
            await aio.sleep(0.5)   # 每单演练足够慢, 取消落在第 2 单中间

        async def close(self):
            return None

    class _Ctx:
        async def new_page(self):
            return _SlowLP()

    class _Scope:
        async def try_track(self, page):
            return True

    class _TempPages:
        from contextlib import asynccontextmanager as _acm

        @_acm
        async def _cm(self):
            yield _Scope()

        def __call__(self):
            return self._cm()

    class _Session:
        human_action_required = False
        context = _Ctx()

        async def start(self):
            return _Page()

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):
            return _TempPages()()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: _Session())
    monkeypatch.setattr(session_mod, "track_temporary_page", lambda scope, page: page)
    monkeypatch.setattr(pacing_mod, "human_scroll", _noop)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    async def _scenario():
        outer = aio.ensure_future(O.track_orders(only_active=False, max_drill=5, force=True))
        await aio.sleep(0.75)          # 第 1 单已完成, 第 2 单正在 goto 中
        outer.cancel()
        try:
            await outer
        except aio.CancelledError:
            pass
        # 取消后 detach task 继续跑: 轮询等待缓存落盘(最多 5s)
        for _ in range(50):
            if state.exists():
                break
            await aio.sleep(0.1)
        assert state.exists(), "cancelled tool task must still stamp the cache via the detached drill"
        data = json.loads(state.read_text(encoding="utf-8"))
        assert [o["order_id"] for o in data["orders"]] == ["3601", "3602", "3603"]

    asyncio.run(_scenario())


def test_track_orders_skips_logistics_drill_for_terminal_list_status(tmp_path, monkeypatch):
    """用户 2026-09-10 指示: 先查列表状态 — 交易关闭的订单自然没有物流, 不该再花
    一次物流页导航(也少加载一次含收件地址的页面, 隐私最小化)。终态单直接采信
    列表状态; 其余订单照常演练。"""
    from contextlib import asynccontextmanager

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    class _Page:
        url = ""

        async def goto(self, *a, **k):
            return None

        async def evaluate(self, js):
            return [
                {"id": "3701", "title": "弹簧钢定制", "status": "交易关闭"},
                {"id": "3702", "title": "转接头", "status": "买家已付款"},
            ]

    class _LP:
        url = ""
        frames: list = []
        navigated_to: list = []

        async def goto(self, *a, **k):
            _LP.navigated_to.append(k.get("url") or (a[0] if a else ""))

        async def close(self):
            return None

    class _Ctx:
        async def new_page(self):
            return _LP()

    class _Scope:
        async def try_track(self, page):
            return True

    class _TempPages:
        @asynccontextmanager
        async def _cm(self):
            yield _Scope()

        def __call__(self):
            return self._cm()

    class _Session:
        human_action_required = False
        context = _Ctx()

        async def start(self):
            return _Page()

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):
            return _TempPages()()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: _Session())
    monkeypatch.setattr(session_mod, "track_temporary_page", lambda scope, page: page)
    monkeypatch.setattr(pacing_mod, "human_scroll", _noop)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    state = tmp_path / ".track_state.json"
    monkeypatch.setattr(O, "_state_file", lambda: state)

    orders = asyncio.run(O.track_orders(only_active=False, max_drill=5, force=True))
    # 交易关闭单: 状态采信列表页, 物流页从未被导航
    assert orders[0].status == "交易关闭"
    assert not any("3701" in u for u in _LP.navigated_to), "closed order must not be drilled"
    # 活跃单照常演练(空帧→未知)
    assert len(_LP.navigated_to) == 1 and "3702" in _LP.navigated_to[0]
    assert orders[1].status == "未知"
    # 缓存仍包含全部订单(重过滤语义不变)
    data = json.loads(state.read_text(encoding="utf-8"))
    assert [o["order_id"] for o in data["orders"]] == ["3701", "3702"]


def test_filter_orders_drops_transaction_closed_from_active():
    """交易关闭是终态: 活跃摘要(转发代购)里不该出现无物流的关闭单。"""
    orders = [
        OrderStatus(order_id="1", title="a", status="交易关闭"),
        OrderStatus(order_id="2", title="b", status="已发货"),
    ]
    got = O._filter_orders(orders, only_active=True, max_drill=5)
    assert [o.order_id for o in got] == ["2"]
