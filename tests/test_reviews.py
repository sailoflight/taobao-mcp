"""Phase 2c acceptance tests for reviews: variant linkage + pagination (Appendix A.4)."""

from __future__ import annotations

import json
from pathlib import Path

from src.extract.reviews import (
    apply_filters,
    dedupe,
    dicts_to_reviews,
    group_by_variant,
    is_default_review,
    parse_meta,
)
from src.models import Review

FIXTURES = Path(__file__).parent / "fixtures"

# Mirrors the real rendered cards captured from the P100 page.
REAL_CARDS = [
    {"text": "速度很快，插上就能用，用着没问题", "meta": "2026-05-26已购：P100 质保3年 以换代修", "has_images": True},
    {"text": "用的技嘉主板，ollama和comfyui都ok", "meta": "2026-03-19已购：P100 质保7天 走量商品 退货承担运费20元", "has_images": True},
]


def test_parse_meta():
    assert parse_meta("2026-05-26已购：P100 质保3年 以换代修") == ("2026-05-26", "P100 质保3年 以换代修")
    # half-width colon variant
    assert parse_meta("2026-03-19已购:黑色 L") == ("2026-03-19", "黑色 L")
    assert parse_meta("no structured meta") == (None, None)


def test_review_sku_linkage():
    """Every review carries sku_bought, and it maps to a REAL variant label from the product fixture."""
    reviews = dicts_to_reviews(REAL_CARDS)
    assert all(r.sku_bought for r in reviews)
    assert all(r.has_images for r in reviews)
    groups = group_by_variant(reviews)
    assert groups  # non-empty rollup

    res = json.loads((FIXTURES / "736546459871" / "detail_res.json").read_text(encoding="utf-8"))
    real_labels = {v["name"] for g in res["skuBase"]["props"] for v in g["values"]}
    assert set(groups.keys()) <= real_labels  # each review links to a known variant


def test_pagination_cap():
    base = [
        Review(rating=None, text=f"t{i}", has_images=(i % 2 == 0), sku_bought="A", date=f"2026-01-{(i % 28) + 1:02d}")
        for i in range(20)
    ]
    with_dups = base + base[:5]            # 5 exact duplicates
    deduped = dedupe(with_dups)
    assert len(deduped) == 20              # dups removed
    assert len(apply_filters(deduped, max_reviews=10)) == 10   # never exceeds cap


def test_only_with_images_and_recency():
    rs = [
        Review(rating=None, text="old-noimg", has_images=False, sku_bought=None, date="2026-01-01"),
        Review(rating=None, text="new-img", has_images=True, sku_bought=None, date="2026-05-01"),
    ]
    only = apply_filters(rs, only_with_images=True)
    assert [r.text for r in only] == ["new-img"]
    recent = apply_filters(rs, most_recent_first=True)
    assert recent[0].date == "2026-05-01"


def test_parse_meta_chinese_date():
    assert parse_meta("2026年3月19日已购：黑色 L") == ("2026-03-19", "黑色 L")


def test_dedupe_collapses_date_formats():
    """The same review appears in preview (ISO) and drawer (Chinese) — dedup to one."""
    cards = [
        {"text": "速度很快，插上就能用", "meta": "2026-03-19已购：P100 质保7天", "has_images": True},
        {"text": "速度很快，插上就能用", "meta": "2026年3月19日已购：P100 质保7天", "has_images": True},
    ]
    assert len(dedupe(dicts_to_reviews(cards))) == 1


def test_is_default_review_filter():
    assert is_default_review("该用户觉得商品非常好，给出好评")
    assert is_default_review("此用户没有填写评价。")
    assert is_default_review("系统默认评价")
    assert not is_default_review("成色非常好，一次点亮，跑llm还行")


def test_dedupe_merges_has_images():
    # H4: the preview (no photo) + drawer (photo) copies of one review must keep has_images=True
    cards = [
        {"text": "好卡，点亮快", "meta": "2026-03-19已购：黑", "has_images": False},
        {"text": "好卡，点亮快", "meta": "2026年3月19日已购：黑", "has_images": True},
    ]
    out = dedupe(dicts_to_reviews(cards))
    assert len(out) == 1
    assert out[0].has_images is True


def test_meta_strips_trailing_zhuiping():
    # M3: 追评 text must not pollute sku_bought
    date, sku = parse_meta("2026-05-26已购：P100 质保3年 追评：又买了一片")
    assert sku == "P100 质保3年"


def test_meta_double_space_not_truncated():
    # a label with an internal double space must NOT be truncated to the first token
    _, sku = parse_meta("2026-05-26已购：P100  16G 走量")
    assert sku and sku.startswith("P100") and "走量" in sku


def test_meta_zhuiping_word_in_label_not_blanked():
    # a label literally containing 追评 (no colon) must not be blanked
    _, sku = parse_meta("2026-01-01已购：追评专享套餐")
    assert sku == "追评专享套餐"


def test_dedupe_does_not_mutate_input():
    a = Review(rating=None, text="x", has_images=False, sku_bought="A", date="2026-01-01")
    b = Review(rating=None, text="x", has_images=True, sku_bought="A", date="2026-01-01")
    out = dedupe([a, b])
    assert a.has_images is False        # caller's object untouched
    assert out[0].has_images is True    # merged copy carries the image flag
