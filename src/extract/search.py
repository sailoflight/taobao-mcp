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
    )


def parse_cards(raw: list[dict]) -> list[SearchResult]:
    return [parse_card_text(r["id"], r.get("text", "")) for r in raw if r.get("id")]


async def parse_search(keyword: str, page_num: int = 1, filters: dict | None = None) -> list[SearchResult]:
    """Live: search Taobao for `keyword` and return the result rows (paced, captcha-guarded)."""
    from urllib.parse import quote

    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    url = f"https://s.taobao.com/search?q={quote(keyword)}&page={page_num}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    for _ in range(3):
        await human_scroll(page, 3)
        await human_delay(1.0, 2.0)
    raw = await page.evaluate(EXTRACT_JS)
    return parse_cards(raw)
