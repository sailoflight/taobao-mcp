"""Q&A (问大家) parser → list[QAPair] (best-effort DOM extraction).

The base repo read Q&A from ``.askAnswerItem--`` cards. The new page may or may
not render Q&A; this returns [] when absent rather than failing. Keep raw Chinese.
"""

from __future__ import annotations

from src.extract.selectors import QA_EXTRACT_JS as _EXTRACT_JS  # centralized (Phase 6)
from src.models import QAPair


def dicts_to_qa(raw: list[dict]) -> list[QAPair]:
    return [QAPair(question=r.get("question", "").strip(), answer=(r.get("answer") or "").strip() or None)
            for r in raw if r.get("question")]


async def parse_qa(product_url_or_id: str, page=None) -> list[QAPair]:
    """Live: extract Q&A pairs from the product page if present (else []).

    page 传已在正确页面(mi_id 详情页)的 Page 时就地抽取 — Tmall 问答条目只在该页渲染;
    None 则开普通页(SSR 无问答, 通常返回 [])。
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
    try:
        raw = await page.evaluate(_EXTRACT_JS)
    except Exception:
        raw = []
    return dicts_to_qa(raw)
