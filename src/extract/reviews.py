"""Reviews parser with pagination + variant linkage (SECOND DELIVERABLE).

On the new SSR detail page reviews render into DOM cards (``[class*="Comment--"]``)
rather than a clean rate XHR (confirmed via capture). Each card exposes:
  - userName  ([class*="userName--"])  — reviewer nick; NOT stored (PII)
  - meta      ([class*="meta--"])      — e.g. "2026-05-26已购：P100 质保3年 以换代修"
  - content   ([class*="content--"])   — the review text (kept raw Chinese)
  - album/photo imgs                   — review photos → has_images

The ``已购：<label>`` string equals a SkuVariant property value, so reviews link
cleanly to variants (CLAUDE.md Appendix A.2). Pure helpers below are unit-tested;
parse_reviews() drives the live page (scroll to lazily paginate). Claude translates.
"""

from __future__ import annotations

import re

from src.extract.selectors import (
    DEFAULT_REVIEW_MARKERS,
    REVIEW_DRAWER_SCROLL_JS,
    VIEW_ALL_LABELS,
    REVIEW_EXTRACT_JS as _EXTRACT_JS,  # centralized (Phase 6)
)
from src.models import Review

_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_CN_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_BOUGHT_RE = re.compile(r"已购[:：]\s*(.+?)\s*$")


def parse_meta(meta: str) -> tuple[str | None, str | None]:
    """From '2026-05-26已购：…' OR '2026年5月26日已购：…' → (ISO date, sku_bought).

    Both formats normalize to ISO so dedupe collapses the preview/drawer duplicates.
    """
    date = None
    m = _DATE_RE.search(meta or "") or _DATE_CN_RE.search(meta or "")
    if m:
        date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    sku = None
    m2 = _BOUGHT_RE.search(meta or "")
    if m2:
        # drop only a trailing "追评:…" segment; keep labels with internal spaces intact
        sku = re.sub(r"\s*追评[:：].*$", "", m2.group(1).strip()).strip()
    return date, sku


def is_default_review(text: str) -> bool:
    """True for auto-generated 'default good review' boilerplate (no real content)."""
    return any(marker in (text or "") for marker in DEFAULT_REVIEW_MARKERS)


def dicts_to_reviews(raw: list[dict]) -> list[Review]:
    """Convert extracted card dicts into Review models (raw Chinese, no rating on list view)."""
    reviews: list[Review] = []
    for r in raw:
        date, sku = parse_meta(r.get("meta", ""))
        reviews.append(
            Review(
                rating=None,  # the list view exposes no numeric star
                text=r.get("text", "").strip(),
                has_images=bool(r.get("has_images")),
                sku_bought=sku,
                date=date,
            )
        )
    return reviews


def dedupe(reviews: list[Review]) -> list[Review]:
    """Dedupe by (text, date, sku_bought), preserving order.

    When the same review appears twice (preview ISO date + drawer Chinese date), OR
    the has_images flag so the image-bearing copy's photos are never lost (H4).
    """
    index: dict[tuple, Review] = {}
    order: list[tuple] = []
    for rv in reviews:
        key = (rv.text, rv.date, rv.sku_bought)
        if key in index:
            if rv.has_images and not index[key].has_images:
                index[key] = index[key].model_copy(update={"has_images": True})  # no in-place mutation
            continue
        index[key] = rv
        order.append(key)
    return [index[k] for k in order]


def apply_filters(
    reviews: list[Review],
    only_with_images: bool = False,
    most_recent_first: bool = True,
    max_reviews: int | None = None,
) -> list[Review]:
    out = [r for r in reviews if r.has_images] if only_with_images else list(reviews)
    if most_recent_first:
        out.sort(key=lambda r: r.date or "", reverse=True)
    if max_reviews is not None:
        out = out[:max_reviews]
    return out


def group_by_variant(reviews: list[Review]) -> dict[str, list[Review]]:
    """Roll reviews up into {sku_bought label -> [Review]} for Product.reviews_by_variant."""
    groups: dict[str, list[Review]] = {}
    for rv in reviews:
        if not rv.sku_bought:
            continue
        groups.setdefault(rv.sku_bought, []).append(rv)
    return groups


async def parse_reviews(
    product_url_or_id: str,
    only_with_images: bool = False,
    most_recent_first: bool = True,
    max_reviews: int | None = None,
    include_default: bool = False,
) -> list[Review]:
    """Live: open the product, open the "查看全部评价" drawer, paginate it, extract.

    Opens the full-review Drawer (not just the 2-card preview), scrolls its inner
    container to lazily load the written reviews, dedupes across the page's two date
    formats, and (by default) drops auto-generated "default good review" boilerplate
    so only genuine written reviews remain.
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.config import load_config
    from src.extract.product import _to_product_id

    cfg = load_config()
    cap = max_reviews if max_reviews is not None else cfg.limits.max_reviews
    pid = _to_product_id(product_url_or_id)

    session = get_session()
    page = await session.start()
    await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
    await session.guard_captcha(page)

    # Scroll to the reviews and open the "view all" drawer.
    for _ in range(5):
        await human_scroll(page, 2)
        await human_delay(1.0, 1.5)
    for label in VIEW_ALL_LABELS:
        try:
            loc = page.get_by_text(label, exact=False).first
            if await loc.count() > 0:
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=3000)
                break
        except Exception:
            continue
    await human_delay(2.0, 3.0)

    # Paginate inside the drawer until the set stops growing or cap reached.
    raw: list[dict] = []
    last = 0
    stale = 0
    for _ in range(cfg.limits.review_pages * 4):
        try:
            await page.evaluate(REVIEW_DRAWER_SCROLL_JS)
        except Exception:
            pass
        await human_delay(1.0, 1.8)
        raw = await page.evaluate(_EXTRACT_JS)
        # cap on UNIQUE GENUINE reviews (each renders twice; boilerplate is filtered out later).
        unique = dedupe(dicts_to_reviews(raw))
        genuine = unique if include_default else [r for r in unique if not is_default_review(r.text)]
        if len(genuine) >= cap:
            break
        stale = stale + 1 if len(raw) == last else 0
        if stale >= 3:
            break
        last = len(raw)

    reviews = dedupe(dicts_to_reviews(raw))
    if not include_default:
        reviews = [r for r in reviews if not is_default_review(r.text)]
    return apply_filters(reviews, only_with_images, most_recent_first, cap)
