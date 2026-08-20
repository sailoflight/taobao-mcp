"""Search-results parser (GREENFIELD — base repo has no search).

s.taobao.com results are pure DOM (no embedded JSON blob, confirmed). The live
path collects item anchors and climbs to the SMALLEST ancestor whose text holds
both ¥ and 付款 (< ~260 chars) to isolate one card (CLAUDE.md Appendix B.2), then
parse_card_text() turns that card's text into a SearchResult. The text parser is
pure and unit-tested. Respect pacing; default to page 1 only unless asked.
"""

from __future__ import annotations

import asyncio
import re
import time

from src.extract.selectors import SEARCH_EXTRACT_JS as EXTRACT_JS  # centralized (Phase 6)
from src.models import SearchResult

# 搜索间冷却: 全局(跨工具调用)上一次搜索开始时刻。直接 URL 连搜是滑块第一触发源
# (实测 2026-08-20: 27ms 内连发两次搜索立即触发滑块), 因此两次 taobao_search
# 之间强制至少 anti_risk.search_cooldown_s 秒, 用真人不可能达到的爆发换降级。
_last_search_at: float = 0.0

# 拟人化导航: 淘宝首页/任意页顶部搜索框的候选选择器(优先命中即用)。
_SEARCH_BOX_SELECTORS = (
    "#q",                            # 淘宝 PC 经典搜索框
    "input[name='q']",
    ".search-combobox-input input",
    "input[placeholder*='搜索']",
    "input[type='search']",
)

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


async def _enforce_search_cooldown() -> None:
    """全局搜索间冷却: 距上一次 taobao_search 不足 anti_risk.search_cooldown_s 秒则等待。

    直接 URL 连搜(上一请求刚 loaded 27ms 后立即 goto 下一个搜索 URL)是滑块
    验证码的第一触发源(2026-08-20 实测连续触发)。冷却跨工具调用生效 —— 即使
    batch 里多个搜索 op 连发, 也会被强制拉开到真人节奏。
    """
    global _last_search_at
    from src.config import load_config
    from src.log import get_logger

    cooldown = float(getattr(load_config().anti_risk, "search_cooldown_s", 0) or 0)
    if cooldown <= 0:
        _last_search_at = time.monotonic()
        return
    now = time.monotonic()
    if _last_search_at:
        wait = cooldown - (now - _last_search_at)
        if wait > 0:
            get_logger().info("search: cooldown %.0fs before next search (interval %.0fs)", wait, cooldown)
            await asyncio.sleep(wait)
    _last_search_at = time.monotonic()


async def _goto_search_page(page, url: str, keyword: str):
    """拟人化导航到搜索结果页, 返回最终应解析的工作页(单标签维护)。

    不用「直接 goto s.taobao.com/search?q=...」的爬虫式跳转(那是风控第一特征),
    而是复用页面顶部搜索框输入关键词回车 —— 同一个人浏览器里, 从搜索框提交搜索
    才是正常人类路径。2026-08-20 实测: 首页搜索框回车会把结果开在**新标签页**。
    本函数处理三件事:
    - 接管新开的结果页, 并**关闭旧标签页**(只留结果页 — 避免标签堆积, 也避免
      每次搜索都触发 Edge 新标签闪烁, 与 guard 的人工提醒混淆);
    - 记录提交前的 URL, 只认「真的变化了」的结果页 URL — 避免在同一个结果页
      顶部搜索框反复提交同词(SPA 可能不刷新, URL 不变, 拿到旧数据);
    - 已在搜索结果页时先回首页再搜, 保证每次都是全新搜索。
    找不到搜索框 / 提交 30s 无结果页时退回直接 goto(记 warning, 不静默)。
    """
    from src.browser.pacing import human_delay
    from src.browser.session import get_session
    from src.log import get_logger

    session = get_session()
    # 已在淘宝域内且已是搜索结果页 → 回首页再搜(全新搜索); 不在淘宝域 → 回首页。
    if ("s.taobao.com/search" in page.url) or not any(d in page.url for d in ("taobao.com", "tmall.com")):
        try:
            await page.goto("https://www.taobao.com", wait_until="domcontentloaded")
            await session.guard_captcha(page)
            await human_delay(1.5, 3.0)
        except Exception as exc:
            get_logger().warning("search: goto taobao home failed (%s) — falling through to direct URL", exc)
    box = None
    for sel in _SEARCH_BOX_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                box = loc
                break
        except Exception:
            continue
    if box is not None:
        try:
            await box.click(timeout=5_000)
            await box.fill(keyword)
            await human_delay(0.4, 1.0)
            start_url = page.url
            await page.keyboard.press("Enter")
            # 提交后: 结果可能在当前标签页, 也可能开在新标签页(2026-08-20 实测)。
            # 每轮都跑 guard(新页可能同现滑块+选图, 等待人工通过); 只认 URL 真的
            # 变成了搜索结果页(≠start_url, 防止同页反复搜索拿旧数据)。
            result_page = page
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                await session.guard_captcha(page)  # 覆盖所有标签页×所有frame
                found = None
                for p in session._candidate_pages(page):
                    u = p.url or ""
                    if ("s.taobao.com/search" in u or "s.tmall.com/search" in u) and u != start_url:
                        found = p
                        break
                if found is not None:
                    if found is not page:
                        get_logger().info("search: adopted new-tab results page %s (closing old tab)", found.url)
                        session.page = found  # 先切工作页, 再关旧标签
                        try:
                            await page.close()  # 关闭旧标签: 不堆积, 少闪烁
                        except Exception:
                            pass
                        result_page = found
                    else:
                        get_logger().info("search: current tab navigated to results %s", found.url)
                    return result_page
                await asyncio.sleep(2.0)
            get_logger().warning("search: no NEW results URL after submit (url=%s) — direct-URL fallback", page.url)
            await page.goto(url, wait_until="domcontentloaded")
            return page
        except Exception as exc:
            get_logger().warning("search: search-box submission failed (%s) — falling back to direct URL", exc)
    # 退回: 直接 URL(历史路径, 保底)
    get_logger().warning("search: no usable search box — using direct URL %s", url)
    await page.goto(url, wait_until="domcontentloaded")
    return page


async def parse_search(keyword: str, page_num: int = 1, filters: dict | None = None) -> list[SearchResult]:
    """Live: search Taobao for `keyword` and return the result rows (paced, captcha-guarded)."""
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.log import get_logger

    await _enforce_search_cooldown()
    session = get_session()
    page = await session.start()
    # New SPA normalizes the URL to ...&tab=all&page=N; include tab=all up
    # front and fall back to clicking the pagination 下一页 button when the
    # SPA still lands on page 1 (observed 2026-08-18: requested page=2 was
    # rewritten by the page to page=1).
    url = build_search_url(keyword, page_num, filters)
    get_logger().info("search: requested page=%s url=%s", page_num, url)
    page = await _goto_search_page(page, url, keyword)
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
