"""Search-results parser (GREENFIELD — base repo has no search).

s.taobao.com results are pure DOM (no embedded JSON blob, confirmed). The live
path collects item anchors and climbs to the SMALLEST ancestor whose text holds
both ¥ and 付款 (< ~260 chars) to isolate one card (CLAUDE.md Appendix B.2), then
parse_card_text() turns that card's text into a SearchResult. The text parser is
pure and unit-tested. Respect pacing; default to page 1 only unless asked.
"""

from __future__ import annotations

import re

from src.extract.selectors import SEARCH_EXTRACT_JS as EXTRACT_JS  # centralized (Phase 6)
from src.models import SearchResult

_PRICE_RE = re.compile(r"¥\s*([\d,]+(?:\.\d+)?)")            # allow thousands commas (¥1,299)
_SALES_RE = re.compile(r"([\d.]+万?)\s*\+?\s*(?:人付款|人付|付款|人收货|收货)")
_SALES_RE2 = re.compile(r"(?:已售|月销|成交)\s*([\d.]+万?)")   # 已售2000+ / 月销1000
_SHIP_TOKENS = ("包邮", "公益宝贝", "退货宝", "48小时内发", "24小时内发", "极速退款", "补贴后", "优惠前", "包退")
# 卡片内规格/尺寸片段: 规格:xxx / 尺寸:xxx / 规格 xxx, 取到下一个 ¥ 或促销词前。
_SPEC_RE = re.compile(r"(?:规格|尺寸|尺码|规格参数|参数)\s*[:：]?\s*([^¥\n]{2,60})")
_PROMO_RE = re.compile(r"满\d+减\d+|立减|直降|券|补贴|赠")
_CJK_TOKEN = re.compile(r"[一-龥]{2,}")
# A ¥-amount immediately preceded by one of these is a struck-through "优惠前" price or a
# promo discount (直降¥100 / 满减), NOT the sell price.
_SKIP_BEFORE_PRICE = ("优惠前", "直降", "立减", "减", "省", "券", "返")


def _to_count(s: str) -> int | None:
    s = s.strip().replace("+", "")
    mult = 1
    if s.endswith("万"):
        mult = 10000
        s = s[:-1]
    try:
        return int(round(float(s) * mult))
    except ValueError:
        return None


# 裸尺寸模式: "30cmX35cm" / "30*34cm" / "30×34厘米" / "35cmx42cm" — 搜索卡片常裸写尺寸, 无"规格:"前缀
# 兼容两种写法: "30cmX35cm"(中间带 cm) 和 "30*34cm"(纯数字+连接符)
_BARE_SIZE_RE = re.compile(
    r"(\d{2,3}\s*cm?\s*[xX*×]\s*\d{2,3}\s*cm|"
    r"\d{2,3}\s*[xX*×]\s*\d{2,3}\s*(?:cm|厘米))"
)
# 档位词(特大号/大号/中号…) — 尺寸常与档位名连写
_GRADE_RE = re.compile(r"(特大号|加大号|超大号|大号|中号|小号|加厚|特厚)")


def _extract_spec(text: str) -> str | None:
    """Pure: 从卡片文本提取规格/尺寸片段(如 "30*34cm", "特大号30*34厘米").

    优先带 "规格：/尺寸:" 前缀的片段; 否则 fallback 到裸尺寸模式
    ("30cmX35cm" / "30*34cm" — 淘宝卡片常裸写, 无前缀)。供 spec_contains
    过滤, 让"按尺寸圈选"在搜索阶段就能做(不必等 coarse 逐款抓)。
    清理促销噪声, 截断到 40 字。
    """
    for m in _SPEC_RE.finditer(text):           # 1) 带前缀 "规格:xxx"
        frag = m.group(1).strip().rstrip("，。;；,")
        if not frag:
            continue
        frag = re.split(r"品牌|促销|店铺|元|¥", frag, maxsplit=1)[0].strip()
        if frag:
            return frag[:40]
    m = _BARE_SIZE_RE.search(text)              # 2) 裸尺寸 "30cmX35cm" / "30*34cm"
    if m:
        return m.group(1).replace(" ", "")[:40]
    # 3) 档位词(仅在完全没有尺寸时的弱信号 — "加厚/特大号" 在标题常见, 不能抢在裸尺寸前)
    g = _GRADE_RE.search(text)
    if g:
        return g.group(1)
    return None


