"""Full-detail (详情图长图) extraction + recon — GREENFIELD.

The product page's bottom 详情 section is a long strip of description images
(e.g. a 3D printer listing has a long spec/picture strip). The mechanism varies
by listing generation:
  1. ``res.descUrl`` / ``res.descPath`` — embedded URL; GET it and parse <img>.
  2. a description <iframe> (e.g. desc.alicdn.com / h5.m.taobao.com/awp/core/detail.htm).
  3. lazy-loaded <img> directly in the DOM (scroll to the bottom to trigger).
  4. an XHR (e.g. mtop.taobao.detail.getdesc) returning a JSON/HTML image list.

Recon-first (CLAUDE.md methodology): ``recon_detail`` opens ONE page, scrolls to
the detail area, finds every desc URL in the raw HTML, and actually fetches the
candidate(s) to record which mechanism is live — that record is the basis for
``fetch_detail``'s path decision and for the NOTES.md field notes.
"""

from __future__ import annotations

import re

from src.extract.selectors import DESC_RECON_JS

# Substring hints for desc-like keys inside the embedded ICE ``res`` dict.
_DESC_KEY_HINTS = (
    "descUrl", "descPath", "desc", "Description", "description", "itemDesc", "descriptions"
)
# Regexes for desc URLs embedded in the page HTML (the classic h5 desc endpoint).
_DESC_URL_RES = (
    re.compile(r'https?:\\?/\\?/h5\.m\.taobao\.com/awp[^"\'\\\s]*'),
    re.compile(r'https?:\\?/\\?/desc\.alicdn\.com[^"\'\\\s]*'),
    re.compile(r'https?:\\?/\\?/item\.taobao\.com/desc[^"\'\\\s]*'),
)
# JSON-ish keys that carry the detail image list on some pages.
_DESC_JSON_KEYS = (
    "descImageList", "descImages", "descPath", "descUrl", "getdesc", "detailImageList", "detailImages"
)


