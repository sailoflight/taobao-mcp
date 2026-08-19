"""Tests for the compare rows: _summarize (Product → row) and _to_markdown (row → table)."""

from __future__ import annotations

from types import SimpleNamespace

from src.extract.compare import _summarize, _to_markdown


def _product(**kw):
    base = dict(
        product_id="862892097837",
        title="天鼠收纳箱特厚超大家用密封塑料衣服棉被玩具杂物特硬储物箱",
        shop_name="天鼠家居旗舰店",
        price_range=(36.0, 274.75),
        variants=[SimpleNamespace(properties={}, price=36.0, stock=1, available=True),
                  SimpleNamespace(properties={}, price=42.25, stock=1, available=True),
                  SimpleNamespace(properties={}, price=54.75, stock=1, available=True)],
        reviews=[1, 2],
        subsidy_caveat=None,
        url="https://item.taobao.com/item.htm?id=862892097837",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_summarize_folds_product():
    r = _summarize(_product())
    assert r["product_id"] == "862892097837"
    assert r["variant_count"] == 3
    assert r["cheapest"] == 36.0
    assert r["price_sample"] == [36.0, 42.25, 54.75]
    assert r["review_count"] == 2
    assert r["shop"] == "天鼠家居旗舰店"


def test_summarize_error_dict_passes_through():
    r = _summarize({"product_id": "123", "error": "boom"})
    assert r["error"] == "boom"


def test_summarize_dedupes_prices():
    r = _summarize(_product(variants=[SimpleNamespace(properties={}, price=36.0, stock=1, available=True),
                                      SimpleNamespace(properties={}, price=36.0, stock=1, available=True)]))
    assert r["price_sample"] == [36.0]


def test_to_markdown_renders_rows():
    rows = [
        _summarize(_product()),
        {"product_id": "999", "error": "超时"},
    ]
    md = _to_markdown(rows, 2)
    assert "### 短名单对比(2 件)" in md
    assert "天鼠" in md
    assert "价格示例" in md  # column header
    assert "36.0" in md
    assert "999" in md  # error row still shown


def test_summarize_includes_review_total():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range=(1.0, 2.0),
        variants=[SimpleNamespace(properties={"c": "a"}, price=1.0, stock=1, available=True)],
        reviews=[], subsidy_caveat=None, url="https://item.taobao.com/item.htm?id=1",
        review_total="1000+", favorable_rate="98%",
    )
    row = _summarize(p)
    assert row["review_total"] == "1000+" and row["favorable_rate"] == "98%"
    assert "1000+(98%)" in _to_markdown([row], 1)


def test_cheapest_available_excludes_oos():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range="(36.0, 54.75)",
        variants=[
            SimpleNamespace(properties={"颜色": "白"}, price=36.0, stock=0, available=False),
            SimpleNamespace(properties={"颜色": "白"}, price=42.25, stock=1, available=True),
        ],
        reviews=[], subsidy_caveat=None, url="u", review_total="1000+", favorable_rate=None,
    )
    row = _summarize(p)
    assert row["cheapest"] == 36.0        # 含缺货
    assert row["cheapest_available"] == 42.25
    md = _to_markdown([row], 1)
    assert "· 有货42.25" in md


def test_cheapest_available_no_note_when_same():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range="(36.0, 54.75)",
        variants=[SimpleNamespace(properties={"颜色": "白"}, price=36.0, stock=1, available=True)],
        reviews=[], subsidy_caveat=None, url="u", review_total=None, favorable_rate=None,
    )
    row = _summarize(p)
    assert row["cheapest_available"] == 36.0
    assert "· 有货" not in _to_markdown([row], 1)


