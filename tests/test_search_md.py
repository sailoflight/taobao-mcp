"""Tests for the search markdown renderer (一屏挑商品) — 打磨轮次 20."""

from __future__ import annotations

from src.extract.search import _search_markdown
from src.models import SearchResult


def _results():
    return [
        SearchResult(product_id="1", url="https://item.taobao.com/item.htm?id=1", title="密封收纳箱 特大号",
                     price=28.0, monthly_sales=1000, shop_name="天鼠家居旗舰店", location="江苏"),
        SearchResult(product_id="2", url="https://item.taobao.com/item.htm?id=2", title="六卡扣收纳箱",
                     price=None, monthly_sales=None, shop_name="B店", location="浙江"),
    ]


def test_search_markdown_renders_rows():
    md = _search_markdown(_results(), keyword="密封收纳箱", max_rows=2)
    assert "### 搜索结果(2 个) — 密封收纳箱" in md
    assert "| 28 | 1000 | 天鼠家居旗舰店 | 江苏 | 密封收纳箱 特大号 |" in md
    assert "| - | - | B店 | 浙江 | 六卡扣收纳箱 |" in md


def test_search_markdown_caps_rows():
    md = _search_markdown(_results(), keyword="", max_rows=1)
    assert "### 搜索结果(1 个)" in md
    assert len(md.splitlines()) == 5
