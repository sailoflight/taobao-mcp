"""Tests for order-tracking pure parsers (logistics text → fields; digest). No live data."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.extract import orders as O
from src.extract.orders import order_digest, parse_logistics, parse_order_title
from src.models import OrderStatus

# A parcel sitting at a pickup station with an ACTIVE 取件码:
PICKUP_TEXT = "已揽收 运输中 中通快递 78912345678901 复制 文化路菜鸟驿站，请凭取货码 1-2-3456 取件"
# A delivered parcel (code already used → none shown):
DONE_TEXT = "已签收 武汉市 中通快递 79007243724230 交诚B区店店菜鸟驿站，感谢使用菜鸟驿站"


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


def test_parse_order_title_strips_status_and_orderno():
    t = parse_order_title("交易成功 特斯拉P100 16G显卡 订单号: 1234567890123456789 中通")
    assert "P100" in t and "订单号" not in t


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
