"""Focused tests for src/extract/account.py — pure query.bag body parsing (no browser).

read_cart() 的解析已抽为纯函数 parse_cart_bag(), 这里离线验证:
  • product_id 从 itemId 填充(cart_atomic (product_id, sku_id) 快照键)
  • sku_id(字符串/字典两种形态)/quantity
  • (itemId, sku_id) 去重
  • 非纯 JSON 包裹体的兜底恢复
"""

from __future__ import annotations

import json

from src.extract.account import parse_cart_bag
from src.models import CartItem


def _bag_body(**over):
    data = {
        "data": {
            "bag": {
                "shopGroup": [
                    {
                        "shopTitle": "好管家旗舰店",
                        "items": [
                            {"itemId": "1001", "title": "乌檀木菜板",
                             "sku": '{"skuId":"5001"}', "quantity": 2},
                            {"itemId": "1001", "title": "乌檀木菜板",
                             "sku": {"skuId": "5002"}, "quantity": 1},
                            {"itemId": "2001", "title": "无 sku 商品", "quantity": 1},
                        ],
                    }
                ]
            }
        }
    }
    if over:
        data.update(over)
    return json.dumps(data, ensure_ascii=False)


def test_parse_cart_bag_populates_product_id():
    """itemId → CartItem.product_id; sku_id/quantity 正确; shopTitle 下传."""
    items = parse_cart_bag(_bag_body())
    by_key = {(it.product_id, it.sku_id): it for it in items}

    a = by_key[("1001", "5001")]
    assert a.seller == "好管家旗舰店" and a.product_id == "1001" and a.sku_id == "5001"
    assert a.quantity == 2 and a.title == "乌檀木菜板"

    b = by_key[("1001", "5002")]          # sku 是 dict 形态
    assert b.sku_id == "5002" and b.quantity == 1

    no_sku = by_key[("2001", None)]       # 无 sku 行: product_id 仍有, sku_id=None
    assert no_sku.product_id == "2001" and no_sku.sku_id is None and no_sku.quantity == 1


def test_parse_cart_bag_dedupes_by_item_and_sku():
    """同一 (itemId, sku_id) 重复出现只保留一条."""
    body = json.dumps({
        "data": {"items": [
            {"itemId": "1001", "title": "菜板", "sku": {"skuId": "5001"}, "quantity": 2},
            {"itemId": "1001", "title": "菜板", "sku": {"skuId": "5001"}, "quantity": 5},
        ]}
    }, ensure_ascii=False)
    items = parse_cart_bag(body)
    assert len(items) == 1 and items[0].sku_id == "5001" and items[0].quantity == 2


def test_parse_cart_bag_recovers_wrapped_json():
    """非纯 JSON 包裹体(如 callback(...) / 前缀)兜底恢复."""
    inner = json.dumps({"data": {"items": [
        {"itemId": "1001", "title": "菜板", "sku": {"skuId": "5001"}, "quantity": 1},
    ]}}, ensure_ascii=False)
    items = parse_cart_bag("cb(" + inner + ")")
    assert len(items) == 1 and items[0].product_id == "1001" and items[0].sku_id == "5001"


def test_parse_cart_bag_garbage_returns_empty():
    """无法解析的 body → 空列表(不抛)."""
    assert parse_cart_bag("not json at all {{{") == []


def test_cart_item_product_id_backward_compatible():
    """CartItem.product_id 可选, 默认 None(旧构造不破坏)."""
    c = CartItem(seller="好管家", title="乌檀木菜板", sku_id="s1", quantity=1)
    assert c.product_id is None
    d = c.model_dump()
    assert "product_id" in d and d["product_id"] is None


def test_query_bag_url_match_is_exact():
    from src.extract.account import _is_query_bag_url

    assert _is_query_bag_url("https://h5api.m.taobao.com/h5/mtop.trade.query.bag/5.0/")
    assert _is_query_bag_url("https://api.taobao.com/x?api=mtop.trade.query.bag&v=5.0")
    assert not _is_query_bag_url("https://api.taobao.com/x?api=mtop.trade.query.bag.extra")
    assert not _is_query_bag_url("https://cart.taobao.com/cart.htm")
    assert not _is_query_bag_url("https://api.taobao.com/x?api=mtop.trade.addBag")


def test_snapshot_accepts_valid_empty_but_rejects_garbage():
    from src.extract.account import _parse_cart_bag_snapshot

    empty, reason = _parse_cart_bag_snapshot('{"data":{"bag":{"items":[]}}}')
    assert empty == [] and reason is None
    invalid, reason = _parse_cart_bag_snapshot("not-json")
    assert invalid is None and reason
    invalid, reason = _parse_cart_bag_snapshot("{}")
    assert invalid is None and reason


