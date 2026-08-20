"""Phase 2b tests for the search-results card parser (pure text → SearchResult)."""

from __future__ import annotations

import pytest

from src.extract.search import _verify_page_reached, parse_card_text, parse_cards
from src.errors import SelectorDriftError

# Real flattened card text captured from s.taobao.com (public listing data).
CARD_1 = "特斯拉P100 16G显卡Tesla 深度学习Ai部署【DeepSeek推荐用卡】 ¥ 397 补贴后 700+人付款 河南 郑州 3期 48小时内发 包邮 南京海雀显卡"
CARD_2 = "Tesla P100 V100 SXM2/PCIE 16G/32G显卡 深度学习人工智能NVLink FP32 Tesla/特斯拉 ¥ 476 补贴后 400+人付款 上海 3期 48小时内发 包邮 瑞兴科技服务器"


def test_parse_card_basic():
    r = parse_card_text("736546459871", CARD_1)
    assert r.product_id == "736546459871"
    assert r.title.startswith("特斯拉P100 16G")
    assert "¥" not in r.title
    assert r.price == 397.0
    assert r.monthly_sales == 700
    assert r.location == "河南郑州"
    assert r.shop_name == "南京海雀显卡"
    assert r.url.endswith("id=736546459871")


def test_parse_card_second():
    r = parse_card_text("856019072830", CARD_2)
    assert r.price == 476.0
    assert r.monthly_sales == 400
    assert r.location == "上海"
    assert r.shop_name == "瑞兴科技服务器"


def test_sales_wan_suffix():
    r = parse_card_text("1", "某显卡 ¥ 99 1.2万人付款 广东 广州 包邮 示例店铺")
    assert r.monthly_sales == 12000
    assert r.price == 99.0
    assert r.shop_name == "示例店铺"


def test_parse_cards_list():
    results = parse_cards([
        {"id": "736546459871", "text": CARD_1},
        {"id": "856019072830", "text": CARD_2},
        {"text": "no id — skipped"},
    ])
    assert [r.product_id for r in results] == ["736546459871", "856019072830"]
    assert all(r.price for r in results)


def test_price_with_thousands_comma():
    # C3: ¥1,299 must not parse as 1.0
    r = parse_card_text("1", "高端运算卡 ¥ 1,299 补贴后 50人付款 广东 深圳 包邮 某服务器店")
    assert r.price == 1299.0
    assert r.monthly_sales == 50
    assert r.location == "广东深圳"


def test_sales_yishou_form():
    # M7: 已售2000+ phrasing
    r = parse_card_text("1", "某显卡 ¥ 500 已售2000+ 上海 包邮 蓝天服务器")
    assert r.monthly_sales == 2000
    assert r.price == 500.0


def test_title_not_truncated_by_promo_yen():
    # M5: a promo ¥ before the price must not eat the product name
    r = parse_card_text("1", "直降¥100 特斯拉P100 16G显卡 ¥ 397 补贴后 700+人付款 河南 包邮 海雀显卡")
    assert "P100" in r.title
    assert r.price == 397.0


def test_price_skips_struck_through_youhuiqian():
    # CRITICAL: real sell price is 397; the "优惠前 ¥420" is the crossed-out price
    r = parse_card_text("1", "特斯拉P100 16G显卡 ¥ 397 补贴后 优惠前 ¥ 420 700+人付款 河南 包邮 南京海雀显卡")
    assert r.price == 397.0
    assert "P100" in r.title and "优惠前" not in r.title


def test_location_for_yishou_card():
    # LOW: 已售-form cards must still get a location
    r = parse_card_text("1", "显卡 ¥ 500 已售2000+ 上海 包邮 蓝天服务器")
    assert r.location == "上海"


def test_spec_text_extracted():
    """Card 规格/尺寸片段被提取(spec_contains 过滤的原料) — 2026-08-19."""
    r = parse_card_text("1", "真空袋 规格：特大号30*34厘米 加厚 ¥ 11.4 1000人付款 江苏 索晨旗舰店")
    assert r.spec_text and "30*34" in r.spec_text


def test_spec_text_none_when_absent():
    r = parse_card_text("2", "普通收纳箱 ¥ 15.9 200人付款 浙江 某店")
    assert r.spec_text is None


def test_spec_contains_filter():
    from src.extract.search import filter_search_results
    from src.models import SearchResult

    def _r(pid, spec=None):
        return SearchResult(product_id=pid,
                            url=f"https://item.taobao.com/item.htm?id={pid}",
                            title=f"商品{pid}", price=1.0, monthly_sales=10,
                            shop_name=None, location=None, spec_text=spec)

    rs = [_r("1", "特大号30*34厘米"), _r("2", "加大号32*45厘米"), _r("3", None)]
    out = filter_search_results(rs, {"spec_contains": "30*34"})
    assert [r.product_id for r in out] == ["1"]
    # 无规格卡片不过滤(保持现状语义)
    out2 = filter_search_results(rs, {"spec_contains": "32*45"})
    assert [r.product_id for r in out2] == ["2"]


# ---- pagination must fail loudly when the requested page was not reached (audit MED-6) ----

def test_page_not_reached_raises():
    """Requested page 3 but the browser is still on page 1 → SelectorDriftError, not silent wrong-page rows."""
    with pytest.raises(SelectorDriftError):
        _verify_page_reached(3, "https://s.taobao.com/search?q=petg&tab=all&page=1")
    # SPA rewrote a requested page=2 back to page=1
    with pytest.raises(SelectorDriftError):
        _verify_page_reached(2, "https://s.taobao.com/search?q=petg&tab=all&page=1")
    # no URL at all
    with pytest.raises(SelectorDriftError):
        _verify_page_reached(2, "")


def test_page_reached_ok():
    """page_num == 1 (no pagination) or the URL really carries page=N → no raise."""
    _verify_page_reached(1, "https://s.taobao.com/search?q=petg&tab=all&page=1")
    _verify_page_reached(1, "https://s.taobao.com/search?q=petg")
    _verify_page_reached(2, "https://s.taobao.com/search?q=petg&tab=all&page=2")
    _verify_page_reached(3, "https://s.taobao.com/search?q=petg&tab=all&page=3&bcoffset=5")


def test_page_reached_handles_encoded_comma():
    """The SPA encodes commas as %2C; that form must also count as reached."""
    _verify_page_reached(3, "https://s.taobao.com/search?q=petg&tab=all&page=3&bcoffset=%2C1")
