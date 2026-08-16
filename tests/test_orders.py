"""Tests for order-tracking pure parsers (logistics text → fields; digest). No live data."""

from __future__ import annotations

import json

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
