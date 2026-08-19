"""Tests for the review text-keyword pre-filter (买家找差评/缺陷常用) — 打磨轮次 11."""

from __future__ import annotations

from src.extract.reviews import apply_filters
from src.models import Review


def _r(text, has_images=True, date="2026-08-01"):
    return Review(rating=5, text=text, has_images=has_images, sku_bought=None, date=date)


def test_keyword_filters_text():
    rs = [_r("密封很严实"), _r("有点开裂"), _r("尺寸刚好")]
    assert [r.text for r in apply_filters(rs, keyword="开裂")] == ["有点开裂"]


def test_keyword_blank_noop():
    rs = [_r("密封"), _r("开裂")]
    assert len(apply_filters(rs, keyword="  ")) == 2
    assert len(apply_filters(rs, keyword="")) == 2


def test_keyword_with_images_and_cap():
    rs = [_r("开裂", has_images=False), _r("开裂", has_images=True), _r("开裂2", has_images=True)]
    out = apply_filters(rs, only_with_images=True, keyword="开裂", max_reviews=1)
    assert len(out) == 1 and out[0].has_images


def test_keyword_no_match_empty():
    rs = [_r("密封")]
    assert apply_filters(rs, keyword="找不到的词") == []
