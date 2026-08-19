"""Q&A (问大家) parser → list[QAPair] (best-effort DOM extraction).

The base repo read Q&A from ``.askAnswerItem--`` cards. The new page may or may
not render Q&A; this returns [] when absent rather than failing. Keep raw Chinese.
"""

from __future__ import annotations

import re

from src.extract.selectors import QA_EXTRACT_JS as _EXTRACT_JS  # centralized (Phase 6)
from src.models import QAPair


def dicts_to_qa(raw: list[dict]) -> list[QAPair]:
    """raw → list[QAPair], 去重(按问题) + 清洗回答噪声("已购"标签/“更多回答”按钮文本)。

    实证(2026-08-19): mi_id 页问答区与抽屉各渲染一次 → 同一问题出现两次;
    回答文本含 "已购" 前缀(买家已购标签)与 "更多回答" 按钮文本 — 一并清洗。
    """
    seen: set[str] = set()
    out: list[QAPair] = []
    for r in raw:
        q = (r.get("question") or "").strip()
        if not q:
            continue
        key = q.replace("\n", "").strip()[:60]
        if key in seen:
            continue
        seen.add(key)
        a = (r.get("answer") or "").strip()
        a = a.replace("更多回答", "").strip()
        a = re.sub(r"^已购[\s:：]*", "", a).strip()
        out.append(QAPair(question=q, answer=a or None))
    return out


async def parse_qa(product_url_or_id: str, page=None) -> list[QAPair]:
    """Live: extract Q&A pairs from the product page if present (else []).

    page 传已在正确页面(mi_id 详情页)的 Page 时就地抽取 — Tmall 问答条目只在该页渲染;
    None 则开普通页(SSR 无问答, 通常返回 [])。
    实证(2026-08-19): mi_id 页默认仅 2 卡, 点"查看全部问答"展开抽屉 → 11 卡;
    每卡含 .question/.answer(.moreAnswerBtn "更多回答" 可再展多答, 未展)。在此一并展开抽取。
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    if page is None:
        page = await get_session().start()
        if f"id={pid}" not in (page.url or ""):
            await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
    await human_scroll(page, 3)
    await human_delay(1.0, 2.0)
    # 展开"查看全部问答"抽屉(如存在) — 可见 2 卡 → 抽屉 ~11 卡。
    # 先 Esc 关掉可能开着的其它抽屉(评论抽屉), 否则遮住按钮/按钮不可点。
    try:
        await page.keyboard.press("Escape")
        await human_delay(0.6, 1.0)
    except Exception:
        pass
    # 只用问答区专属展开标签(避免误点通用"查看更多"等其它按钮);
    # 无展开按钮(产品无问答区/已全显)则跳过 → 取可见卡或 []。
    for lbl in ("查看全部问答", "全部问答", "更多问答"):
        try:
            loc = page.get_by_text(lbl, exact=False).first
            if await loc.count() > 0:
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=5000)
                await human_delay(2.0, 3.0)
                break
        except Exception:
            continue
    try:
        raw = await page.evaluate(_EXTRACT_JS)
    except Exception:
        raw = []
    # 关掉问答抽屉(如有), 避免遮挡后续(评论)抽取。
    try:
        await page.keyboard.press("Escape")
        await human_delay(0.6, 1.0)
    except Exception:
        pass
    return dicts_to_qa(raw)


async def probe_qa_expand(product_url_or_id: str) -> dict:
    """[DEBUG] 探究问答(问大家)展开机制: 在 mi_id 页数问答卡, 点"查看全部问答",
    报告是否开新页/更多卡/抽屉 — 决定能否一次取全问答(含每题多答)。
    双机制取 mi_id 页(足迹→收藏兜底)。
    """
    from src.browser.pacing import human_delay
    from src.browser.session import get_session
    from src.extract.favorite import (
        click_from_favorites,
        ensure_favorited,
        ensure_unfavorited,
        open_via_footmark,
    )
    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    out: dict = {"product_id": pid}
    popup = None
    res = await open_via_footmark(page, pid)
    if res.get("url") and res.get("matches_target") and res.get("popup"):
        out["miid_channel"] = "footmark"
        popup = res.get("popup")
    else:
        out["footmark_fallback"] = res.get("reason")
        try:  # 兜底收藏渠道
            fav = await ensure_favorited(page, pid)
            added = bool(fav.get("added_by_us"))
            fres = await click_from_favorites(page, pid, added_by_us=added)
            if fres.get("mi_id") and fres.get("matches_target") and fres.get("popup"):
                out["miid_channel"] = "favorite"
                popup = fres.get("popup")
                if added:
                    try:
                        await ensure_unfavorited(page, pid)
                    except Exception:
                        pass
        except Exception as exc:
            out["favorite_error"] = str(exc)[:120]
    if not popup:
        out["miid_error"] = "footmark 与收藏兜底均未取到 mi_id 页"
        return out
    out["miid_url"] = (popup.url or "")[:160]
    qa_sel = '[class*="askAnswerItem"], [class*="qaItem"], [class*="QA"]'

    async def qa_count(p):
        try:
            return await p.locator(qa_sel).count()
        except Exception:
            return -1

    out["before_cards"] = await qa_count(popup)
    try:
        first = popup.locator(qa_sel).first
        out["first_html"] = (await first.evaluate("el => el.outerHTML") or "")[:1400]
    except Exception:
        out["first_html"] = None
    clicked = None
    for lbl in ("查看全部问答", "全部问答", "查看更多", "更多问答"):
        try:
            loc = popup.get_by_text(lbl, exact=False).first
            if await loc.count() > 0:
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=5000)
                clicked = lbl
                break
        except Exception:
            continue
    out["clicked"] = clicked
    await human_delay(3.0, 4.0)
    ctx = session.context
    out["pages_after"] = [{"url": (p.url or "")[:150]} for p in (ctx.pages if ctx else [])]
    out["after_cards"] = await qa_count(popup)
    try:
        out["drawer_or_modal"] = await popup.locator('[class*="Drawer"], [class*="modal"], [class*="Modal"]').count()
    except Exception:
        out["drawer_or_modal"] = None
    for p in (ctx.pages if ctx else []):
        u = (p.url or "").lower()
        if p is not popup and ("ask" in u or "wenda" in u or "qa" in u):
            try:
                out["new_page_cards"] = await qa_count(p)
                out["new_page_url"] = (p.url or "")[:160]
            except Exception:
                pass
    try:
        await popup.close()
    except Exception:
        pass
    return out