def parse_card_text(product_id: str, text: str) -> SearchResult:
    """Parse one result card's flattened text into a SearchResult (pure)."""
    # Price = the FIRST ¥-amount that is not a struck-through "优惠前" price or a promo discount.
    matches = list(_PRICE_RE.finditer(text))
    price = None
    price_pos = None
    for m in matches:
        before = text[max(0, m.start() - 6):m.start()]
        if any(tok in before for tok in _SKIP_BEFORE_PRICE):
            continue
        try:
            price = float(m.group(1).replace(",", ""))
            price_pos = m.start()
        except ValueError:
            continue
        break
    if price is None and matches:  # everything looked like 优惠前/promo — fall back to the first ¥
        try:
            price = float(matches[0].group(1).replace(",", ""))
            price_pos = matches[0].start()
        except ValueError:
            price = None

    title = (text[:price_pos] if price_pos is not None else text.split("¥", 1)[0]).strip()

    sm = _SALES_RE.search(text)
    sm2 = _SALES_RE2.search(text)
    sale_m = sm or sm2
    monthly_sales = _to_count(sale_m.group(1)) if sale_m else None

    # Location = leading CJK tokens right after the sales marker, excluding ship/promo.
    location = None
    if sale_m:
        loc_toks: list[str] = []
        for tok in text[sale_m.end():].split():
            if _CJK_TOKEN.fullmatch(tok) and tok not in _SHIP_TOKENS and not _PROMO_RE.search(tok):
                loc_toks.append(tok)
                if len(loc_toks) >= 2:
                    break
            elif loc_toks:
                break
        location = "".join(loc_toks) or None

    # Shop = trailing token, skipping ship/promo/period/hour labels and the location.
    shop_name = None
    for tok in reversed([t for t in text.split() if t]):
        if tok in _SHIP_TOKENS or tok.endswith("期") or "小时" in tok or _PROMO_RE.search(tok) or tok == location:
            continue
        shop_name = tok
        break

    return SearchResult(
        product_id=str(product_id),
        url=f"https://item.taobao.com/item.htm?id={product_id}",
        title=title,
        price=price,
        monthly_sales=monthly_sales,
        shop_name=shop_name,
        location=location,
        spec_text=_extract_spec(text),
    )


def parse_cards(raw: list[dict]) -> list[SearchResult]:
    return [parse_card_text(r["id"], r.get("text", "")) for r in raw if r.get("id")]


def build_search_url(keyword: str, page_num: int = 1, filters: dict | None = None) -> str:
    """Pure: build the s.taobao.com search URL with optional filters.

    filters: min_price/max_price -> filter=reserve_price[MIN,MAX] (Taobao may ignore,
    best-effort); sort -> s=N (1=综合 2=销量 5=价格低→高 6=高→低). Pure (no session),
    unit-tested.
    """
    from urllib.parse import quote

    url = f"https://s.taobao.com/search?q={quote(keyword)}&tab=all&page={page_num}"
    if filters:
        if filters.get("min_price") is not None or filters.get("max_price") is not None:
            lo = filters.get("min_price", "")
            hi = filters.get("max_price", "")
            url += f"&filter=reserve_price[{lo},{hi}]"
        if filters.get("sort"):
            url += f"&s={filters['sort']}"
    return url


def filter_search_results(results: list[SearchResult], filters: dict | None) -> list[SearchResult]:
    """Pure: client-side filter of parsed results (Taobao's URL filters are best-effort).

    Applies, when present in `filters`:
      min_sales / max_sales — monthly_sales band (skips near-zero-sales sketchy listings)
      min_price / max_price — price band (falls back to client-side when the URL param is ignored)
      title_contains — case-insensitive substring required in the title (e.g. "加固")
      spec_contains — substring required in the card's 规格/尺寸片段 (e.g. "30*34")
      sort — client-side re-sort for reliability (SPA 的 s=N 排序偶发不生效):
        5 = 价格从低到高, 6 = 价格从高到低, 2 = 销量从高到低 (缺价/缺销量排最后)
    Items missing the compared field pass through (None is not filtered out).
    """
    if not filters:
        return results
    lo_s = filters.get("min_sales")
    hi_s = filters.get("max_sales")
    lo_p = filters.get("min_price")
    hi_p = filters.get("max_price")
    tc = filters.get("title_contains")
    sc = filters.get("spec_contains")
    sort = filters.get("sort")
    if not any(x is not None for x in (lo_s, hi_s, lo_p, hi_p, tc, sc, sort)):
        return results
    out = []
    for r in results:
        if lo_s is not None and r.monthly_sales is not None and r.monthly_sales < lo_s:
            continue
        if hi_s is not None and r.monthly_sales is not None and r.monthly_sales > hi_s:
            continue
        if lo_p is not None and r.price is not None and r.price < lo_p:
            continue
        if hi_p is not None and r.price is not None and r.price > hi_p:
            continue
        if tc is not None and tc and (tc not in (r.title or "")):
            continue
        if sc is not None and sc and (sc not in (r.spec_text or "")):
            continue
        out.append(r)
    if sort == 5:
        out.sort(key=lambda r: r.price if r.price is not None else float("inf"))
    elif sort == 6:
        out.sort(key=lambda r: r.price if r.price is not None else float("-inf"), reverse=True)
    elif sort == 2:
        out.sort(key=lambda r: r.monthly_sales if r.monthly_sales is not None else -1, reverse=True)
    return out


