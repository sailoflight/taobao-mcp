"""Reviews stratified sampling (好/中/差评各取, 防注入好评) — 打磨轮次 20."""

from __future__ import annotations

import asyncio

import pytest

from src.extract.reviews import parse_reviews_stratified, stratified_reviews
from src.errors import CaptchaError, SelectorDriftError
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


# ---- CaptchaError / SelectorDriftError must NOT be swallowed (audit HIGH-3) ----

def test_stratified_propagates_captcha_from_drawer(monkeypatch):
    """风控墙出现在评论抽屉抓取时 → CaptchaError 上浮, 不吞成空结果。"""
    import src.extract.reviews as reviews_mod

    async def boom(*a, **k):
        raise CaptchaError()

    monkeypatch.setattr(reviews_mod, "parse_reviews", boom)
    with pytest.raises(CaptchaError):
        asyncio.run(parse_reviews_stratified("12345678901", max_reviews=4))


def test_stratified_propagates_selector_drift_from_drawer(monkeypatch):
    """评论抽屉选择器漂移 → SelectorDriftError 上浮, 不吞成空结果。"""
    import src.extract.reviews as reviews_mod

    async def boom(*a, **k):
        raise SelectorDriftError(step="reviews drawer")

    monkeypatch.setattr(reviews_mod, "parse_reviews", boom)
    with pytest.raises(SelectorDriftError):
        asyncio.run(parse_reviews_stratified("12345678901", max_reviews=4))


def test_stratified_propagates_captcha_from_embedded_fallback(monkeypatch):
    """主抓取空 → 嵌入式回退重导航撞上风控墙 → CaptchaError 同样上浮。"""
    import src.extract.product as product_mod
    import src.extract.reviews as reviews_mod

    async def no_reviews(*a, **k):
        return []

    async def boom(*a, **k):
        raise CaptchaError()

    monkeypatch.setattr(reviews_mod, "parse_reviews", no_reviews)
    monkeypatch.setattr(product_mod, "parse_product", boom)
    with pytest.raises(CaptchaError):
        asyncio.run(parse_reviews_stratified("12345678901", max_reviews=4))


def test_stratified_still_falls_back_on_benign_empty(monkeypatch):
    """主抓取空、无风控/漂移 → 仍回退到嵌入式预览评论(行为不变, 仅不吞真实错误)。"""
    import src.extract.product as product_mod
    import src.extract.reviews as reviews_mod

    async def no_reviews(*a, **k):
        return []

    async def embedded(*a, **k):
        p = type("P", (), {"reviews": [_mk(None, "嵌入好评")]})()
        return p

    monkeypatch.setattr(reviews_mod, "parse_reviews", no_reviews)
    monkeypatch.setattr(product_mod, "parse_product", embedded)
    out = asyncio.run(parse_reviews_stratified("12345678901", max_reviews=4))
    assert [r.text for r in out] == ["嵌入好评"]