def find_desc_keys(obj, prefix: str = "", depth: int = 0, out: dict | None = None) -> dict:
    """Shallow recursive scan of the ``res`` dict for desc-like keys and their values.

    Stops at depth 3 and at list length 20 so a huge tree is never walked fully.
    """
    if out is None:
        out = {}
    if depth > 3:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if any(h in ks for h in _DESC_KEY_HINTS):
                s = str(v)
                out[f"{prefix}{ks}"] = s[:240] + ("…" if len(s) > 240 else "")
            find_desc_keys(v, f"{prefix}{ks}.", depth + 1, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            find_desc_keys(v, f"{prefix}[{i}].", depth + 1, out)
    return out


def _find_desc_urls(html: str) -> list[str]:
    """All desc-URL substrings in the page HTML (deduped, unescaped)."""
    found: list[str] = []
    for rx in _DESC_URL_RES:
        for m in rx.finditer(html):
            s = m.group(0).replace("\\/", "/")
            found.append(s)
    # dedupe preserving order
    out: list[str] = []
    for s in found:
        if s not in out:
            out.append(s)
    return out


async def _fetch_desc_summary(page, url: str) -> dict:
    """GET a desc URL via the page's request context (shares cookies) and summarize."""
    summary: dict = {"url": url}
    try:
        resp = await page.request.get(url, timeout=20000)
        summary["status"] = resp.status
        summary["content_type"] = resp.headers.get("content-type", "")
        body = await resp.text()
        summary["len"] = len(body)
        imgs = re.findall(r'<img[^>]*src=["\']([^"\']+)["\']', body, re.I)
        summary["img_count"] = len(imgs)
        summary["img_sample"] = imgs[:8]
        json_imgs = re.findall(r'["\']?(?:https?:)?//[^"\']*img\.alicdn\.com[^"\']*["\']?', body)
        summary["alicdn_url_count"] = len(json_imgs)

        # What does the shell actually load? scripts / desc-ish tokens / window vars.
        script_srcs = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', body, re.I)
        summary["script_srcs"] = script_srcs[:10]
        desc_tokens = {}
        for tok in ("descPath", "descUrl", "getdesc", "desc.alicdn.com", "mtop.taobao.detail",
                    "pcdetail", "getDetail", "descContent", "J_DescContent", "detail_100"):
            if tok in body:
                idx = body.find(tok)
                desc_tokens[tok] = body[max(0, idx - 80):idx + 160]
        summary["desc_tokens"] = desc_tokens
        # A sample of the first script block (may carry the real desc data).
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", body, re.S | re.I)
        if scripts:
            nonempty = [s for s in scripts if len(s) > 40]
            summary["script_blocks"] = len(scripts)
            summary["script_block_head"] = (nonempty[0][:600] if nonempty else "")[:600]
        summary["head"] = body[:200]
    except Exception as exc:
        summary["error"] = str(exc)
    return summary


async def _probe_pc_detail(page, item_url: str, referer: str | None = None) -> dict:
    """PC 桌面端点 recon: scroll the SKU-panel inner container + harvest the 详情.

    Mechanism (from the maintained userscript greasyfork 460143, 2026-01): the new
    SSR page renders the 详情 inside the scrollable ``#tbpcDetail_SkuPanelBody`` div,
    NOT the main page. Scroll that inner div to trigger lazy images, then harvest
    .desc-root / .content-detail / [class*="desc-"] / [class*="detail-"] imgs. Also
    records every ``mtop`` API the page calls (request+body) as the fallback record.

    ``referer`` — the user found that entering from the SEARCH page (referer =
    s.taobao.com/search) makes the SSR render the 详情, whereas a bare direct
    navigation does not. Test both.
    """
    import json as _json
    import re

    from src.extract.selectors import DESC_PANEL_JS, DESC_TAB_PROBE_JS

    captured_resps = []

    def on_resp(resp):
        try:
            u = resp.url or ""
            if "mtop" in u and "taobao.com" in u:
                captured_resps.append(resp)
        except Exception:
            pass

    page.on("response", on_resp)
    out: dict = {}
    try:
        goto_kwargs = {"wait_until": "domcontentloaded"}
        if referer:
            goto_kwargs["referer"] = referer
        await page.goto(item_url, **goto_kwargs)
        await page.wait_for_timeout(2500)
        for _ in range(3):  # bring the SKU panel into view (it sits below the fold)
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await page.wait_for_timeout(1200)
        out["final_url"] = page.url
        out["panel"] = await page.evaluate(DESC_PANEL_JS)
        out["tab"] = await page.evaluate(DESC_TAB_PROBE_JS)
        try:  # return the tab to the top so the next op starts clean
            await page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

        apis: list[dict] = []
        for r in captured_resps:
            item: dict = {"url": (r.url or "")[:200], "status": r.status}
            try:
                item["ct"] = (r.headers or {}).get("content-type", "")
            except Exception:
                pass
            try:
                body = await r.text()
                item["body_len"] = len(body)
                if body[:1] in "{[":
                    j = _json.loads(body)
                    if isinstance(j, dict):
                        item["json_keys"] = list(j.keys())[:20]
                        s = _json.dumps(j, ensure_ascii=False)
                        imgs = sorted(set(re.findall(r'//[^"]*img\.alicdn\.com[^"]*', s)))
                        item["alicdn_img_count"] = len(imgs)
                        item["desc_field"] = _find_big_desc_field(j)
            except Exception as exc:
                item["body_error"] = str(exc)
            apis.append(item)
        out["mtop_apis"] = apis
    except Exception as exc:
        out["error"] = str(exc)
    finally:
        page.remove_listener("response", on_resp)
    return out


def _find_big_desc_field(obj, depth: int = 0) -> dict | None:
    """Locate the description HTML/image payload inside the mtop JSON (best-effort)."""
    if depth > 4:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k).lower()
            if isinstance(v, str) and ("desc" in ks or "description" in ks or "detail" in ks) and len(v) > 200:
                imgs = sorted(set(re.findall(r'//[^"]*img\.alicdn\.com[^"]*', v)))
                return {"key": str(k), "len": len(v), "img_count": len(imgs), "img_sample": imgs[:6],
                        "head": v[:160]}
            found = _find_big_desc_field(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:10]):
            found = _find_big_desc_field(v, depth + 1)
            if found:
                return found
    return None


