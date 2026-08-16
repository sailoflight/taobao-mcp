"""Compact per-product markdown for in-chat reading. Phase 4 (Output Agent).

Tools return BOTH this markdown (human-readable) and the structured model
(structuredContent) so Claude can read and re-use the data (CLAUDE.md §8 Phase 3).
"""

from __future__ import annotations

from src.models import Product


def _money(x: float | None) -> str:
    return f"¥{x:g}" if x is not None else "—"


def product_to_markdown(product: Product) -> str:
    """Render a Product as a compact markdown block (title, price range, variants, review gist)."""
    lines: list[str] = [f"### {product.title or product.product_id}"]
    lines.append(f"- **Shop:** {product.shop_name or '—'}")
    if product.price_range:
        lines.append(f"- **Price:** {_money(product.price_range[0])} – {_money(product.price_range[1])}")
    if product.subsidy_caveat:
        lines.append(f"- ⚠️ **Subsidy:** {product.subsidy_caveat}")
    lines.append(f"- **Variants:** {len(product.variants)}  ·  **Images:** {len(product.image_urls)}")
    lines.append(f"- **URL:** {product.url}")

    if product.variants:
        lines += ["", "| Variant | Price | Stock | In stock |", "|---|---|---|---|"]
        for v in product.variants:
            label = " / ".join(f"{k}: {val}" for k, val in v.properties.items()) or v.sku_id
            lines.append(f"| {label} | {_money(v.price)} | {v.stock if v.stock is not None else '—'} | {'✓' if v.available else '✗'} |")

    if product.reviews:
        n = len(product.reviews)
        imgs = sum(1 for r in product.reviews if r.has_images)
        lines += ["", f"- **Reviews:** {n} ({imgs} with photos) — raw Chinese; translate in-chat."]
        for label, rs in (product.reviews_by_variant or {}).items():
            ri = sum(1 for r in rs if r.has_images)
            lines.append(f"  - {label}: {len(rs)} ({ri} with photos)")

    return "\n".join(lines)