def test_cheapest_unit_in_compare():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range="(36.0, 112.25)",
        variants=[
            SimpleNamespace(properties={"规格": "1个装"}, price=42.25, stock=1, available=True),
            SimpleNamespace(properties={"规格": "2个装"}, price=79.75, stock=1, available=True),
            SimpleNamespace(properties={"规格": "3个装"}, price=112.25, stock=1, available=True),
        ],
        reviews=[], subsidy_caveat=None, url="u", review_total=None, favorable_rate=None,
    )
    row = _summarize(p)
    assert abs(row["cheapest_unit"] - 112.25 / 3) < 0.001
    assert "最低单价¥37.42" in _to_markdown([row], 1)


def test_sort_rows_by_price_and_unit():
    from src.extract.compare import _sort_rows
    rows = [
        {"product_id": "e", "error": "x"},
        {"product_id": "a", "cheapest_available": 42.25, "cheapest_unit": 37.42},
        {"product_id": "b", "cheapest_available": 15.9, "cheapest_unit": 15.9},
    ]
    assert [r["product_id"] for r in _sort_rows(rows, "price")] == ["b", "a", "e"]
    assert [r["product_id"] for r in _sort_rows(rows, "unit")] == ["b", "a", "e"]
    assert [r["product_id"] for r in _sort_rows(rows, "")] == ["e", "a", "b"]


def test_sort_rows_missing_key_last():
    from src.extract.compare import _sort_rows
    rows = [{"product_id": "x", "cheapest_unit": None}, {"product_id": "y", "cheapest_unit": 10.0}]
    assert [r["product_id"] for r in _sort_rows(rows, "unit")] == ["y", "x"]


def test_append_variants_markdown():
    from src.extract.compare import _append_variants_markdown
    rows = [
        {"product_id": "1", "title": "天鼠收纳箱", "variants_summary": [
            {"label": "规格:1个装", "price": 36.0, "stock": 200, "available": True},
            {"label": "规格:2个装", "price": 67.25, "stock": 0, "available": False},
        ]},
        {"product_id": "2", "title": "无型号", "variants_summary": []},
    ]
    md = _append_variants_markdown("### 总览\n", rows)
    assert "## 各商品型号明细" in md
    assert "### 天鼠收纳箱" in md
    assert "| 规格:1个装 | 36 | 200 | ✓ |" in md
    assert "| 规格:2个装 | 67.25 | 0 | ✗ |" in md
    assert "无型号数据" in md


def test_review_total_num_parses():
    from src.extract.compare import _review_total_num
    assert _review_total_num("1000+") == 1000
    assert _review_total_num("5万+") == 50000
    assert _review_total_num("860") == 860
    assert _review_total_num("3千+") == 3000
    assert _review_total_num(None) is None
    assert _review_total_num("abc") is None


def test_to_markdown_best_unit_recommendation():
    from src.extract.compare import _to_markdown
    rows = [
        {"product_id": "a", "title": "天鼠收纳箱", "cheapest_unit": 30.33, "cheapest_available": 36.0,
         "cheapest": 36.0, "shop": "s", "price_range": (36.0, 54.75), "variant_count": 3,
         "price_sample": [36.0], "review_total": "1000+", "subsidy_caveat": None, "favorable_rate": None},
        {"product_id": "b", "title": "Purable", "cheapest_unit": None, "cheapest_available": 18.9,
         "cheapest": 18.9, "shop": "s2", "price_range": (18.9, 175.9), "variant_count": 12,
         "price_sample": [18.9], "review_total": "500+", "subsidy_caveat": None, "favorable_rate": None},
        {"product_id": "e", "error": "x"},
    ]
    md = _to_markdown(rows, 3)
    assert "💰 最低单价推荐: 天鼠收纳箱 (每件¥30.33)" in md
    md2 = _to_markdown([{"product_id": "a", "title": "无单价", "cheapest_unit": None}], 1)
    assert "最低单价推荐" not in md2