async def _probe_h5_detail_api(page, pid: str) -> dict:
    """Capture the exact ``mtop.taobao.detail.data.get`` call the H5 page makes.

    The desktop SSR page never fetches the 详情; the H5 variant (shell redirects to
    ``detail.tmall.com?x-ssr=true``) pulls it via ``mtop.taobao.detail.data.get/1.0``.
    Loading the shell in the browser and capturing that call's full signed URL + POST
    body + response shows exactly where the 详情 image list lives.
    """
    import json as _json

    captured = []

    def on_resp(resp):
        try:
            u = resp.url or ""
            if "mtop.taobao.detail" in u or "getdesc" in u:
                captured.append(resp)
        except Exception:
            pass

    page.on("response", on_resp)
    out: dict = {}
    try:
        shell = f"https://h5.m.taobao.com/awp/core/detail.htm?id={pid}"
        await page.goto(shell, wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        out["final_url"] = (page.url or "")[:220]

        # The captured response body is often already consumed; re-fetch the exact
        # signed URL via the page's request context (shares cookies) to get the JSON.
        apis: list[dict] = []
        for r in captured:
            item: dict = {"url": (r.url or "")[:1200], "status": r.status}
            try:
                item["ct"] = (r.headers or {}).get("content-type", "")
            except Exception:
                pass
            body = None
            try:
                body = await r.text()
            except Exception:
                body = None
            if not body:
                try:
                    r2 = await page.request.get(r.url, timeout=25000)
                    body = await r2.text()
                    item["refetched"] = True
                except Exception as exc:
                    item["refetch_error"] = str(exc)
            if body:
                item["body_len"] = len(body)
                if body[:1] in "{[":
                    try:
                        j = _json.loads(body)
                        if isinstance(j, dict):
                            item["json_keys"] = list(j.keys())[:25]
                            item["ret"] = j.get("ret")
                            df = _find_big_desc_field(j)
                            item["desc_field"] = df
                    except Exception as exc:
                        item["json_error"] = str(exc)
            apis.append(item)
        out["apis"] = apis
    except Exception as exc:
        out["error"] = str(exc)
    finally:
        page.remove_listener("response", on_resp)
    return out


async def watch_detail(product_url_or_id: str, watch_seconds: int = 120) -> dict:
    """Open a product page and WATCH it while a human operates the visible window.

    Attaches a network response listener (mtop / desc / alicdn images) and takes a
    light DOM snapshot every ~3s for `watch_seconds`. Whatever the human clicks in
    the real Chrome window during the window is recorded — the definitive way to
    discover how the 详情 loads when automated probes keep missing it.
    """
    import asyncio
    import time

    from src.browser.session import get_session
    from src.extract.product import _to_product_id
    from src.extract.selectors import SNAPSHOT_JS

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)

    net: list[dict] = []

    def on_resp(resp):
        try:
            u = resp.url or ""
            if ("mtop" in u and "taobao.com" in u) or "desc" in u or ("imgextra" in u and "alicdn" in u):
                entry: dict = {"t": round(time.time(), 1), "status": resp.status, "url": u[:180]}
                try:
                    entry["ct"] = (resp.headers or {}).get("content-type", "")[:40]
                except Exception:
                    pass
                if "mtop" in u:
                    try:
                        entry["method"] = resp.request.method
                        pd = resp.request.post_data or ""
                        entry["post"] = pd[:300]
                    except Exception:
                        pass
                net.append(entry)
                if len(net) > 300:
                    net.pop(0)
        except Exception:
            pass

    page.on("response", on_resp)

    timeline: list[dict] = []
    start = time.time()
    deadline = start + watch_seconds
    while time.time() < deadline:
        try:
            snap = await page.evaluate(SNAPSHOT_JS)
            snap["t"] = round(time.time() - start, 1)
            snap["net_count"] = len(net)
            snap["url"] = (page.url or "")[:180]
            timeline.append(snap)
        except Exception:
            pass
        await asyncio.sleep(min(3.0, max(0.5, deadline - time.time())))

    page.remove_listener("response", on_resp)
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    return {
        "url": url,
        "watch_seconds": watch_seconds,
        "final_url": (page.url or "")[:180],
        "net_count": len(net),
        "timeline_count": len(timeline),
        "net": net,
        "timeline": timeline,
    }


