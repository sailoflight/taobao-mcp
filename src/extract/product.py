"""Detail + per-SKU price extraction (PRIMARY DELIVERABLE).

Requirement: a price for EVERY SKU (CLAUDE.md Appendix A.1). The new SSR detail
page (tbpcDetail_ssr2025) does NOT fetch detail via an mtop XHR — it embeds the
data in the page as an ICE.js context: ``var b = {... loaderData.home.data.res
...}`` where ``res.skuBase`` holds props+skus and ``res.skuCore.sku2info`` maps
skuId → price/quantity. We extract that object from the HTML and apply the join.

Confirmed field shapes (fixture 736546459871):
  skuBase.props[i]   = {pid, name (e.g. "颜色分类"), values:[{vid, name, corner}]}
  skuBase.skus[i]    = {propPath: "pid:vid;pid:vid", skuId}
  skuCore.sku2info[skuId] = {price:{priceText,"priceMoney"(fen),priceTitle}, quantity, quantityText}
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from src.errors import ProductNotFoundError, SelectorDriftError, SkuIncompleteError
from src.extract.selectors import ICE_ANCHORS, RES_SKU_BASE_KEY
from src.models import Product, Review, SkuVariant

# ---- embedded-data extraction ---------------------------------------------


def _balanced_object(text: str, start_at: int) -> str | None:
    """Return the balanced {...} starting at/after start_at, respecting strings."""
    j = text.find("{", start_at)
    if j < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for k in range(j, len(text)):
        c = text[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[j:k + 1]
    return None


def extract_ice_res(html: str) -> dict:
    """Pull loaderData.home.data.res out of the embedded ICE context.

    Raises SelectorDriftError if the page IS a detail page (anchor present) but its
    structure changed; ProductNotFoundError if no embedded context at all.
    """
    anchor_seen = False
    for anchor in ICE_ANCHORS:
        start = 0
        while True:  # scan EVERY occurrence — a decoy anchor may precede the real data
            i = html.find(anchor, start)
            if i < 0:
                break
            anchor_seen = True
            start = i + len(anchor)
            raw = _balanced_object(html, i + len(anchor) - 1)
            if not raw:
                continue
            try:
                b = json.loads(raw)
            except Exception:
                continue
            loader = b.get("loaderData") if isinstance(b, dict) else None
            if not isinstance(loader, dict):
                continue
            # canonical path, then any loaderData child containing res.skuBase
            candidates = [loader.get("home")] + list(loader.values())
            for child in candidates:
                res = (child or {}).get("data", {}).get("res") if isinstance(child, dict) else None
                if isinstance(res, dict) and RES_SKU_BASE_KEY in res:
                    return res
    if anchor_seen:
        raise SelectorDriftError(step="extract_ice_res", selector="loaderData.home.data.res")
    raise ProductNotFoundError("could not locate embedded product data (skuBase) in page HTML")


# ---- the join (Appendix A.1) ----------------------------------------------

def _price_from_info(info: dict) -> float | None:
    price = info.get("price") or {}
    pm = price.get("priceMoney")
    if pm not in (None, ""):
        try:
            return round(float(pm) / 100.0, 2)   # priceMoney is in fen
        except (TypeError, ValueError):
            pass
    pt = price.get("priceText")
    if pt not in (None, ""):
        # priceText drifts: "420", "¥420", "420.00起", "420-450" — take the first number.
        m = re.search(r"\d+(?:\.\d+)?", str(pt).replace(",", ""))
        if m:
            try:
                return float(m.group())
            except ValueError:
                pass
    return None


def _pidvid_lookup(sku_base: dict) -> tuple[dict[str, tuple[str, str]], list[int]]:
    """Build {'pid:vid' -> (groupName, valueName)} and the per-group value counts."""
    lookup: dict[str, tuple[str, str]] = {}
    group_sizes: list[int] = []
    for g in sku_base.get("props", []) or []:
        pid = str(g.get("pid"))
        gname = g.get("name") or pid
        values = g.get("values", []) or []
        group_sizes.append(len(values))
        for v in values:
            lookup[f"{pid}:{v.get('vid')}"] = (gname, v.get("name") or str(v.get("vid")))
    return lookup, group_sizes


def _stock_and_soldout(info: dict) -> tuple[int | None, bool]:
    qty_raw = info.get("quantity")
    try:
        stock = int(qty_raw) if qty_raw is not None else None
    except (TypeError, ValueError):
        stock = None
    qty_text = info.get("quantityText") or ""
    sold_out = any(t in qty_text for t in ("无货", "缺货", "售罄")) or (stock == 0)
    return stock, sold_out


def _variant_from_info(sku_id: str, props: dict[str, str], info: dict) -> SkuVariant:
    price = _price_from_info(info)
    if price is not None and price <= 0:   # M2: ¥0 is a placeholder, not a real price
        price = None
    stock, sold_out = _stock_and_soldout(info)
    if sold_out:
        price = None
    return SkuVariant(sku_id=sku_id, properties=props, price=price, stock=stock, available=price is not None)


def build_variants(sku_base: dict, sku2info: dict) -> list[SkuVariant]:
    """Join skuBase (props+skus) with skuCore.sku2info into one priced SkuVariant per sku.

    Produces a variant for EVERY entry in skuBase.skus. If a product has no SKU
    matrix (skus empty — very common for simple items), synthesizes ONE default
    variant from sku2info so the headline price is never lost (C1). Raises
    SkuIncompleteError if a real sku is dropped (a join bug).
    """
    lookup, _ = _pidvid_lookup(sku_base)
    skus = sku_base.get("skus", []) or []

    if not skus:  # C1: single-SKU / no-matrix product — emit the default headline variant
        if not sku2info:
            return []
        default = sku2info.get("0") or next(iter(sku2info.values()), {}) or {}
        return [_variant_from_info("0", {}, default)]

    variants: list[SkuVariant] = []
    for sku in skus:
        sku_id = str(sku.get("skuId"))
        props: dict[str, str] = {}
        for pair in (sku.get("propPath", "") or "").split(";"):
            pair = pair.strip()
            if not pair:
                continue
            mapped = lookup.get(pair)
            if mapped is None:
                continue  # H5: unknown pid:vid (stale cache) — skip, never emit a raw token
            gname, vname = mapped
            props[gname] = vname
        variants.append(_variant_from_info(sku_id, props, sku2info.get(sku_id, {}) or {}))

    if len(variants) != len(skus):
        raise SkuIncompleteError(expected=len(skus), got=len(variants))
    return variants


def cartesian_count(sku_base: dict) -> int:
    """Product of per-group value counts (the 'should-have' combo count)."""
    _, sizes = _pidvid_lookup(sku_base)
    total = 1
    for s in sizes:
        total *= s if s else 1
    return total if sizes else 0


# ---- assembling the Product ------------------------------------------------

def parse_product_html(html: str, product_id: str, url: str = "") -> Product:
    """Parse a saved/rendered detail page into a fully-populated Product."""
    return parse_product_res(extract_ice_res(html), product_id, url)


def parse_sku_info(sku_info: str) -> str | None:
    """'颜色分类:P100 质保3年 以换代修' → 'P100 质保3年 以换代修'; '颜色:黑;尺寸:L' → '黑 L'."""
    if not sku_info:
        return None
    values = []
    for pair in str(sku_info).split(";"):
        pair = pair.strip()
        if not pair:
            continue
        values.append(pair.split(":", 1)[-1].strip() if ":" in pair else pair)
    return " ".join(v for v in values if v) or None


def extract_specs(res: dict) -> dict[str, str]:
    """参数 table from componentsVO.extensionInfoVO.infos (type BASE_PROPS) → {title: value}."""
    specs: dict[str, str] = {}
    infos = ((res.get("componentsVO", {}) or {}).get("extensionInfoVO", {}) or {}).get("infos", []) or []
    for block in infos:
        if not isinstance(block, dict) or block.get("type") != "BASE_PROPS":
            continue
        for item in block.get("items", []) or []:
            title, text = item.get("title"), item.get("text")
            if not title:
                continue
            value = " / ".join(text) if isinstance(text, list) else str(text or "")
            if value:
                specs[str(title)] = value
    return specs


def extract_embedded_reviews(res: dict) -> list[Review]:
    """Reviews already embedded in componentsVO.rateVO.group.items (no extra navigation).

    Each item carries content + skuInfo ('颜色分类:<label>', the FULL variant string,
    so linkage is clean) + media (presence → has_images) + dateTime.
    """
    items = (((res.get("componentsVO", {}) or {}).get("rateVO", {}) or {})
             .get("group", {}) or {}).get("items", []) or []
    reviews: list[Review] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = (it.get("content") or it.get("feedback") or "").strip()
        if not text:
            continue
        reviews.append(Review(
            rating=None,
            text=text,
            has_images=bool(it.get("media") or it.get("pics") or it.get("photos")),
            sku_bought=parse_sku_info(it.get("skuInfo") or it.get("auctionSku") or ""),
            date=it.get("dateTime") or it.get("feedbackDate"),
        ))
    return reviews


def embedded_review_total(res: dict):
    """The listing's stated total review count + favorable-rate text (or (None, None))."""
    rate = (res.get("componentsVO", {}) or {}).get("rateVO", {}) or {}
    fav = rate.get("favorableRate")
    fav_text = fav.get("rateText") if isinstance(fav, dict) else fav
    return rate.get("totalCount"), fav_text


