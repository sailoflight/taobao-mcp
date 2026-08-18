"""Dump full goodsItem card structure to find the clickable element + pid."""
from __future__ import annotations

from src.extract.favorite import COLLECT_URL


async def probe_collect_click() -> dict:
    """Click the first 收藏夹 card (fresh favorite sits at top) and see if it opens the
    item in a NEW TAB with a fresh mi_id. Validates the click-from-favorites step."""
    from urllib.parse import parse_qs, urlparse
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    ctx = session.context
    new_pages: list = []
    ctx.on("page", lambda p: new_pages.append(p))

    out: dict = {}
    await page.goto(COLLECT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(12000)
    for _ in range(5):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await page.wait_for_timeout(1500)
    out["before_url"] = (page.url or "")[:120]
    out["cards"] = await page.evaluate(
        """() => {
          const cs = [...document.querySelectorAll('[class*="goodsItem"]')];
          return { count: cs.length, firstTitle: cs.length ? (cs[0].querySelector('[class*="title"]') || {}).innerText || '' : '' };
        }"""
    )
    try:
        card = page.locator('[class*="goodsItem"]').first
        await card.click(timeout=8000)
        await page.wait_for_timeout(6000)
        out["main_url"] = (page.url or "")[:160]
        # capture the new tab's URL + mi_id
        for np in new_pages:
            try:
                nu = np.url or ""
            except Exception:
                nu = ""
            if nu and "item.htm" in nu:
                qs = parse_qs(urlparse(nu).query)
                out["new_tab_url"] = nu[:220]
                out["new_tab_mi_id"] = (qs.get("mi_id") or [None])[0]
                break
        out["new_tab_count"] = len(new_pages)
    except Exception as exc:
        out["click_error"] = str(exc)
    return out


async def recon_goodsitem(target_pid: str = "") -> dict:
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    out: dict = {"target_pid": target_pid}
    await page.goto(COLLECT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(12000)
    for _ in range(5):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await page.wait_for_timeout(1500)
    try:
        out["card_html"] = await page.evaluate(
            """() => {
              const cards = [...document.querySelectorAll('[class*="goodsItem"]')];
              const out = [];
              for (let i = 0; i < cards.length && i < 3; i++) {
                const e = cards[i];
                const links = [];
                e.querySelectorAll('a').forEach(a => links.push({ href: (a.getAttribute('href')||'').slice(0,140), text: (a.innerText||'').trim().slice(0,16), cls: String(a.className||'').slice(0,30) }));
                const attrs = {};
                for (const a of e.attributes) attrs[a.name] = (a.value||'').slice(0,60);
                out.push({ idx: i, attrs, links, onclick: (e.getAttribute('onclick')||'').slice(0,150), html: (e.outerHTML||'').slice(0, 1600) });
              }
              return out;
            }"""
        )
        out["pid_in_dom"] = await page.evaluate(
            """(pid) => {
              const s = document.body ? document.body.innerHTML : '';
              return { inHtml: s.includes(pid), sample: s.indexOf(pid) };
            }""",
            target_pid,
        )
    except Exception as exc:
        out["error"] = str(exc)
    return out