async def parse_search(keyword: str, page_num: int = 1, filters: dict | None = None) -> list[SearchResult]:
    """Live: search Taobao for `keyword` and return the result rows (paced, captcha-guarded)."""
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.log import get_logger

    session = get_session()
    page = await session.start()
    # New SPA normalizes the URL to ...&tab=all&page=N; include tab=all up
    # front and fall back to clicking the pagination 下一页 button when the
    # SPA still lands on page 1 (observed 2026-08-18: requested page=2 was
    # rewritten by the page to page=1).
    url = build_search_url(keyword, page_num, filters)
    get_logger().info("search: requested page=%s url=%s", page_num, url)
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    for _ in range(3):
        await human_scroll(page, 3)
        await human_delay(1.0, 2.0)

    if page_num > 1:
        # If the SPA rewrote the URL to page=1, use its own pagination widget.
        for target in range(2, page_num + 1):
            current = page.url
            if f"page={target}" in current.replace("%2C", ","):
                break
            # A soft 访问太频繁 popup blocks pointer events — close its X first.
            await session.dismiss_frequency_dialog(page)
            try:
                next_btn = page.locator("button.next-pagination-item.next-next").first
                await next_btn.click(timeout=5_000)
                get_logger().info("search: clicked 下一页 toward page=%s url=%s", target, page.url)
                await human_delay(1.0, 2.0)
                await page.wait_for_load_state("domcontentloaded")
                for _ in range(2):
                    await human_scroll(page, 3)
                    await human_delay(0.5, 1.0)
            except Exception as exc:
                get_logger().warning("search: pagination click failed for page=%s: %s", target, exc)
                # Popup may have appeared between the dismiss and the click.
                if await session.dismiss_frequency_dialog(page):
                    try:
                        await page.locator("button.next-pagination-item.next-next").first.click(timeout=5_000)
                        get_logger().info("search: retried 下一页 toward page=%s url=%s", target, page.url)
                        await human_delay(1.0, 2.0)
                        await page.wait_for_load_state("domcontentloaded")
                        for _ in range(2):
                            await human_scroll(page, 3)
                            await human_delay(0.5, 1.0)
                    except Exception as retry_exc:
                        get_logger().warning("search: pagination retry failed for page=%s: %s", target, retry_exc)
                        break
                else:
                    break
    else:
        get_logger().info("search: loaded page=%s url=%s", page_num, page.url)

    raw = await page.evaluate(EXTRACT_JS)
    return filter_search_results(parse_cards(raw), filters)


def _search_markdown(results, keyword: str = "", max_rows: int = 30, page: int | None = None) -> str:
    """Pure: 把搜索结果渲染成可读 markdown 表(价格/销量/店铺/位置/标题)."""
    rows = list(results)[:max(1, min(int(max_rows or 30), 100))]
    title = f"### 搜索结果({len(rows)} 个)"
    if keyword:
        title += f" — {keyword}"
    if page:
        title += f" (第 {page} 页)"
    head = [title, "",
            "| 价格¥ | 销量 | 店铺 | 位置 | 规格/尺寸 | 商品 |",
            "|---|---|---|---|---|---|"]
    for r in rows:
        p = f"{r.price:g}" if r.price is not None else "-"
        s = str(r.monthly_sales) if r.monthly_sales is not None else "-"
        sp = (r.spec_text or "").replace("|", "/")[:24]
        title = (r.title or "").replace("|", "/")[:36]
        if r.url:
            title = f"[{title}]({r.url})"  # 买家可直接点开商品
        head.append(f"| {p} | {s} | {(r.shop_name or '')[:12]} | {(r.location or '')[:8]} | {sp or '-'} | {title} |")
    return "\n".join(head)
