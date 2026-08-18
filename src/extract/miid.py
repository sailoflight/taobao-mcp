"""mi_id acquisition / refresh — a human click generates a fresh one (LOW RISK).

Why this exists: the 详情 SSR renders only when the item URL carries the account's
marketing ``mi_id`` (see NOTES.md 2026-08-18). A stale or repeatedly-reused mi_id
is both a risk-control footprint (every request carries the same marketing token)
and fragile (Taobao can rotate/revoke it). The low-risk renewal is to have a HUMAN
click a product from search results like a real shopper — the click-generated
tracking URL carries a fresh mi_id, which we capture and persist.

`get_miid` opens a search page and polls the tab URL for up to `watch_seconds`
while the human clicks a product card. On success the mi_id is written to
`output/.miid.json` (gitignored) and `load_config()` picks it up at runtime.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_MIID_STATE = Path("output") / ".miid.json"


def miid_from_url(u: str) -> str | None:
    """Extract mi_id from a click-generated URL. Requires a product page (item.htm)."""
    if not u or "item.htm" not in u:
        return None
    qs = parse_qs(urlparse(u).query)
    v = qs.get("mi_id") or qs.get("miid")
    if not v:
        return None
    val = v[0].strip()
    return val or None


def load_persisted_miid() -> str | None:
    try:
        d = json.loads(_MIID_STATE.read_text(encoding="utf-8"))
        return (d.get("mi_id") or "").strip() or None
    except Exception:
        return None


def persist_miid(miid: str) -> Path:
    _MIID_STATE.parent.mkdir(parents=True, exist_ok=True)
    _MIID_STATE.write_text(
        json.dumps(
            {"mi_id": miid, "updated": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return _MIID_STATE


async def recon_home_ads() -> dict:
    """Recon the Taobao homepage for a stable ad/product position to auto-click.

    Returns product-page anchors (with mi_id/spm flags) and ad-like containers, so we
    can pick a deterministic fixed position for the simulated click that generates mi_id.
    """
    from src.browser.session import get_session
    from src.extract.selectors import HOME_AD_RECON_JS

    session = get_session()
    page = await session.start()
    await page.goto("https://www.taobao.com", wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await page.wait_for_timeout(3000)
    return await page.evaluate(HOME_AD_RECON_JS)


async def _auto_acquire_miid(page, keyword: str = "3D打印机") -> str | None:
    """Programmatically obtain a fresh mi_id from the homepage rec-feed (fixed position).

    Recon (2026-08-18): the homepage 猜你喜欢 feed items (a.item-link inside
    .tb-pick-feeds-container) carry mi_id directly in their href (xxc=home_recommend),
    at a deterministic first position. Strategy:
      1. read mi_id straight from the first feed link's href (ZERO click footprint);
      2. fallback: click that fixed-position feed item (a real DOM click, human-paced);
      3. last resort: search page → click the first product result.
    """
    from urllib.parse import quote as _quote

    # 1) read from the fixed homepage rec-feed href
    try:
        await page.goto("https://www.taobao.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        href_miid = await page.evaluate(
            """() => {
              const a = document.querySelector(
                '.tb-pick-feeds-container a[href*="mi_id="], a.item-link[href*="mi_id="], a[href*="mi_id="]');
              return a ? a.getAttribute('href') : null;
            }"""
        )
        if href_miid:
            m = miid_from_url(href_miid)
            if m:
                return m
        # 2) click the first feed item (fixed position)
        feed = page.locator('.tb-pick-feeds-container a.item-link, a[href*="mi_id="]').first
        if await feed.count() > 0:
            await feed.click(timeout=8000)
            await page.wait_for_timeout(4000)
            m = miid_from_url(page.url)
            if m:
                return m
    except Exception:
        pass
    # 3) search page → first result
    try:
        await page.goto("https://s.taobao.com/search?q=" + _quote(keyword), wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        card = page.locator('a[href*="item.htm"]').first
        if await card.count() > 0:
            await card.click(timeout=8000)
            await page.wait_for_timeout(4000)
            m = miid_from_url(page.url)
            if m:
                return m
    except Exception:
        pass
    return None


async def watch_pages(watch_seconds: int = 180, start_url: str | None = None) -> dict:
    """Record URL changes + mi_id across MULTIPLE pages/tabs while a human operates.

    Attaches to the browser CONTEXT (not just the main tab), so new tabs/pages opened
    by the human are tracked too. Every URL change on every page is logged with its
    mi_id. Use it to record a manual 收藏→收藏夹→点击 flow, then build the automated
    version from the recorded sequence.
    """
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    ctx = session.context
    states: dict[int, dict] = {id(page): {"page": page, "last": page.url or "", "tag": "main"}}
    events: list[dict] = []
    start = time.time()

    def _tag_of(p) -> str:
        u = p.url or ""
        if "cart" in u:
            return "cart"
        if "favorite" in u or "collect" in u or "my_itaobao" in u or "my_taobao" in u:
            return "fav"
        if "item.htm" in u:
            return "item"
        if "search" in u:
            return "search"
        return "other"

    def on_page(p):
        states[id(p)] = {"page": p, "last": p.url or "", "tag": "new_tab"}
        events.append({"t": round(time.time() - start, 1), "kind": "new_page", "tag": "new_tab", "url": (p.url or "")[:160]})
        try:
            p.on("close", lambda pp=id(p): events.append({"t": round(time.time() - start, 1), "kind": "close_page", "page_id": pp}))
        except Exception:
            pass

    ctx.on("page", on_page)
    try:
        if start_url:
            await page.goto(start_url, wait_until="domcontentloaded")
            states[id(page)]["last"] = page.url or ""
    except Exception:
        pass

    deadline = start + watch_seconds
    while time.time() < deadline:
        for pid, st in list(states.items()):
            p = st.get("page")
            try:
                u = p.url or ""
            except Exception:
                u = ""
            if u and u != st.get("last"):
                st["last"] = u
                events.append({
                    "t": round(time.time() - start, 1),
                    "kind": "url_change",
                    "tag": st.get("tag", _tag_of(p)),
                    "url": u[:200],
                    "miid": miid_from_url(u),
                })
        try:
            await page.wait_for_timeout(1000)
        except Exception:
            break

    # summary
    miids = [e["miid"] for e in events if e.get("miid")]
    unique: list[str] = []
    for m in miids:
        if m not in unique:
            unique.append(m)
    return {
        "watch_seconds": watch_seconds,
        "event_count": len(events),
        "pages_tracked": len(states),
        "distinct_miids": unique,
        "miid_rotates": len(unique) > 1,
        "events": events,
    }


async def get_miid(watch_seconds: int = 90, keyword: str = "3D打印机", mode: str = "auto") -> dict:
    """Obtain/refresh mi_id — auto (simulated click on a fixed homepage feed position)
    or human (a person clicks a product). Persists to output/.miid.json.

    ``mode="auto"`` reads mi_id from the homepage 猜你喜欢 feed's fixed first link (or
    clicks it) — no human needed, zero/minimal footprint. ``mode="human"`` opens a
    search page and waits for a person to click a product (lowest-risk fallback).
    """
    from urllib.parse import quote

    from src.browser.session import get_session

    session = get_session()
    page = await session.start()

    # ---- auto mode: no human needed, fixed homepage feed position ----
    if mode == "auto":
        start = time.time()
        miid = await _auto_acquire_miid(page, keyword=keyword)
        if miid:
            path = persist_miid(miid)
            return {
                "ok": True,
                "mi_id": miid,
                "persisted_to": str(path),
                "mode": "auto",
                "via": "homepage rec-feed fixed link (read/click)",
                "elapsed_s": round(time.time() - start, 1),
                "message": "mi_id auto-captured from the homepage fixed feed position. taobao_fetch_detail will now use it.",
            }
        return {
            "ok": False,
            "mi_id": None,
            "mode": "auto",
            "elapsed_s": round(time.time() - start, 1),
            "message": (
                "Auto mode found no mi_id (homepage feed may have shifted or shown a "
                "captcha). Re-run, or switch to mode='human' and click a product."
            ),
        }

    # ---- human mode: wait for a person to click a product ----
    await page.goto(
        "https://s.taobao.com/search?q=" + quote(keyword), wait_until="domcontentloaded"
    )
    await session.guard_captcha(page)

    start = time.time()
    deadline = start + watch_seconds
    while time.time() < deadline:
        try:
            u = page.url or ""
        except Exception:
            u = ""
        miid = miid_from_url(u)
        if miid:
            path = persist_miid(miid)
            return {
                "ok": True,
                "mi_id": miid,
                "persisted_to": str(path),
                "mode": "human",
                "via_url": u[:220],
                "watched_s": round(time.time() - start, 1),
                "message": "mi_id captured from a human click and persisted. taobao_fetch_detail will now use it.",
            }
        try:
            await page.wait_for_timeout(2000)
        except Exception:
            break

    return {
        "ok": False,
        "mi_id": None,
        "mode": "human",
        "watched_s": round(time.time() - start, 1),
        "message": (
            "No mi_id captured. In the Chrome window, click a product card in the "
            "search results (a normal left-click that navigates the tab to the product). "
            "Then re-run this tool — or run it again and click during the window."
        ),
    }
