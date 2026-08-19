"""Shared "N个装" per-unit price helper (DRY across product/compare/cart).

A variant/cart label like "规格:2个装【特厚】" means the pack holds 2 pieces;
per-unit price = pack price / N. Kept in one place so product/compare/cart
never drift.
"""

from __future__ import annotations

import re

_UNIT_RE = re.compile(r"(\d+)\s*个装")


def unit_price_from_label(label: str, price) -> float | None:
    """Pure: label 含 'N个装' 时算每件单价(price/N), 否则 None."""
    if price is None:
        return None
    m = _UNIT_RE.search(label or "")
    if not m:
        return None
    try:
        return float(price) / int(m.group(1))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
