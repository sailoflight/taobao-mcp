"""Tests for the search URL builder (pure) — filters wiring (打磨轮次 2/4)."""

from __future__ import annotations

from src.extract.search import build_search_url


def test_base_url():
    u = build_search_url("密封收纳箱 特大号", 1)
    assert u.startswith("https://s.taobao.com/search?q=")
    assert "tab=all" in u
    assert "page=1" in u


def test_keyword_quoted():
    u = build_search_url("tesla p100 16g", 2)
    assert "q=tesla%20p100%2016g" in u
    assert "page=2" in u


def test_price_band_filter():
    u = build_search_url("收纳箱", 1, {"min_price": 30, "max_price": 80})
    assert "filter=reserve_price[30,80]" in u


def test_sort_filter():
    u = build_search_url("收纳箱", 1, {"sort": 5})
    assert "&s=5" in u


def test_price_and_sort_combined():
    u = build_search_url("收纳箱", 1, {"min_price": 30, "max_price": 80, "sort": 5})
    assert "filter=reserve_price[30,80]" in u
    assert "&s=5" in u
    assert u.rfind("filter") < u.rfind("&s")  # sort appended last


def test_no_filters_no_extra_params():
    u = build_search_url("收纳箱", 1, None)
    assert "filter=" not in u
    assert "&s=" not in u
