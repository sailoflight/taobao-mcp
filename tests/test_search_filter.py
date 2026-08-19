"""Tests for filter_search_results — client-side min/max sales & price bands (打磨轮次 5)."""

from __future__ import annotations

from src.extract.search import filter_search_results
from src.models import SearchResult


def _r(pid, price=None, sales=None):
    return SearchResult(product_id=pid, url=f"https://item.taobao.com/item.htm?id={pid}",
                        title=f"商品{pid}", price=price, monthly_sales=sales, shop_name=None, location=None)


def test_no_filters_returns_all():
    rs = [_r("1", 30, 100), _r("2", 50, 200)]
    assert filter_search_results(rs, None) == rs
    assert filter_search_results(rs, {}) == rs


def test_min_sales_skips_low():
    rs = [_r("1", 30, 5), _r("2", 50, 100), _r("3", 20, 500)]
    out = filter_search_results(rs, {"min_sales": 100})
    assert [r.product_id for r in out] == ["2", "3"]


def test_max_sales_skips_high():
    rs = [_r("1", 30, 5), _r("2", 50, 100), _r("3", 20, 500)]
    out = filter_search_results(rs, {"max_sales": 100})
    assert [r.product_id for r in out] == ["1", "2"]


def test_price_band_client_side():
    rs = [_r("1", 30, 5), _r("2", 50, 100), _r("3", 20, 500)]
    out = filter_search_results(rs, {"min_price": 30, "max_price": 50})
    assert [r.product_id for r in out] == ["1", "2"]


def test_combined_sales_and_price():
    rs = [_r("1", 30, 5), _r("2", 50, 100), _r("3", 20, 500), _r("4", 90, 300)]
    out = filter_search_results(rs, {"min_sales": 100, "max_sales": 400, "min_price": 40})
    assert [r.product_id for r in out] == ["2", "4"]


def test_none_fields_pass_through():
    # missing price/sales are not filtered out by that band
    rs = [_r("1", None, None), _r("2", 50, None)]
    out = filter_search_results(rs, {"min_sales": 10, "min_price": 40})
    assert {r.product_id for r in out} == {"1", "2"}


def test_title_contains_filters():
    rs = [_r("1", 30, 100), _r("2", 50, 200)]
    rs[0].title = "加厚收纳箱"
    rs[1].title = "透明收纳箱"
    out = filter_search_results(rs, {"title_contains": "加厚"})
    assert [r.product_id for r in out] == ["1"]
    out = filter_search_results(rs, {"title_contains": "收纳"})
    assert [r.product_id for r in out] == ["1", "2"]


def test_title_contains_combined_with_sales():
    rs = [_r("1", 30, 5), _r("2", 50, 100)]
    rs[0].title = "加厚收纳箱"
    rs[1].title = "加厚透明箱"
    out = filter_search_results(rs, {"title_contains": "加厚", "min_sales": 10})
    assert [r.product_id for r in out] == ["2"]


def test_client_side_sort_reliable():
    from src.extract.search import filter_search_results
    rs = [SR("a", 30, 50), SR("b", 10, 500), SR("c", None, 999), SR("d", 20, 100)]
    assert [r.price for r in filter_search_results(rs, {"sort": 5})] == [10, 20, 30, None]
    assert [r.price for r in filter_search_results(rs, {"sort": 6})] == [30, 20, 10, None]
    assert [r.monthly_sales for r in filter_search_results(rs, {"sort": 2})] == [999, 500, 100, 50]
    assert [r.price for r in filter_search_results(rs, {"sort": 5, "min_price": 15})] == [20, 30, None]
    assert [r.price for r in filter_search_results(rs, {"min_price": 5})] == [30, 10, None, 20]
