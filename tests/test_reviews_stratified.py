"""Reviews stratified sampling (好/中/差评各取, 防注入好评) — 打磨轮次 20."""

from __future__ import annotations

from src.extract.reviews import stratified_reviews
from src.models import Review


def _mk(rating: int | None, text: str = "") -> Review:
    return Review(rating=rating, text=text or "x", has_images=False, sku_bought=None, date="2026-01-01")


def test_rating_present_stratified():
    revs = [_mk(5, "很好")] * 8 + [_mk(3, "一般")] * 4 + [_mk(1, "差")] * 4
    out = stratified_reviews(revs, max_total=12, per_rating=3)
    # 好/中/差各至多 3 条, 不因好评占多数而全取好评
    good = sum(1 for r in out if r.rating and r.rating >= 4)
    neutral = sum(1 for r in out if r.rating == 3)
    bad = sum(1 for r in out if r.rating and r.rating <= 2)
    assert good <= 3 and neutral <= 3 and bad <= 3
    assert bad >= 3 and neutral >= 3   # 差评/中评未被好评淹没
    assert len(out) <= 12


def test_no_rating_segment_sampling():
    revs = [_mk(None, f"r{i}") for i in range(30)]
    out = stratified_reviews(revs, max_total=9, per_rating=3)
    texts = [r.text for r in out]
    # 前/中/后 三段各取, 而非只取前 9(好评优先页)
    assert "r0" in texts and "r10" in texts and "r20" in texts
    assert len(out) == 9


def test_empty_and_caps():
    assert stratified_reviews([], max_total=5) == []
    out = stratified_reviews([_mk(None, f"r{i}") for i in range(6)], max_total=2, per_rating=5)
    assert len(out) <= 2


def test_rating_majority_threshold():
    # 带评分的占多数 → 走评分分层; 否则走三段抽样
    mixed = [_mk(5)] * 7 + [_mk(1)] * 3
    out = stratified_reviews(mixed, max_total=6, per_rating=2)
    bad = sum(1 for r in out if r.rating == 1)
    assert bad >= 2   # 差评保留, 未被 7 条好评淹没