def test_match_cart_price_by_variant_text():
    """购物车到手价按型号文本匹配到变体(cart 模式核心) — 2026-08-19."""
    from src.extract.compare import _match_cart_price
    from src.models import SkuVariant

    variants = [
        SkuVariant(sku_id="a", properties={"颜色分类": "10个袋子30*34cm+2夹子"},
                   price=13.5, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"颜色分类": "便携式抽真空机一台"},
                   price=15.9, stock=1, available=True),
    ]
    cart = [
        {"product_id": "1039147294809", "variant": "10个袋子30*34cm+2夹子",
         "after_price": None, "platform_after": "11.4"},
        {"product_id": "1039147294809", "variant": "便携式抽真空机一台",
         "after_price": None, "platform_after": "13.43"},
        {"product_id": "999", "variant": "别的", "after_price": "1", "platform_after": None},
    ]
    out = _match_cart_price("1039147294809", variants, cart)
    assert out["颜色分类:10个袋子30*34cm+2夹子"]["cart_price"] == 11.4
    assert out["颜色分类:便携式抽真空机一台"]["cart_price"] == 13.43
    # 只匹配同一 product_id 的行
    assert len(out) == 2


def test_summarize_cart_override_basis():
    """cart 覆盖后行级标 price_basis + cheapest 用手价口径 — 2026-08-19."""
    from src.extract.compare import _match_cart_price, _summarize
    from src.models import Product, SkuVariant

    variants = [
        SkuVariant(sku_id="a", properties={"颜色分类": "10个袋子30*34cm+2夹子"},
                   price=13.5, stock=1, available=True),
    ]
    p = Product(product_id="1039147294809", url="u", title="便携式电动手持真空封口抽气泵",
                shop_name="華人包装", price_range=(13.5, 13.5), variants=variants,
                reviews=[], scraped_at="x")
    cart = [{"product_id": "1039147294809", "variant": "10个袋子30*34cm+2夹子",
             "after_price": None, "platform_after": "11.4"}]
    co = _match_cart_price("1039147294809", variants, cart)
    row = _summarize(p, cart_overrides=co)
    assert row["price_basis"] == "cart"
    assert row["cheapest_available"] == 11.4  # 用购物车到手价, 不是粗查原价 13.5
    assert row["cart_overrides"] and row["cart_overrides"][0]["cart_price"] == 11.4


def test_summarize_no_cart_falls_back_coarse():
    """购物车无该商品 → 退回粗查原价, price_basis='coarse' — 2026-08-19."""
    from src.extract.compare import _match_cart_price, _summarize
    from src.models import Product, SkuVariant

    variants = [SkuVariant(sku_id="a", properties={"颜色分类": "特大号30*34"},
                           price=12.5, stock=1, available=True)]
    p = Product(product_id="111", url="u", title="t", shop_name="s",
                price_range=(12.5, 12.5), variants=variants, reviews=[], scraped_at="x")
    co = _match_cart_price("111", variants, [])  # 购物车空
    row = _summarize(p, cart_overrides=co)
    assert row["price_basis"] == "coarse"
    assert row["cheapest_available"] == 12.5
    assert not row["cart_overrides"]


def test_skus_restrict_variants():
    """skus 严格指定型号 → 只保留该型号(cart 优先, 无则粗查价) — 2026-08-19."""
    from src.extract.compare import _match_cart_price, _summarize
    from src.models import Product, SkuVariant

    variants = [
        SkuVariant(sku_id="a", properties={"颜色分类": "10个袋子30*34cm+2夹子"},
                   price=13.5, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"颜色分类": "10个袋子26*34cm+2夹子"},
                   price=11.8, stock=1, available=True),
    ]
    p = Product(product_id="1039147294809", url="u", title="t", shop_name="華人包装",
                price_range=(11.8, 13.5), variants=variants, reviews=[], scraped_at="x")
    p.variants = [v for v in variants if "30*34" in "; ".join(v.properties.values())]
    cart = [{"product_id": "1039147294809", "variant": "10个袋子30*34cm+2夹子",
             "after_price": None, "platform_after": "11.4"}]
    co = _match_cart_price("1039147294809", p.variants, cart)
    row = _summarize(p, cart_overrides=co)
    assert row["variant_count"] == 1
    assert row["cheapest_available"] == 11.4
