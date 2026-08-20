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
from src.dates import parse_date_iso

_BOUGHT_RE = re.compile(r"已购[:：]\s*(.+?)\s*$")


def parse_meta(meta: str) -> tuple[str | None, str | None]:
    """From '2026-05-26已购：…' OR '2026年5月26日已购：…' → (ISO date, sku_bought).

    Both formats normalize to ISO so dedupe collapses the preview/drawer duplicates.
    """
    date = parse_date_iso(meta or "")
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
    keyword: str = "",
) -> list[Review]:
    """Pure: image-only filter, optional text-keyword filter, recency sort, cap.

    keyword is a case-insensitive substring on the review text (Chinese works);
    applied BEFORE the max cap so the buyer sees the matching reviews, not a
    truncated-then-filtered slice.
    """
    out = [r for r in reviews if r.has_images] if only_with_images else list(reviews)
    kw = (keyword or "").strip()
    if kw:
        # keyword 匹配评论文本 OR 购买型号(sku_bought) — 找特定型号/密封款评论更有效
        out = [r for r in out if kw in (r.text or "") or kw in (r.sku_bought or "")]
    if most_recent_first:
        out.sort(key=lambda r: r.date or "", reverse=True)
    if max_reviews is not None:
        out = out[:max_reviews]
    return out


