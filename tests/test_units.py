"""Tests for the shared 'N个装' per-unit price helper — 打磨轮次 37 (DRY)."""

from __future__ import annotations

from src.extract.units import unit_price_from_label


def test_unit_price_parses_pack_counts():
    assert unit_price_from_label("规格:1个装", 36.0) == 36.0
    assert unit_price_from_label("规格:2个装", 67.25) == 33.625
    assert unit_price_from_label("颜色:X; 规格:3个装【保价】", 91.0) == 91.0 / 3


def test_unit_price_no_match_or_bad():
    assert unit_price_from_label("规格:单个", 36.0) is None
    assert unit_price_from_label("规格:1个装", None) is None
    assert unit_price_from_label("规格:2个装", "abc") is None
