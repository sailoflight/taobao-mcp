"""Phase 2b tests for the search-results card parser (pure text → SearchResult)."""

from __future__ import annotations

from src.extract.search import parse_card_text, parse_cards

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