def extract_subsidy_caveat(res: dict) -> str | None:
    """Flag the gap when priceVO shows a 平台加补后 (after-subsidy) price below 优惠前.

    Per-SKU prices come from sku2info (the 优惠前 / pre-discount figure). The subsidized
    price is computed per selection and usually needs a mainland ID/address (国补), so it
    may not apply to an overseas buyer — surface it rather than silently mislead.
    """
    pv = (res.get("componentsVO", {}) or {}).get("priceVO", {}) or {}
    extra = pv.get("extraPrice") or {}
    base = pv.get("price") or {}
    after, before = extra.get("priceText"), base.get("priceText")
    if after and before and str(after) != str(before):
        desc = extra.get("priceDesc", "") or ""
        return (
            f"{extra.get('priceTitle', '平台加补后')} ￥{after}{desc} vs {base.get('priceTitle', '优惠前')} ￥{before}. "
            f"Per-SKU figures are the 优惠前 price; with a mainland account + China delivery address the "
            f"platform subsidy (百亿补贴) usually applies, so the after-subsidy price is the realistic cost. "
            f"The government 国补 portion can be ID/quantity-limited (often ~1 per category) — on bulk, units "
            f"beyond the limit pay the platform-subsidized price; confirm at checkout."
        )
    return None


def parse_product_res(res: dict, product_id: str, url: str = "") -> Product:
    """Build a Product from an already-extracted ICE ``res`` dict (test/fixture-friendly).

    Reads variants (skuBase↔sku2info), 参数 specs (componentsVO BASE_PROPS), and the
    preview reviews embedded in componentsVO.rateVO — all from the one HTML, no extra nav.
    """
    from src.extract.reviews import group_by_variant

    sku_base = res.get("skuBase", {}) or {}
    sku2info = (res.get("skuCore", {}) or {}).get("sku2info", {}) or {}
    item = res.get("item", {}) or {}
    seller = res.get("seller", {}) or {}

    variants = build_variants(sku_base, sku2info)
    priced = [v.price for v in variants if v.price is not None]
    price_range = (min(priced), max(priced)) if priced else None
    reviews = extract_embedded_reviews(res)

    return Product(
        product_id=str(product_id),
        url=url or f"https://item.taobao.com/item.htm?id={product_id}",
        title=item.get("title", "") or "",
        shop_name=seller.get("shopName") or seller.get("sellerNick") or "",
        price_range=price_range,
        variants=variants,
        specs=extract_specs(res),
        image_urls=list(item.get("images", []) or []),
        reviews=reviews,
        reviews_by_variant=group_by_variant(reviews),
        qa=[],
        scraped_at=datetime.now(timezone.utc).isoformat(),
        subsidy_caveat=extract_subsidy_caveat(res),
    )


