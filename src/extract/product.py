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
import time
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


def _pidvid_lookup(sku_base: dict) -> tuple[dict[str, dict], list[int]]:
    """Build {'pid:vid' -> {'name': 档位名, 'image': 选项图URL}} and per-group value counts.

    Image (``v.get("image")``) is the SKU option thumbnail — the 尺寸/规格 chart is
    usually printed on it, not in the variant text. Keeping it lets downstream
    (and Claude) read the option image instead of guessing from the label.
    """
    lookup: dict[str, dict] = {}
    group_sizes: list[int] = []
    for g in sku_base.get("props", []) or []:
        pid = str(g.get("pid"))
        gname = g.get("name") or pid
        values = g.get("values", []) or []
        group_sizes.append(len(values))
        for v in values:
            key = f"{pid}:{v.get('vid')}"
            lookup[key] = {
                "group": gname,
                "name": v.get("name") or str(v.get("vid")),
                "image": v.get("image") or v.get("img") or None,
            }
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


def _variant_from_info(sku_id: str, props: dict[str, str], info: dict, image: str | None = None) -> SkuVariant:
    price = _price_from_info(info)
    if price is not None and price <= 0:   # M2: ¥0 is a placeholder, not a real price
        price = None
    stock, sold_out = _stock_and_soldout(info)
    if sold_out:
        price = None
    return SkuVariant(sku_id=sku_id, properties=props, price=price, stock=stock,
                      available=price is not None, image=image)


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
        image: str | None = None
        for pair in (sku.get("propPath", "") or "").split(";"):
            pair = pair.strip()
            if not pair:
                continue
            mapped = lookup.get(pair)
            if mapped is None:
                continue  # H5: unknown pid:vid (stale cache) — skip, never emit a raw token
            props[mapped["group"]] = mapped["name"]
            if mapped.get("image") and not image:
                image = mapped["image"]  # 取该档位选项图(尺寸/规格常印其上)
        variants.append(_variant_from_info(sku_id, props, sku2info.get(sku_id, {}) or {}, image=image))

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
    rt, fr = embedded_review_total(res)

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
        review_total=str(rt) if rt else None,
        favorable_rate=str(fr) if fr else None,
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


_DEEP_PRICE_TIME_BUDGET_S = 40.0
_DEEP_PRICE_ESTIMATED_PER_SKU_S = 2.5


def _append_subsidy_note(product: Product, note: str) -> None:
    product.subsidy_caveat = " ".join(
        part for part in (product.subsidy_caveat, note) if part
    )