async def fetch_detail(product_url_or_id: str, miid_source: str = "favorite") -> dict:
    """Fetch the full 详情 (图文详情) image strip for one product.

    miid_source="favorite" (default, LOW-RISK, user-designed, CLAUDE.md §7 paced):
      1. ensure_favorited — judge the #collectBtn color; ONLY add when not favorited,
         never touch/re-order an existing favorite (added_by_us tracks cleanup need).
      2. click_from_favorites — a fresh favorite sits at the TOP of the 收藏夹; clicking
         its card opens a NEW TAB with a natural tracking URL carrying a FRESH mi_id
         (spm=tbpc.mytb_itemcollect.item.goods) every time → harvest .desc-root from it.
      3. cleanup — if we added the favorite this round, un-favorite it afterwards
         (no residue); close the popup tab (single-tab hygiene).
    miid_source="config" uses the static mi_id (fast fallback). Slow but risk-friendly:
    every query regenerates a fresh, product-scoped mi_id from a real user-data path.
    """
    from src.browser.pacing import human_delay
    from src.browser.session import get_session
    from src.config import load_config
    from src.extract.miid import miid_from_url
    from src.extract.product import _to_product_id
    from src.extract.selectors import DESC_PANEL_JS

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()

    entry: dict = {"miid_source": miid_source, "added_by_us": False, "favorite_fallback": False}
    harvest_page = page
    popup = None
    if miid_source == "favorite":
        from src.extract.favorite import click_from_favorites, ensure_favorited, ensure_unfavorited

        fav = await ensure_favorited(page, pid)
        entry["favorite"] = fav
        entry["added_by_us"] = bool(fav.get("added_by_us"))
        res = await click_from_favorites(page, pid, added_by_us=entry["added_by_us"])
        popup = res.get("popup")
        if res.get("mi_id") and res.get("matches_target"):
            entry["clicked_url"] = res["url"]
            entry["miid_from"] = "favorite_click"
            harvest_page = popup or page
        else:
            entry["favorite_fallback"] = True  # click missed/not found → static config below
            entry["click_fail_reason"] = res.get("reason")
            entry["clicked_opened_id"] = res.get("opened_id")
    else:
        entry["favorite"] = None

    # Ensure we're on an item page carrying a usable mi_id (fallback / config path).
    if not (harvest_page.url and "item.htm" in harvest_page.url and miid_from_url(harvest_page.url or "")):
        mi_id = load_config().detail.mi_id
        url = f"https://item.taobao.com/item.htm?id={pid}"
        if mi_id:
            url += f"&mi_id={mi_id}"
        await page.goto(url, wait_until="domcontentloaded")
        await session.guard_captcha(page)
        harvest_page = page

    for _ in range(2):  # bring the SKU panel into view before scrolling it internally
        try:
            await harvest_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await human_delay(0.8, 1.5)

    harvest = await harvest_page.evaluate(DESC_PANEL_JS)
    raw = harvest.get("imgs") or harvest.get("imgsAnyWidth") or []
    normalized: list[str] = []
    for u in raw:
        s = str(u).strip()
        if s.startswith("//"):
            s = "https:" + s
        elif not s.startswith("http"):
            continue
        if s not in normalized:
            normalized.append(s)

    try:
        await harvest_page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    # Cleanup (user rule): if WE favorited it this round, un-favorite — no residue.
    if miid_source == "favorite" and entry.get("added_by_us"):
        try:
            entry["cleanup"] = await ensure_unfavorited(page, pid)
        except Exception as exc:
            entry["cleanup"] = {"error": str(exc)}
    else:
        entry["cleanup"] = {"state": "not_added_by_us", "clicked": False}
    # Single-tab hygiene (CLAUDE.md §7.3): close the popup tab we opened.
    if popup and not popup.is_closed():
        try:
            await popup.close()
        except Exception:
            pass

    stale = not harvest.get("scope")
    return {
        "product_id": pid,
        "url": (harvest_page.url or "")[:220],
        "miid_used": miid_from_url(harvest_page.url or ""),
        "entry": entry,
        "scope": harvest.get("scope"),
        "panel_found": harvest.get("panelFound"),
        "count": len(normalized),
        "detail_images": normalized,
        # Signal to the caller: mi_id is stale/expired → the favorite flow regenerates one.
        "miid_stale": stale,
        "caveat": (None if not stale else
                   "no .desc-root found — the mi_id is stale. Run taobao_fetch_detail again "
                   "(favorite flow regenerates a fresh one) or taobao_get_miid (auto)."),
    }