def stratified_reviews(
    reviews: list[Review],
    max_total: int | None = None,
    per_rating: int | None = None,
) -> list[Review]:
    """Pure: 好/中/差评各自分层抽样, 防止被注入好评(用户要求).

    站点列表常按"好评优先"排序, 只取前 N 条会系统性偏向好评。
    - 若多数评论带 rating(>=60%): 按 好(>=4)/中(3)/差(<=2) 分组, 每组各取 per_rating 条,
      无星级评论从前段补位少量, 再截断到 max_total。
    - 若无星级(列表接口常见): 按 前/中/后 三段各取 per_rating 条, 摊开覆盖不同时期/倾向。
    per_rating 缺省取 config anti_risk.review_sample_per_rating(默认 3)。
    """
    if per_rating is None:
        try:
            from src.config import load_config

            per_rating = load_config().anti_risk.review_sample_per_rating
        except Exception:
            per_rating = 3
    per = max(1, int(per_rating or 1))
    if max_total is None or max_total <= 0:
        max_total = len(reviews)
    if not reviews:
        return []

    rated = [r for r in reviews if r.rating is not None]
    unrated = [r for r in reviews if r.rating is None]
    picked: list[Review] = []

    if rated and len(rated) >= len(reviews) * 0.6:
        good = [r for r in rated if r.rating >= 4]
        neutral = [r for r in rated if r.rating == 3]
        bad = [r for r in rated if r.rating <= 2]
        for g in (good, neutral, bad):
            picked.extend(g[:per])
        picked.extend(unrated[: max(1, per // 2)])
    else:
        n = len(reviews)
        seg = max(1, n // 3)
        for start in (0, seg, 2 * seg):
            picked.extend(reviews[start:start + per])
    return picked[:max_total]


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
    keyword: str = "",
    page=None,
) -> list[Review]:
    """Live: open the product, open the "查看全部评价" drawer, paginate it, extract.

    Opens the full-review Drawer (not just the 2-card preview), scrolls its inner
    container to lazily load the written reviews, dedupes across the page's two date
    formats, and (by default) drops auto-generated "default good review" boilerplate
    so only genuine written reviews remain.

    page: 传入已在正确页面的 Page 时就地抽取(不新开页)。实证: Tmall 评论只在本收藏链路
    (mi_id 个性化详情页)渲染 — 普通 SSR 页 rateContent=0、无评论卡; 细查(fine)把 miid
    弹窗页传给本函数即可。miid 每次经收藏→点击收藏卡 新建, 用完即关, 不存在可复用 URL。
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.config import load_config
    from src.extract.product import _to_product_id

    cfg = load_config()
    cap = max_reviews if max_reviews is not None else cfg.limits.max_reviews
    pid = _to_product_id(product_url_or_id)

    if page is None:
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
    return apply_filters(reviews, only_with_images, most_recent_first, cap, keyword)


async def parse_reviews_stratified(
    product_url_or_id: str,
    max_reviews: int | None = 12,
    keyword: str = "",
    only_with_images: bool = False,
    most_recent_first: bool = True,
    page=None,
) -> list[Review]:
    """Live: 抓评论后做 好/中/差评 分层抽样(防注入好评), 供 taobao_product with_reviews.

    先按 max_reviews*3 抓原始池(保证每层有足够样本), 再 stratified_reviews 摊开到 max_reviews。
    page 传已在正确页面(mi_id 详情页)的 Page 时就地抽取 — Tmall 评论只在该页渲染;
    None 则开普通页(SSR 无评论, 会落到嵌入式回退)。抽屉抓取为空时回退嵌入式预览评论。
    """
    from src.extract.reviews import parse_reviews as _pr

    cap = max(3, int(max_reviews or 12) * 3)
    try:
        raw = await _pr(product_url_or_id, only_with_images=only_with_images,
                        most_recent_first=most_recent_first, max_reviews=cap, keyword=keyword,
                        page=page)
    except Exception:
        raw = []
    if raw:
        return stratified_reviews(raw, max_total=max_reviews)

    # 回退: 嵌入式预览评论(站点漂移)
    try:
        from src.extract.product import parse_product

        p = await parse_product(product_url_or_id)
        embedded = list(p.reviews or [])
        kw = (keyword or "").strip()
        if kw:
            embedded = [r for r in embedded if kw in (r.text or "") or kw in (r.sku_bought or "")]
        return stratified_reviews(embedded, max_total=max_reviews)
    except Exception:
        return []


async def probe_reviews_rendering(product_url_or_id: str) -> dict:
    """[DEBUG] 实证 Tmall 评论是否渲染: 分别探测 普通页 与 收藏链路(mi_id 弹窗页).

    回答"评论抽屉/评论卡在哪类页面出现", 以决定 with_reviews 的正确抓取路径。
    收藏链路每次经 收藏→点击收藏卡 新建弹窗页(新鲜 mi_id), 用完即关 — 不存在可复用 miid。
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.extract.favorite import click_from_favorites, ensure_favorited, ensure_unfavorited
    from src.extract.fav_quota import check_and_record
    from src.extract.product import _to_product_id
    from src.extract.selectors import DRAWER_SELECTOR

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()

    def evidence(url_label: str, html: str) -> dict:
        return {
            "url_label": url_label,
            "html_len": len(html),
            "n_评价": html.count("评价"),
            "n_rateContent": html.count("rateContent"),
            "n_查看全部评价": html.count("查看全部评价"),
            "n_Comment_class": html.count('class*="Comment'),
        }

    out: dict = {"product_id": pid, "pages": {}}

    # --- 普通页(非 miid) ---
    await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
    await session.guard_captcha(page)
    for _ in range(3):
        await human_scroll(page, 3)
        await human_delay(0.8, 1.5)
    out["pages"]["plain"] = evidence("plain", await page.content())

    # --- 收藏链路 mi_id 弹窗页 ---
    entry = {"added_by_us": False}
    try:
        quota = check_and_record()
        out["fav_quota"] = quota
        if quota.get("allowed"):
            fav = await ensure_favorited(page, pid)
            entry["added_by_us"] = bool(fav.get("added_by_us"))
            res = await click_from_favorites(page, pid, added_by_us=entry["added_by_us"])
            popup = res.get("popup")
            if popup and res.get("mi_id"):
                out["mi_id"] = res["mi_id"]
                out["clicked_url"] = res["url"]
                await human_delay(2.0, 3.2)
                out["pages"]["miid"] = evidence("miid_popup", await popup.content())
                drawer_info: dict = {"clicked": False, "comment_cards": 0, "extracted": 0}
                try:
                    for _ in range(5):
                        await human_scroll(popup, 2)
                        await human_delay(0.8, 1.2)
                    for label in VIEW_ALL_LABELS:
                        loc = popup.get_by_text(label, exact=False).first
                        if await loc.count() > 0:
                            await loc.scroll_into_view_if_needed(timeout=3000)
                            await loc.click(timeout=3000)
                            drawer_info["clicked"] = True
                            break
                    await human_delay(2.0, 3.0)
                    drawer_info["comment_cards"] = await popup.locator('[class*="Comment--"]').count()
                    drawer_info["drawer_present"] = await popup.locator(DRAWER_SELECTOR).count() > 0
                    extracted = await popup.evaluate(_EXTRACT_JS)
                    drawer_info["extracted"] = len(extracted or [])
                    drawer_info["sample"] = [(r.get("text") or "")[:40] for r in (extracted or [])[:3]]
                    try:
                        first = popup.locator('[class*="Comment--"]').first
                        drawer_info["first_card_html"] = (await first.evaluate("el => el.outerHTML") or "")[:2000]
                    except Exception:
                        drawer_info["first_card_html"] = None
                except Exception as exc:
                    drawer_info["error"] = str(exc)[:120]
                out["drawer_on_miid"] = drawer_info

                # Q&A(问大家)区结构: 可见卡数 / "查看更多·查看全部"按钮 / 问答页链接
                qa_info: dict = {}
                try:
                    qa_sel = '[class*="askAnswerItem"], [class*="qaItem"], [class*="QA"]'
                    qa_info["cards"] = await popup.locator(qa_sel).count()
                    first_qa = popup.locator(qa_sel).first
                    if await first_qa.count() > 0:
                        qa_info["first_html"] = (await first_qa.evaluate("el => el.outerHTML") or "")[:1200]
                    for lbl in ("查看更多", "查看全部问答", "全部问答", "更多问答", "问大家"):
                        n = await popup.get_by_text(lbl, exact=False).count()
                        if n:
                            qa_info.setdefault("buttons", {})[lbl] = n
                    qa_info["links"] = await popup.evaluate(
                        """() => [...document.querySelectorAll('a[href*="ask"],a[href*="qa"],a[href*="wenda"],a[href*="answer"],a[href*="wenj"]')].map(a => a.href).slice(0,6)""")
                except Exception as exc:
                    qa_info["error"] = str(exc)[:100]
                out["qa_on_miid"] = qa_info
                try:
                    await popup.close()
                except Exception:
                    pass
            else:
                out["click_fail_reason"] = res.get("reason")
        else:
            out["fav_quota_blocked"] = True
    except Exception as exc:
        out["fav_flow_error"] = str(exc)[:200]
    finally:
        if entry.get("added_by_us"):
            try:
                await ensure_unfavorited(page, pid)
            except Exception:
                pass
    return out