async def fill_subsidy_prices(page, product, max_skus: int = 24) -> None:
    """deep_price: click each variant to read its live 平台加补后 (after-subsidy) price.

    Budget-limited so a many-SKU listing can never blow the MCP tool timeout:
    - more than `max_skus` clickable variants → skip clicks and say so;
    - otherwise stop when ~40s of click budget is spent and mark the result as
      partial (the caller still gets every SKU; unclicked rows keep the embedded
      优惠前 price).
    """
    from src.browser.pacing import human_delay
    from src.extract.selectors import SUBSIDY_PRICE_JS

    variants = product.variants
    if not variants:
        return

    clickable = [v for v in variants if v.available and v.price is not None]
    if len(clickable) > max_skus:
        _append_subsidy_note(
            product,
            f"deep_price skipped: {len(clickable)} clickable variants > safe budget "
            f"({max_skus}); per-SKU prices shown are the embedded 优惠前 figures.",
        )
        return

    deadline = time.monotonic() + _DEEP_PRICE_TIME_BUDGET_S
    got_any = False
    processed = 0
    for v in variants:
        if time.monotonic() + _DEEP_PRICE_ESTIMATED_PER_SKU_S > deadline:
            break
        if not (v.available and v.price is not None):
            continue

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
        processed += 1
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

    if processed < len(clickable):
        _append_subsidy_note(
            product,
            f"deep_price partial: updated {processed}/{len(clickable)} variants within "
            f"{_DEEP_PRICE_TIME_BUDGET_S:.0f}s; remaining rows keep the embedded 优惠前 price.",
        )

    if got_any:
        priced = [x.price for x in variants if x.price is not None]
        if priced:
            product.price_range = (min(priced), max(priced))
        _append_subsidy_note(
            product,
            "Deep-priced rows are the live 平台加补后 figures; 国补 can be "
            "ID/quantity-limited (often ~1 per category), so bulk units beyond the limit pay "
            "the platform-subsidized price.",
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
    # URL 诊断(2026-08-20 用户疑点): 粗查 goto 的 item.htm 是否被淘宝注入 mi_id?
    # 用 p.url 记录实际落地 URL — 判断"URL 拼接 vs 模拟点击"的语义差异。
    try:
        from src.extract.miid import miid_from_url

        product.miid_present = bool(miid_from_url(page.url or ""))
        product.landed_url = (page.url or "")[:220]
    except Exception:
        pass

    if deep_price:  # click each variant for its live after-subsidy price (before any re-nav)
        await fill_subsidy_prices(page, product)

    if deep_reviews:  # opt-in deep crawl; re-navigates. CaptchaError/SelectorDriftError propagate
        from src.extract.reviews import group_by_variant, parse_reviews

        reviews = await parse_reviews(pid, max_reviews=review_max)
        if reviews:
            product.reviews = reviews
            product.reviews_by_variant = group_by_variant(reviews)

    return product


def _unit_price(v) -> float | None:
    """Pure: 型号标签含 'N个装' 时算每件单价(共享 helper, 防漂移)."""
    from src.extract.units import unit_price_from_label

    return unit_price_from_label("; ".join((v.properties or {}).values()), v.price)


def _product_markdown(p) -> str:
    """Pure: 把 Product 渲染成可读 markdown(标题/店铺/价区间 + 全部型号价表).

    买家一屏看全所有型号价格/库存, 比 JSON 直观。只读数据渲染, 不发消息。
    """
    lines = [f"### {p.title or ''}", ""]
    meta = []
    if p.shop_name:
        meta.append(f"店铺: {p.shop_name}")
    if p.price_range:
        lo, hi = p.price_range
        meta.append(f"价区间: ¥{lo:g}–¥{hi:g}" if hi != lo else f"价: ¥{lo:g}")
    meta.append(f"型号数: {len(p.variants)}")
    rt = getattr(p, "review_total", None)
    fr = getattr(p, "favorable_rate", None)
    if rt:
        meta.append(f"总评价: {rt}" + (f" ({fr})" if fr else ""))
    elif p.reviews:
        meta.append(f"评论: {len(p.reviews)} 条")
    lines.append(" | ".join(meta))
    if p.subsidy_caveat:
        lines.append(f"⚠️ 补贴提示: {p.subsidy_caveat}")
    # 参数表(材质/尺寸/密封等) — 买家不翻 JSON 直接看关键规格
    specs = getattr(p, "specs", None) or {}
    if specs:
        lines.append("")
        lines.append("| 参数 | 值 |\n|---|---|")
        for k, v in list(specs.items())[:15]:
            lines.append(f"| {k} | {str(v)[:40]} |")
    # 最便宜有货型号高亮 + Top3(买家一屏看到最优选择)
    avail = [v for v in p.variants if v.available and v.price is not None]
    if avail:
        best = min(avail, key=lambda v: v.price)
        bl = "; ".join(f"{k}:{val}" for k, val in (best.properties or {}).items())
        lines.append(f"🟢 最便宜有货: {bl or '-'} → ¥{best.price:g}")
        top = sorted(avail, key=lambda v: v.price)[:3]
        lines.append("💰 最便宜有货 Top3:")
        for v in top:
            tl = "; ".join(f"{k}:{val}" for k, val in (v.properties or {}).items())
            lines.append(f"- {tl or '-'} → ¥{v.price:g}")
    lines.append("")
    if p.variants:
        lines.append("| 型号 | 价格¥ | 单价¥ | 库存 | 有货 |\n|---|---|---|---|---|")
        for v in p.variants[:200]:
            props = "; ".join(f"{k}:{val}" for k, val in (v.properties or {}).items())
            price = f"{v.price:g}" if v.price is not None else "-"
            unit = _unit_price(v)
            unit_cell = f"{unit:.2f}" if unit is not None else "-"
            stock = v.stock if v.stock is not None else "-"
            ok = "✓" if v.available else "✗"
            lines.append(f"| {props or '-'} | {price} | {unit_cell} | {stock} | {ok} |")
        if len(p.variants) > 200:
            lines.append(f"| … 共 {len(p.variants)} 个型号(前 200 显示) |")
    return "\n".join(lines)


async def export_product_markdown(product_url_or_id: str, filename: str = "", title: str = "",
                                   with_reviews: bool = False,
                                   out_dir: str | None = None) -> dict:
    """只读: 抓单个商品并渲染成 markdown 落盘 output/product_<pid>.md(买家留档单商品全貌).

    复用 parse_product(只读浏览) + _product_markdown; 返回 {path, product_id, markdown}。
    filename 可选(自定义文件名, 默认 product_<pid>_<ts>.md); title 可选(自定义标题);
    with_reviews=True 时追加嵌入式评论(如有)。
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from src.config import load_config

    pid = _to_product_id(product_url_or_id)
    p = await parse_product(pid)
    md = _product_markdown(p)
    if with_reviews:
        revs = list(getattr(p, "reviews", None) or [])
        if revs:
            md += "\n\n## 评论(嵌入式, 有限)\n\n"
            for r in revs:
                sku = getattr(r, "sku_bought", "") or ""
                md += f"- {getattr(r, 'date', '') or ''} · {(r.text or '')[:80]}\n"
                if sku:
                    md += f"  (购 {sku[:40]})\n"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(out_dir) if out_dir else Path(load_config().output.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    from src.config import safe_filename
    path = out_dir / safe_filename(filename, f"product_{pid}_{ts}.md")
    head = f"> 导出时间: {ts}"
    if title:
        head += f" — {title}"
    path.write_text(head + "\n\n" + md + "\n", encoding="utf-8")
    return {"path": str(path), "product_id": pid, "markdown": head + "\n\n" + md}