async def probe_entry(entry_url: str, referer: str | None = None) -> dict:
    """Navigate to an arbitrary entry URL and harvest the 详情 — tests which entry
    (tracking URL / referer) makes the SSR render the 详情 strip."""
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    return await _probe_pc_detail(page, entry_url, referer=referer)


async def recon_detail(product_url_or_id: str) -> dict:
    """Open ONE product page, find every desc URL, and probe the live mechanism."""
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.extract.product import _to_product_id, extract_ice_res

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    for _ in range(3):  # incremental scroll to trigger lazy detail images
        await human_scroll(page, 3)
        await human_delay(1.0, 2.0)

    html = await page.content()
    evidence: dict = {"product_id": pid, "url": url, "html_len": len(html)}

    try:
        res = extract_ice_res(html)
        evidence["res_top_keys"] = sorted(res.keys())
        evidence["desc_like"] = find_desc_keys(res)
    except Exception as exc:
        evidence["res_error"] = str(exc)

    try:
        evidence["dom"] = await page.evaluate(DESC_RECON_JS)
    except Exception as exc:
        evidence["dom_error"] = str(exc)

    desc_urls = _find_desc_urls(html)
    evidence["desc_urls_found"] = desc_urls
    evidence["desc_json_keys"] = {k: (k in html) for k in _DESC_JSON_KEYS}
    evidence["html_hosts"] = {
        "h5.m.taobao.com/awp": ("h5.m.taobao.com/awp" in html),
        "desc.alicdn.com": ("desc.alicdn.com" in html),
        "descUrl": ("descUrl" in html),
    }

    # Probe the mechanism: fetch found desc URLs (cap 3), else the classic constructed one.
    candidates = desc_urls[:3]
    if not candidates:
        candidates = [f"https://h5.m.taobao.com/awp/core/detail.htm?id={pid}"]
    probes = []
    for cu in candidates:
        probes.append(await _fetch_desc_summary(page, cu))
    evidence["desc_probes"] = probes

    # Live probe: call mtop.taobao.detail.getdetail via the page's own lib.mtop (still on
    # the item page — same tab, no navigation). This is the real detail-fetch path.
    from src.extract.selectors import GETDETAIL_JS

    try:
        evidence["getdetail_probe"] = await page.evaluate(GETDETAIL_JS, pid)
    except Exception as exc:
        evidence["getdetail_probe"] = {"error": str(exc)}

    # Final probe: the user found 详情 renders when entering FROM the search page
    # (referer = s.taobao.com/search), not on a bare direct navigation. Simulate that
    # entry and harvest the 详情 strip.
    from urllib.parse import quote

    search_ref = f"https://s.taobao.com/search?q={quote(product_url_or_id)}&tab=all"
    evidence["pc_detail_with_search_referer"] = await _probe_pc_detail(page, url, referer=search_ref)

    return evidence