def test_snapshot_ignores_quantity_placeholders_and_keeps_skuless_line():
    from src.extract.account import _parse_cart_bag_snapshot

    body = json.dumps({"data": {"items": [
        {"itemId": "100", "title": "valid", "quantity": 2},
        {"itemId": "101", "title": "null", "quantity": None},
        {"itemId": "102", "title": "zero", "quantity": 0},
        {"itemId": "103", "title": "bad", "quantity": "2.5"},
    ]}})
    items, reason = _parse_cart_bag_snapshot(body)
    assert reason is None
    assert [(item.product_id, item.sku_id, item.quantity) for item in items] == [("100", None, 2)]


def test_snapshot_unknown_product_id_is_invalid():
    from src.extract.account import _parse_cart_bag_snapshot

    body = json.dumps({"data": {"items": [
        {"title": "unknown product", "sku": {"skuId": "s1"}, "quantity": 1},
    ]}})
    items, reason = _parse_cart_bag_snapshot(body)
    assert items is None
    assert "missing itemId" in reason


def test_latest_valid_response_wins_without_merging():
    from src.extract.account import _latest_valid_snapshot

    first = json.dumps({"data": {"items": [
        {"itemId": "100", "title": "first", "sku": {"skuId": "s1"}, "quantity": 1},
    ]}})
    latest = json.dumps({"data": {"items": [
        {"itemId": "200", "title": "latest", "sku": {"skuId": "s2"}, "quantity": 3},
    ]}})
    items, reason = _latest_valid_snapshot([first, "garbage", latest], 200)
    assert reason is None
    assert [(item.product_id, item.sku_id, item.quantity) for item in items] == [("200", "s2", 3)]


class _FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    async def text(self):
        return self._body


class _FakeCartPage:
    def __init__(self, responses=()):
        self._responses = list(responses)
        self._response_handler = None
        self.removed = False
        self.goto_calls = 0
        self.mouse = self

    def on(self, event, handler):
        assert event == "response"
        self._response_handler = handler

    def remove_listener(self, event, handler):
        assert event == "response" and handler is self._response_handler
        self.removed = True

    async def goto(self, url, **kwargs):
        self.goto_calls += 1
        responses, self._responses = self._responses, []
        for response in responses:
            await self._response_handler(response)

    async def wheel(self, dx, dy):
        return None


class _FakeCartSession:
    def __init__(self, page, captcha_error=None):
        self.page = page
        self.captcha_error = captcha_error

    async def start(self):
        return self.page

    async def guard_captcha(self, page):
        if self.captcha_error:
            raise self.captcha_error


def _patch_read_cart(monkeypatch, session):
    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    async def no_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(session_mod, "get_session", lambda: session)
    monkeypatch.setattr(pacing_mod, "human_delay", no_delay)


def test_read_cart_strict_accepts_valid_empty_response(monkeypatch):
    import asyncio
    from src.extract.account import read_cart

    response = _FakeResponse(
        "https://h5api.m.taobao.com/h5/mtop.trade.query.bag/5.0/",
        '{"data":{"bag":{"items":[]}}}',
    )
    page = _FakeCartPage([response])
    _patch_read_cart(monkeypatch, _FakeCartSession(page))
    assert asyncio.run(read_cart(require_snapshot=True)) == []
    assert page.removed and page.goto_calls == 1


def test_read_cart_strict_rejects_missing_response(monkeypatch):
    import asyncio
    import pytest
    from src.errors import CartSnapshotError
    from src.extract.account import read_cart

    page = _FakeCartPage()
    _patch_read_cart(monkeypatch, _FakeCartSession(page))
    with pytest.raises(CartSnapshotError):
        asyncio.run(read_cart(require_snapshot=True))
    assert page.removed and page.goto_calls == 2


def test_read_cart_removes_listener_when_captcha_raises(monkeypatch):
    import asyncio
    import pytest
    from src.errors import CaptchaError
    from src.extract.account import read_cart

    page = _FakeCartPage()
    _patch_read_cart(monkeypatch, _FakeCartSession(page, CaptchaError()))
    with pytest.raises(CaptchaError):
        asyncio.run(read_cart(require_snapshot=True))
    assert page.removed


def test_snapshot_rejects_unsuccessful_mtop_envelope():
    from src.extract.account import _parse_cart_bag_snapshot

    body = json.dumps({"api": "mtop.trade.query.bag", "ret": ["FAIL_SYS_TOKEN_EXOIRED"], "data": {}})
    items, reason = _parse_cart_bag_snapshot(body)
    assert items is None and "did not report success" in reason
