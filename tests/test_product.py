"""Phase 2a acceptance tests for per-SKU price extraction (CLAUDE.md Appendix A.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.extract.product import (
    _price_from_info,
    _to_product_id,
    build_variants,
    cartesian_count,
    extract_ice_res,
    extract_subsidy_caveat,
    fill_subsidy_prices,
    parse_product_res,
    parse_sku_info,
)
from src.models import Product, SkuVariant

FIXTURES = Path(__file__).parent / "fixtures"
P100_ID = "736546459871"


def _p100_html() -> str:
    path = FIXTURES / P100_ID / "page.html"
    if not path.exists():
        pytest.skip(f"raw fixture {path} not present (gitignored; capture locally to test HTML extraction)")
    return path.read_text(encoding="utf-8")


def _p100_res() -> dict:
    """The committed, token-free sanitized fixture (CI-safe)."""
    return json.loads((FIXTURES / P100_ID / "detail_res.json").read_text(encoding="utf-8"))


def test_specs_extracted_from_components():
    """参数 specs come from componentsVO.BASE_PROPS — were silently always empty before."""
    p = parse_product_res(_p100_res(), P100_ID)
    assert p.specs.get("品牌") == "0431"
    assert p.specs.get("浮点运算精度") == "FP32"
    assert "生产企业" in p.specs


def test_embedded_reviews_variant_linked():
    """Reviews come embedded in the same HTML (componentsVO.rateVO) — no second navigation."""
    p = parse_product_res(_p100_res(), P100_ID)
    assert len(p.reviews) == 2
    assert all(r.sku_bought for r in p.reviews)            # full skuInfo label, clean linkage
    assert any(r.has_images for r in p.reviews)
    assert p.reviews_by_variant


def test_parse_sku_info():
    assert parse_sku_info("颜色分类:P100 质保3年 以换代修") == "P100 质保3年 以换代修"
    assert parse_sku_info("颜色:黑;尺寸:L") == "黑 L"
    assert parse_sku_info("") is None


def test_subsidy_caveat_flagged():
    """The after-subsidy (平台加补后) price differs from 优惠前 → caveat surfaced."""
    p = parse_product_res(_p100_res(), P100_ID)
    assert p.subsidy_caveat is not None
    assert "397" in p.subsidy_caveat and "400" in p.subsidy_caveat   # after-subsidy vs pre-discount


def test_subsidy_caveat_none_when_no_gap():
    assert extract_subsidy_caveat({}) is None
    assert extract_subsidy_caveat(
        {"componentsVO": {"priceVO": {"price": {"priceText": "100"}, "extraPrice": {"priceText": "100"}}}}
    ) is None


def test_deep_price_skips_large_products():
    """deep_price must skip (no clicking) when a product has too many SKUs — returns before touching the page."""
    import asyncio

    variants = [
        SkuVariant(sku_id=str(i), properties={"颜色": f"c{i}"}, price=10.0, stock=1, available=True)
        for i in range(30)
    ]
    p = Product(product_id="1", url="u", title="t", shop_name="s", price_range=(10.0, 10.0),
                variants=variants, scraped_at="2026-06-04T00:00:00Z")
    asyncio.run(fill_subsidy_prices(None, p, max_skus=24))  # page=None proves it never interacts
    assert all(v.price == 10.0 for v in p.variants)


def test_all_variants_priced():
    """Every SKU on the real P100 page comes out with its own price + readable label."""
    product = parse_product_res(_p100_res(), P100_ID)
    assert len(product.variants) == 3
    for v in product.variants:
        assert v.price is not None, f"variant {v.sku_id} missing price"
        assert "颜色分类" in v.properties, f"label not human-readable: {v.properties}"
        # labels must be names, never raw pid:vid
        assert not any(":" in k and k.replace(":", "").isdigit() for k in v.properties)


def test_variant_prices_exact():
    """Prices match the fixture: tiers are 420 / 450 / 400 (the 80-unit wholesale floor)."""
    product = parse_product_res(_p100_res(), P100_ID)
    by_id = {v.sku_id: v.price for v in product.variants}
    assert by_id["5731208484120"] == 420.0   # 7-day volume tier
    assert by_id["5731208484121"] == 450.0   # 3-year warranty tier
    assert by_id["5940639352839"] == 400.0   # 80-unit wholesale tier
    assert product.price_range == (400.0, 450.0)


def test_product_metadata():
    product = parse_product_res(_p100_res(), P100_ID)
    assert "P100" in product.title
    assert product.shop_name == "南京海雀显卡"
    assert len(product.image_urls) >= 1


def test_extract_ice_res_has_blocks():
    res = extract_ice_res(_p100_html())
    assert "skuBase" in res and "skuCore" in res


def test_multigroup_cartesian_3x4():
    """Synthetic 3-colour x 4-size product → exactly 12 priced variants, 2-key labels."""
    colours = [{"vid": f"c{i}", "name": n} for i, n in enumerate(["黑色", "白色", "红色"])]
    sizes = [{"vid": f"s{i}", "name": n} for i, n in enumerate(["S", "M", "L", "XL"])]
    sku_base = {
        "props": [
            {"pid": "1", "name": "颜色", "values": colours},
            {"pid": "2", "name": "尺寸", "values": sizes},
        ],
        "skus": [
            {"propPath": f"1:{c['vid']};2:{s['vid']}", "skuId": f"{c['vid']}-{s['vid']}"}
            for c in colours
            for s in sizes
        ],
    }
    sku2info = {
        sku["skuId"]: {"price": {"priceMoney": "9900"}, "quantity": 7, "quantityText": "有货"}
        for sku in sku_base["skus"]
    }
    assert cartesian_count(sku_base) == 12
    variants = build_variants(sku_base, sku2info)
    assert len(variants) == 12
    for v in variants:
        assert set(v.properties.keys()) == {"颜色", "尺寸"}
        assert v.price == 99.0 and v.available is True


def test_single_sku_keeps_headline_price():
    """C1: a no-matrix product must still emit the headline price from sku2info['0']."""
    sku_base = {"props": [], "skus": []}
    sku2info = {"0": {"price": {"priceMoney": "39900", "priceText": "399起"}, "quantity": 50, "quantityText": "有货"}}
    variants = build_variants(sku_base, sku2info)
    assert len(variants) == 1
    assert variants[0].price == 399.0 and variants[0].available is True
    res = {"skuBase": sku_base, "skuCore": {"sku2info": sku2info}, "item": {"title": "x"}, "seller": {"shopName": "s"}}
    assert parse_product_res(res, "1").price_range == (399.0, 399.0)


def test_to_product_id_parsing():
    assert _to_product_id("736546459871") == "736546459871"
    assert _to_product_id("https://item.taobao.com/item.htm?id=736546459871&spm=a") == "736546459871"
    assert _to_product_id("https://detail.tmall.com/item.htm?spm=x&id=12345678901") == "12345678901"
    with pytest.raises(Exception):
        _to_product_id("not-a-product")


def test_price_text_fallback_formats():
    """H6: priceText drifts — ¥-prefixed, suffixed, ranged must still parse."""
    assert _price_from_info({"price": {"priceText": "¥420"}}) == 420.0
    assert _price_from_info({"price": {"priceText": "420.00起"}}) == 420.0
    assert _price_from_info({"price": {"priceText": "420-450"}}) == 420.0
    assert _price_from_info({"price": {}}) is None


def test_zero_price_is_unavailable():
    """M2: a ¥0 placeholder SKU is not a real in-stock variant."""
    sku_base = {"props": [{"pid": "1", "name": "x", "values": [{"vid": "9", "name": "v"}]}], "skus": [{"propPath": "1:9", "skuId": "A"}]}
    v = build_variants(sku_base, {"A": {"price": {"priceMoney": "0"}, "quantity": 5, "quantityText": "有货"}})[0]
    assert v.price is None and v.available is False


def test_unknown_propvid_not_leaked_raw():
    """H5: a propPath vid missing from props is dropped, never emitted as a raw pid:vid."""
    sku_base = {"props": [{"pid": "1", "name": "颜色", "values": [{"vid": "10", "name": "黑"}]}], "skus": [{"propPath": "1:10;9:99", "skuId": "A"}]}
    v = build_variants(sku_base, {"A": {"price": {"priceMoney": "10000"}, "quantity": 3}})[0]
    assert v.properties == {"颜色": "黑"}


def test_oos_variant_marked():
    """A sold-out variant (quantity 0 / 无货) is priced None and available=False."""
    sku_base = {
        "props": [{"pid": "1", "name": "颜色", "values": [{"vid": "10", "name": "黑"}, {"vid": "11", "name": "白"}]}],
        "skus": [{"propPath": "1:10", "skuId": "A"}, {"propPath": "1:11", "skuId": "B"}],
    }
    sku2info = {
        "A": {"price": {"priceMoney": "10000"}, "quantity": 5, "quantityText": "有货"},
        "B": {"price": {"priceMoney": "12000"}, "quantity": 0, "quantityText": "无货"},
    }
    variants = {v.sku_id: v for v in build_variants(sku_base, sku2info)}
    assert variants["A"].available is True and variants["A"].price == 100.0
    assert variants["B"].available is False and variants["B"].price is None