# ---- live entry ------------------------------------------------------------

def _to_product_id(product_url_or_id: str) -> str:
    s = product_url_or_id.strip()
    if s.isdigit():
        return s
    m = re.search(r"[?&]id=(\d{6,})", s)
    if m:
        return m.group(1)
    m = re.search(r"(\d{9,})", s)
    if m:
        return m.group(1)
    raise ProductNotFoundError(product_url_or_id)


async def fill_subsidy_prices(page, product, max_skus: int = 24) -> None:
    """deep_price: click each variant to read its live 平台加补后 (after-subsidy) price.

    Replaces SkuVariant.price with the displayed subsidized price — the realistic cost
    for a mainland account + China address. Skipped when a product has > max_skus variants
    (too many clicks → slow + flag risk). Multi-group: selects one value per group; uses
    exact text match to hit the real chip (not the spec table).
    """
    from src.browser.pacing import human_delay
    from src.extract.selectors import SUBSIDY_PRICE_JS

    variants = product.variants
    if not variants or len(variants) > max_skus:
        return

    got_any = False
    for v in variants:
        ok = True
        for value in v.properties.values():
            try:
                loc = page.get_by_text(value, exact=True).first
                if await loc.count() == 0:
                    loc = page.get_by_text(value[:14], exact=False).first  # 推荐-badge fallback
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=4000)
            except Exception:
                ok = False
                break
            await human_delay(0.6, 1.2)
        if not ok:
            continue
        await human_delay(0.8, 1.4)
        try:
            shown = await page.evaluate(SUBSIDY_PRICE_JS)
        except Exception:
            shown = None
        if shown:
            try:
                v.price = float(shown)
                got_any = True
            except (TypeError, ValueError):
                pass

    if got_any:
        priced = [x.price for x in variants if x.price is not None]
        if priced:
            product.price_range = (min(priced), max(priced))
        product.subsidy_caveat = (
            "Per-SKU prices are the live 平台加补后 (after-subsidy) figures. The government 国补 "
            "portion can be ID/quantity-limited (often ~1 per category), so on bulk the units "
            "beyond the limit pay the platform-subsidized price."
        )


async def parse_product(
    product_url_or_id: str,
    deep_reviews: bool = False,
    deep_price: bool = False,
    review_max: int | None = None,
) -> Product:
    """Live: navigate to the product (logged in, paced, captcha-guarded) and parse it.

    Variants, 参数 specs, and preview reviews all come from the embedded HTML in a SINGLE
    navigation. deep_price=True additionally clicks each variant to read its live
    平台加补后 (after-subsidy) price. deep_reviews=True crawls the full review drawer
    (re-navigates). Both opt-in; CaptchaError propagates so a wall is never hidden.
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_scroll(page, 3)
    await human_delay(1.5, 3.0)
    product = parse_product_html(await page.content(), pid, url)

    if deep_price:  # click each variant for its live after-subsidy price (before any re-nav)
        await fill_subsidy_prices(page, product)

    if deep_reviews:  # opt-in deep crawl; re-navigates. CaptchaError/SelectorDriftError propagate
        from src.extract.reviews import group_by_variant, parse_reviews

        reviews = await parse_reviews(pid, max_reviews=review_max)
        if reviews:
            product.reviews = reviews
            product.reviews_by_variant = group_by_variant(reviews)

    return product
