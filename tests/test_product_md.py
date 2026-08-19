"""Tests for the single-product markdown renderer (全部型号价表) — 打磨轮次 17."""

from __future__ import annotations

from src.extract.product import _product_markdown
from src.models import Product, Review, SkuVariant


def _product() -> Product:
    return Product(
        product_id="1", url="https://item.taobao.com/item.htm?id=1", title="天鼠收纳箱 密封",
        shop_name="天鼠家居旗舰店", price_range=(36.0, 54.75),
        variants=[
            SkuVariant(sku_id="a", properties={"颜色": "白", "规格": "特大号"}, price=42.25, stock=500, available=True),
            SkuVariant(sku_id="b", properties={"颜色": "白", "规格": "超大号"}, price=54.75, stock=0, available=False),
        ],
        reviews=[Review(rating=5, text="结实", has_images=False, sku_bought=None, date="2026-08-01")],
        scraped_at="2026-08-18",
    )


def test_product_markdown_header_and_variants():
    md = _product_markdown(_product())
    assert "### 天鼠收纳箱 密封" in md
    assert "店铺: 天鼠家居旗舰店" in md and "¥36–¥54.75" in md and "型号数: 2" in md
    assert "颜色:白; 规格:特大号 | 42.25 | - | 500 | ✓" in md
    assert "颜色:白; 规格:超大号 | 54.75 | - | 0 | ✗" in md


def test_product_markdown_subsidy_caveat():
    p = _product()
    p.subsidy_caveat = "国补价需大陆身份"
    assert "⚠️ 补贴提示: 国补价需大陆身份" in _product_markdown(p)


def test_product_markdown_shows_review_total():
    p = _product()
    p.review_total = "1000+"
    p.favorable_rate = "98%"
    md = _product_markdown(p)
    assert "总评价: 1000+ (98%)" in md


def test_product_markdown_cheapest_available():
    p = _product()
    p.variants = [
        SkuVariant(sku_id="c", properties={"颜色": "白", "规格": "超大号"}, price=54.75, stock=0, available=False),
        SkuVariant(sku_id="a", properties={"颜色": "白", "规格": "特大号"}, price=42.25, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"颜色": "白", "规格": "加大号"}, price=36.0, stock=1, available=True),
    ]
    md = _product_markdown(p)
    assert "🟢 最便宜有货: 颜色:白; 规格:加大号 → ¥36" in md


def test_product_markdown_no_highlight_when_all_oos():
    p = _product()
    p.variants = [SkuVariant(sku_id="a", properties={"颜色": "白"}, price=42.25, stock=0, available=False)]
    assert "🟢" not in _product_markdown(p)


def test_product_markdown_unit_price_column():
    p = _product()
    p.variants = [
        SkuVariant(sku_id="a", properties={"规格": "1个装【天猫甄检】"}, price=36.0, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"规格": "2个装【特厚】"}, price=67.25, stock=1, available=True),
        SkuVariant(sku_id="c", properties={"规格": "单个"}, price=36.0, stock=1, available=True),
    ]
    md = _product_markdown(p)
    assert "| 型号 | 价格¥ | 单价¥ | 库存 | 有货 |" in md
    assert "规格:1个装【天猫甄检】 | 36 | 36.00" in md
    assert "规格:2个装【特厚】 | 67.25 | 33.62" in md
    assert "规格:单个 | 36 | -" in md


def test_product_markdown_specs_section():
    p = _product()
    p.specs = {"材质": "PP", "密封": "硅胶圈+卡扣", "尺寸": "56*41*32cm"}
    md = _product_markdown(p)
    assert "| 参数 | 值 |" in md
    assert "| 材质 | PP |" in md and "| 密封 | 硅胶圈+卡扣 |" in md


def test_product_markdown_no_specs_ok():
    p = _product()
    p.specs = {}
    assert "| 参数 |" not in _product_markdown(p)


def test_product_markdown_top3_cheapest_available():
    p = _product()
    p.variants = [
        SkuVariant(sku_id="a", properties={"规格": "1个装"}, price=36.0, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"规格": "2个装"}, price=67.25, stock=1, available=True),
        SkuVariant(sku_id="c", properties={"规格": "3个装"}, price=91.0, stock=1, available=True),
        SkuVariant(sku_id="d", properties={"规格": "超大"}, price=54.75, stock=0, available=False),
    ]
    md = _product_markdown(p)
    assert "💰 最便宜有货 Top3:" in md
    top = md.split("💰 最便宜有货 Top3:")[1].split("\n\n")[0]
    assert "- 规格:1个装 → ¥36" in top
    assert "超大" not in top
