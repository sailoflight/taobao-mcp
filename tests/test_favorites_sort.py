"""Tests for the favorites price-sort — 打磨轮次 29."""

from __future__ import annotations

from src.extract.favorite import _price_of, _sort_favorites


def _items():
    return [
        {"price": "23.4", "title": "护目镜"},
        {"price": "1359.15", "title": "拓竹A1"},
        {"price": None, "title": "无价"},
        {"price": "3.96", "title": "电池"},
    ]


def test_price_of_parses_and_handles_bad():
    assert _price_of({"price": "23.4"}) == 23.4
    assert _price_of({"price": None}) is None
    assert _price_of({"price": "abc"}) is None


def test_sort_favorites_asc_desc_and_order():
    assert [i["title"] for i in _sort_favorites(_items(), "price_asc")] == ["电池", "护目镜", "拓竹A1", "无价"]
    assert [i["title"] for i in _sort_favorites(_items(), "price_desc")] == ["拓竹A1", "护目镜", "电池", "无价"]
    assert [i["title"] for i in _sort_favorites(_items(), "")] == ["护目镜", "拓竹A1", "无价", "电池"]
