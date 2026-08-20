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

from src.errors import CaptchaError, SelectorDriftError
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


async def probe_miid_price(product_url_or_id: str, target_chip: str = "特大号") -> dict:
    """细查观察: 走收藏链路落到带 mi_id 的个性化页面, 尝试选中目标变体芯片
    (默认"特大号"), 读该页显示的价格(平台加补后/到手价/价格行), 看优惠价是否可见。
    Read-only navigation + one chip click; un-favorites if we added it this round.
    """
    from src.browser.pacing import human_click
    from src.browser.session import get_session
    from src.extract.favorite import click_from_favorites, ensure_favorited, ensure_unfavorited
    from src.extract.product import _to_product_id
    from src.extract.selectors import PRICE_LINES_JS, SUBSIDY_PRICE_JS

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    out: dict = {"pid": pid, "target_chip": target_chip}
    fav = await ensure_favorited(page, pid)
    out["favorite"] = fav
    res = await click_from_favorites(page, pid, added_by_us=fav.get("state") == "added")
    popup = res.get("popup")
    tp = popup or page
    try:
        await tp.wait_for_timeout(2500)
        for _ in range(2):  # light scroll to trigger the price area to render
            try:
                await tp.mouse.wheel(0, 400)
            except Exception:
                pass
            await tp.wait_for_timeout(600)
        try:
            await tp.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        await tp.wait_for_timeout(1200)
        out["landed_url"] = (res.get("url") or "")[:160]
        out["subsidy_default"] = await tp.evaluate(SUBSIDY_PRICE_JS)
        out["page_default"] = await tp.evaluate(PRICE_LINES_JS)
    except Exception as exc:
        out["land_error"] = str(exc)

    # try to select the target chip (特大号 / 56*41*32) inside the sku area
    chip_clicked = False
    for sel in (f'[class*="sku"]:has-text("{target_chip}")',
                f'[class*="Sku"]:has-text("{target_chip}")',
                f'[class*="skuItem"]:has-text("{target_chip}")',
                f'[class*="item"]:has-text("{target_chip}")'):
        try:
            loc = tp.locator(sel).first
            if await loc.count() > 0:
                await human_click(tp, loc)
                await tp.wait_for_timeout(1800)
                chip_clicked = True
                break
        except Exception:
            continue
    out["chip_clicked"] = chip_clicked
    if chip_clicked:
        try:
            await tp.wait_for_timeout(2200)
            out["subsidy_after_chip"] = await tp.evaluate(SUBSIDY_PRICE_JS)
            out["page_after_chip"] = await tp.evaluate(PRICE_LINES_JS)
        except Exception as exc:
            out["post_chip_error"] = str(exc)

    if fav.get("added_by_us"):
        try:
            out["cleanup"] = await ensure_unfavorited(page, pid)
        except Exception as exc:
            out["cleanup"] = {"error": str(exc)}
    if popup and not popup.is_closed():
        try:
            await popup.close()
        except Exception:
            pass
    return out


async def probe_sku_structure(product_url_or_id: str, target: str = "特大号白色") -> dict:
    """诊断: SKU 芯片真实结构 + 点击后 selected 态/URL/价格是否变化. 走收藏链路落 mi_id 页."""
    from src.browser.session import get_session
    from src.extract.favorite import click_from_favorites, ensure_favorited, ensure_unfavorited
    from src.extract.product import _to_product_id
    from src.extract.selectors import PRICE_LINES_JS

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    out: dict = {"pid": pid, "target": target}
    fav = await ensure_favorited(page, pid)
    res = await click_from_favorites(page, pid, added_by_us=fav.get("state") == "added")
    popup = res.get("popup")
    tp = popup or page
    await tp.wait_for_timeout(2500)

    SKU_STATE_JS = r"""() => {
      const out = { chips: [] };
      // 只取 SKU 选项根元素(带 data-vid 的 valueItem), 避免把 imgWrap/img/text 子元素算进来;
      // 去掉 10-chip 上限 — 多档位商品(如 19 档食品袋)也能全量返回。
      const nodes = document.querySelectorAll('[class*="valueItem"][data-vid]');
      if (!nodes.length) {
        document.querySelectorAll('[class*="valueItem"]').forEach(e => {
          const t = (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 30);
          out.chips.push({
            text: t,
            cls: String(e.className || '').slice(0, 60),
            selected: /selected|active|cur|on/i.test(String(e.className || '')),
            html: (e.outerHTML || '').slice(0, 220),
          });
        });
        return out;
      }
      nodes.forEach(e => {
        const t = (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40);
        out.chips.push({
          text: t,
          cls: String(e.className || '').slice(0, 80),
          selected: /selected|active|cur|on/i.test(String(e.className || '')),
          html: (e.outerHTML || '').slice(0, 260),
        });
      });
      return out;
    }"""
    try:
        out["chips_before"] = await tp.evaluate(SKU_STATE_JS)
        # click the target chip
        chip = tp.locator('[class*="valueItem"]').filter(has_text=target).first
        if await chip.count() == 0:
            chip = tp.get_by_text(target, exact=False).last
        await chip.click(timeout=6000)
        await tp.wait_for_timeout(3800)  # price re-render is async
        out["url_after"] = (tp.url or "")[:200]
        out["chips_after"] = await tp.evaluate(SKU_STATE_JS)
        out["price_after"] = await tp.evaluate(PRICE_LINES_JS)
        from src.extract.selectors import PRICE_NODE_JS
        out["price_nodes_after"] = await tp.evaluate(PRICE_NODE_JS)
    except Exception as exc:
        out["error"] = str(exc)[:120]

    if fav.get("added_by_us"):
        try:
            out["cleanup"] = await ensure_unfavorited(page, pid)
        except Exception as exc:
            out["cleanup"] = {"error": str(exc)}
    if popup and not popup.is_closed():
        try:
            await popup.close()
        except Exception:
            pass
    return out


async def sweep_variant_prices(product_url_or_id: str, max_chips: int = 12) -> dict:
    """细查变体价格扫描: 走收藏链路落到带 mi_id 的个性化页面, 逐个点击 SKU 型号芯片,
    读每个型号的显示价格(店铺优惠后/券后/到手价/价格行) — 确认能分清每个 SKU 型号的价格。
    Read-only navigation + per-chip clicks; un-favorites if we added it this round.
    """
    from src.browser.session import get_session
    from src.extract.favorite import click_from_favorites, ensure_favorited, ensure_unfavorited
    from src.extract.product import _to_product_id
    from src.extract.selectors import CHIP_DISCOVER_JS, DESC_PANEL_JS, PRICE_LINES_JS

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    out: dict = {"pid": pid}
    fav = await ensure_favorited(page, pid)
    res = await click_from_favorites(page, pid, added_by_us=fav.get("state") == "added")
    popup = res.get("popup")
    tp = popup or page
    try:
        # DESC_PANEL scroll trick renders the whole page (prices included) — proven in fetch_detail
        await tp.evaluate(DESC_PANEL_JS)
        await tp.evaluate("window.scrollTo(0, 0)")
        await tp.wait_for_timeout(1000)
    except Exception:
        pass
    out["landed_url"] = (res.get("url") or "")[:160]
    try:
        out["base"] = await tp.evaluate(PRICE_LINES_JS)
    except Exception as exc:
        out["base"] = {"error": str(exc)[:80]}
    try:
        chips = await tp.evaluate(CHIP_DISCOVER_JS)
    except Exception as exc:
        chips = []
        out["chip_discover_error"] = str(exc)
    out["chips"] = chips
    out["per_variant"] = {}
    clicked = 0
    for ch in chips[:max_chips]:
        text = (ch or {}).get("text", "")
        if not text or text in ("规格", "颜色分类") or "物品类型" in text or "重量" in text:
            continue
        # only the real SKU option chips (valueItem) carrying a size/color marker
        try:
            size_part = text.split("【")[0].strip()
            chip = tp.locator('[class*="valueItem"]').filter(has_text=size_part).first
            if await chip.count() == 0:
                chip = tp.get_by_text(text, exact=False).last
            await chip.click(timeout=6000)  # native click — reliable selection for this diagnostic
            await tp.wait_for_timeout(3200)  # the price/URL re-render is async — give it time
            url_now = tp.url or ""
            # upStreamPrice in the URL is Taobao's own per-variant price (authoritative)
            import re as _re
            m = _re.search(r'upStreamPrice=(\d+)', url_now)
            out["per_variant"][text] = {
                "url": url_now[:200],
                "upstream_price": (m.group(1)[:-2] + "." + m.group(1)[-2:]) if m else None,
                "price": await tp.evaluate(PRICE_LINES_JS),
            }
            clicked += 1
        except Exception as exc:
            out["per_variant"][text] = {"error": str(exc)[:80]}
    out["chips_clicked"] = clicked
    if fav.get("added_by_us"):
        try:
            out["cleanup"] = await ensure_unfavorited(page, pid)
        except Exception as exc:
            out["cleanup"] = {"error": str(exc)}
    if popup and not popup.is_closed():
        try:
            await popup.close()
        except Exception:
            pass
    return out


async def _cleanup_fetch(entry: dict, page, pid: str, popup) -> None:
    """User-rule cleanup that MUST run even when a CaptchaError/SelectorDriftError
    escapes from on-page extraction (audit HIGH-3, cleanup-on-error guarantee):
    un-favorite what WE favorited this round (no residue) and close the popup tab
    we opened (single-tab hygiene, CLAUDE.md §7.3). Populates entry['cleanup'] for
    the success-path return; on the error path it still runs first and then the
    exception propagates.
    """
    if entry.get("added_by_us"):
        try:
            from src.extract.favorite import ensure_unfavorited

            entry["cleanup"] = await ensure_unfavorited(page, pid)
        except Exception as exc:
            entry["cleanup"] = {"error": str(exc)}
    else:
        entry["cleanup"] = {"state": "not_added_by_us", "clicked": False}
    if popup and not popup.is_closed():
        try:
            await popup.close()
        except Exception:
            pass


async def fetch_detail(product_url_or_id: str, miid_source: str = "config",
                       with_reviews: bool = False, reviews_max: int = 12,
                       reviews_keyword: str = "") -> dict:
    """Fetch the full 详情 (图文详情) image strip for one product.

    TWO-PHASE WORKFLOW (query separation, user-designed): 粗查定位 uses
    taobao_search + taobao_fetch_product (NEVER favorites, never regenerates mi_id);
    细查对比 uses this tool ONLY on shortlisted products.

    miid_source: ""/auto(默认, 走 config anti_risk.miid_channel, 即足迹→收藏 双机制) |
      footmark(仅足迹) | favorite(仅收藏) | config(静态 mi_id, 安全不碰收藏/足迹)。
    双机制(用户设计, 2026-08-19): 默认先试 足迹(浏览历史, 不耗收藏配额, 但列表易受用户
    手动浏览并发扰动 → 校验 opened_id==pid), 足迹失败(无卡/打开非目标)再退回 收藏链路。
    with_reviews: 在 mi_id 详情页就地抽取评论(好/中/差分层抽样) — Tmall 评论只在
    该页渲染(普通 SSR 页无评论卡); mi_id 每次经 足迹/收藏 点击新建、用完即关(无复用 URL),
    故评论/问答必须在关闭弹窗前一次抽取。QA(问大家)在真实 mi_id 上下文下总是顺带抽取。
    miid_source="config": uses the static mi_id. Use it for a quick look during 粗查
    without touching favorites/footmark.
    miid_source="favorite" (LOW-RISK, fine-compare, CLAUDE.md §7 paced):
      1. ensure_favorited — judge the #collectBtn color; ONLY add when not favorited,
         never touch/re-order an existing favorite (added_by_us tracks cleanup need).
      2. click_from_favorites — a fresh favorite sits at the TOP of the 收藏夹; a REAL
         simulated click on its card opens a NEW TAB with a natural tracking URL
         carrying a FRESH mi_id + favorites-channel params (spm=tbpc.mytb_itemcollect)
         every time → harvest .desc-root from that exact clicked page (all params kept).
      3. cleanup — if we added the favorite this round, un-favorite it afterwards
         (no residue); close the popup tab (single-tab hygiene).
    """
    from src.browser.pacing import human_delay
    from src.browser.session import get_session
    from src.config import load_config
    from src.extract.miid import miid_from_url
    from src.extract.product import _to_product_id
    from src.extract.selectors import DESC_PANEL_JS
    from src.log import get_logger

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()

    miid_channel = str(miid_source or "")
    if not miid_channel or miid_channel == "auto":
        from src.config import load_config

        miid_channel = load_config().anti_risk.miid_channel or "auto"

    entry: dict = {"miid_source": miid_channel, "added_by_us": False, "favorite_fallback": False}
    harvest_page = page
    popup = None
    footmark_ok = False

    # 1) 足迹渠道(默认, 不耗收藏配额; 列表易受用户手动浏览并发扰动 → 校验 opened_id==pid)
    if miid_channel in ("auto", "footmark"):
        from src.extract.favorite import open_via_footmark

        fres = await open_via_footmark(page, pid)
        if fres.get("url") and fres.get("mi_id") and fres.get("matches_target"):
            entry["footmark"] = {k: fres[k] for k in ("url", "mi_id", "opened_id", "cards", "matched_idx")
                                 if k in fres}
            entry["miid_from"] = "footmark_click"
            popup = fres.get("popup")
            harvest_page = popup or page
            footmark_ok = True
        else:
            entry["footmark"] = {k: fres.get(k) for k in ("reason", "cards") if fres.get(k) is not None}
            entry["footmark_fallback"] = True
            entry["click_fail_reason"] = fres.get("reason")

    # 2) 收藏渠道(兜底: "auto" 且足迹失败, 或显式 "favorite")
    if not footmark_ok and miid_channel in ("auto", "favorite"):
        from src.config import load_config
        if not load_config().anti_risk.fav_flow:
            # 收藏链路总开关关闭 — 不碰收藏, 落到静态 config mi_id 快速查看
            entry["favorite"] = {"state": "disabled", "quota": {"allowed": False}}
            entry["favorite_fallback"] = True
            entry["click_fail_reason"] = ("anti_risk.fav_flow=false(配置总开关关闭), 已用 config mi_id 快速查看; "
                                          "如需收藏链路细查请 taobao_config set anti_risk.fav_flow true(人工确认)")
        else:
            from src.extract.favorite import click_from_favorites, ensure_favorited, ensure_unfavorited
            from src.extract.fav_quota import check_and_record

            quota = check_and_record()  # anti-risk: daily cap on the favorite flow
            entry["quota"] = quota
            if not quota.get("allowed"):
                # 今日收藏链路配额已尽 — 不碰收藏, 落到静态 config mi_id 快速查看
                entry["favorite"] = {"state": "quota_exceeded", "quota": quota}
                entry["favorite_fallback"] = True
                entry["click_fail_reason"] = (f"今日收藏链路已达上限({quota.get('limit')}次), "
                                              "已用 config mi_id 快速查看; 明日或调大 "
                                              "limits.fav_flow_per_day 后再细查")
            else:
                fav = await ensure_favorited(page, pid)
                entry["favorite"] = fav
                entry["added_by_us"] = bool(fav.get("added_by_us"))
                res = await click_from_favorites(page, pid, added_by_us=entry["added_by_us"])
                popup2 = res.get("popup")
                if res.get("mi_id") and res.get("matches_target"):
                    entry["clicked_url"] = res["url"]
                    entry["miid_from"] = "favorite_click"
                    popup = popup2
                    harvest_page = popup2 or page
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

    # URL 轨迹诊断(2026-08-20): 用户观察到 足迹→详情→搜索页→详情 的异常导航。
    # 记录每一步的 URL, 下次实机即可定位搜索页出现在哪个分支(主流程本不应经过搜索页)。
    try:
        get_logger().info(
            "detail url-trace: miid_from=%s harvest=%s popup_closed=%s page=%s",
            entry.get("miid_from"), (harvest_page.url or "")[:160],
            (popup.is_closed() if popup else None), (page.url or "")[:160])
    except Exception:
        pass

    # Bring the SKU panel into view (it sits below the fold). Use the element's own
    # position, NOT window.scrollTo(0, scrollHeight) — the latter also drags the page
    # through the 推广商品 area (2026-08-20 user: "细查会一直下滑到推广区, 浪费时间").
    from src.browser.scroll import scroll_into_view

    panel_loc = harvest_page.locator("#tbpcDetail_SkuPanelBody")
    for _ in range(2):
        try:
            await scroll_into_view(harvest_page, panel_loc)
        except Exception:
            try:
                await harvest_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
        await human_delay(0.6, 1.2)

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

    # Price observation (fine-compare): on the mi_id-entered page the personalized
    # channel may show the platform-subsidy / coupon price (e.g. 平台加补后 ¥33.75).
    # The whole on-page extraction region lives in a try/finally so the user-rule
    # cleanup (un-favorite + popup close) ALWAYS runs — even when a
    # CaptchaError/SelectorDriftError escapes from review/QA/recommend extraction
    # (cleanup-on-error: fail loud WITHOUT leaving account-state residue).
    try:
        price_observed: dict = {}
        if entry.get("miid_from") in ("footmark_click", "favorite_click"):
            from src.extract.selectors import PRICE_LINES_JS, SUBSIDY_PRICE_JS

            try:
                price_observed["platform_subsidy_after"] = await harvest_page.evaluate(SUBSIDY_PRICE_JS)
            except Exception:
                price_observed["platform_subsidy_after"] = None
            try:
                price_observed["page"] = await harvest_page.evaluate(PRICE_LINES_JS)
            except Exception:
                pass

        # On-page 评论/问答 (Tmall 只在 mi_id 详情页渲染): 关闭弹窗前就地一次抽取。
        # mi_id 每次经 足迹/收藏 点击新建、用完即关, 不存在可复用 URL — 所以必须在这里取。
        # 先问答后评论: 问答抽屉("查看全部问答")需页面无其它抽屉遮挡; 评论抽屉最后开。
        reviews_extra: list = []
        qa_extra: list = []
        if harvest_page is not None and harvest_page.url and "item.htm" in harvest_page.url:
            if entry.get("miid_from") in ("footmark_click", "favorite_click"):
                try:
                    from src.extract.qa import parse_qa

                    qa_extra = [q.model_dump() for q in await parse_qa(pid, page=harvest_page)]
                except (CaptchaError, SelectorDriftError):
                    raise  # 风控墙/布局漂移必须上浮给调用方 — 问答是 fine 模式必提取项, 不嵌入 error 静默
                except Exception as exc:
                    qa_extra = [{"error": str(exc)[:120]}]
            try:
                if with_reviews:
                    from src.extract.reviews import parse_reviews_stratified

                    revs = await parse_reviews_stratified(pid, max_reviews=reviews_max,
                                                          keyword=reviews_keyword, page=harvest_page)
                    reviews_extra = [r.model_dump() for r in revs]
            except (CaptchaError, SelectorDriftError):
                raise  # 风控墙/布局漂移必须上浮给调用方 — with_reviews=True 时评论是必提取项, 不嵌入 error 静默
            except Exception as exc:
                reviews_extra = [{"error": str(exc)[:120]}]

        # 同类推荐/看了又看 (近似搜索通道, 2026-08-20): 搜索页被验证码风控, 但详情页
        # 零验证码。从当前详情页 DOM 顺带收集同类商品卡(id+标题+¥) → 零额外流量/零验证码
        # 的"近似搜索"。推荐列表无限长且含泛推荐噪声 → rank_recommendations 排序/过滤/压缩
        # (按耗材关键词打分, 降序, 截断到上限, 防大量清单冲击上下文)。失败静默。
        recommendations: dict = {"items": [], "total_raw": 0, "kept": 0, "dropped_noise": 0, "capped": False}
        try:
            from src.extract.recommend import rank_recommendations
            from src.extract.selectors import RECOMMEND_JS

            raw_rec = await harvest_page.evaluate(RECOMMEND_JS)
            recommendations = rank_recommendations(raw_rec or [])
        except CaptchaError:
            raise  # 撞上风控墙必须上浮 — 推荐虽是可选提取, 墙不能被吞掉后继续
        except Exception as exc:
            get_logger().warning("detail: recommend extraction failed: %s", exc)
    finally:
        # 用户规则清理(必须始终执行, 含异常上浮路径): 本轮收藏的取消收藏 + 关闭弹窗标签页。
        await _cleanup_fetch(entry, page, pid, popup)

    stale = not harvest.get("scope")
    # 分层评价摘要(让"检查分层详细评价"的过程可见, 2026-08-20 用户反馈):
    # 评论若非空, 统计 好/中/差 或 前/中/后段 各自取了几条。
    review_strata: dict | None = None
    if with_reviews and reviews_extra and not any(
        isinstance(r, dict) and "error" in r for r in reviews_extra
    ):
        try:
            rated = [r for r in reviews_extra if r.get("rating") is not None]
            if rated and len(rated) >= len(reviews_extra) * 0.6:
                review_strata = {
                    "mode": "rated",
                    "good": sum(1 for r in reviews_extra if (r.get("rating") or 0) >= 4),
                    "neutral": sum(1 for r in reviews_extra if (r.get("rating") or 0) == 3),
                    "bad": sum(1 for r in reviews_extra if (r.get("rating") or 0) <= 2),
                    "total": len(reviews_extra),
                }
            else:
                n = len(reviews_extra)
                seg = max(1, n // 3)
                review_strata = {
                    "mode": "segmented",
                    "front": len(reviews_extra[0:seg]),
                    "middle": len(reviews_extra[seg:2 * seg]),
                    "back": len(reviews_extra[2 * seg:n]),
                    "total": n,
                    "note": "列表接口无星级 → 按 前/中/后 三段抽样, 覆盖不同时期/倾向",
                }
        except Exception:
            review_strata = None
    return {
        "product_id": pid,
        "url": (harvest_page.url or "")[:220],
        "miid_used": miid_from_url(harvest_page.url or ""),
        "entry": entry,
        "price_observed": price_observed,
        "scope": harvest.get("scope"),
        "panel_found": harvest.get("panelFound"),
        "count": len(normalized),
        "detail_images": normalized,
        "reviews": reviews_extra if with_reviews else None,
        "review_strata": review_strata,
        "qa": qa_extra,
        "recommendations": recommendations,
        # Signal to the caller: mi_id is stale/expired → the favorite flow regenerates one.
        "miid_stale": stale,
        "caveat": (None if not stale else
                   "no .desc-root found — the mi_id is stale. Run taobao_fetch_detail again "
                   "(favorite flow regenerates a fresh one) or taobao_get_miid (auto)."),
    }


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
        # skuBase props values 的真实字段名采样(诊断选项图 URL 在哪个键, 2026-08-19)
        try:
            sb = res.get("skuBase", {}) or {}
            props = sb.get("props", []) or []
            sample = []
            for g in props[:3]:
                vals = g.get("values", []) or []
                sample.append({
                    "group": g.get("name"),
                    "value_keys": sorted(vals[0].keys()) if vals else [],
                    "first_value": (vals[0] if vals else {}),
                })
            evidence["skuBase_values_sample"] = sample
        except Exception as exc:
            evidence["skuBase_sample_error"] = str(exc)
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


async def save_detail_images(product_url_or_id: str, output_dir: str = "", max_images: int = 60,
                             detail: dict | None = None) -> dict:
    """细查后把详情长图下载到本地文件夹(买家离线查看; AI 模型读不了图, 人需要).

    收藏链路(fetch_detail, miid_source='favorite')拿到带 mi_id 页的 .desc-root 详情图
    URL, 再用浏览器会话上下文下载到 output/detail_imgs/<pid>/。只读浏览 + 落盘, 不收藏残留
    (fetch_detail 已 cleanup)、不发消息。WebP 图片, 浏览器/看图软件可直接打开。
    detail: 传入本次 fine 调用已取得的 fetch_detail 结果(含 detail_images + 页面 URL 作
    referer), 则**不再二次走收藏链路** — 一次 fine+save_images 只消耗一次 miid/收藏配额。
    """
    from pathlib import Path

    from src.browser.session import get_session
    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    if detail is None:
        # 独立使用(未在 fine 调用中)时才自行走收藏链路
        detail = await fetch_detail(pid, miid_source="auto")
    urls = detail.get("detail_images") or []
    out_dir = Path(output_dir or f"output/detail_imgs/{pid}")
    out_dir.mkdir(parents=True, exist_ok=True)

    session = get_session()
    page = await session.start()
    referer = (detail.get("url") or "")
    saved: list[str] = []
    failures: list[str] = []
    for i, u in enumerate(urls[:max_images]):
        fname = f"{i + 1:02d}.webp"
        try:
            resp = await page.request.get(u, headers={"Referer": referer}, timeout=30000)
            if resp.ok:
                (out_dir / fname).write_bytes(await resp.body())
                saved.append(fname)
            else:
                failures.append(f"{fname}:http{resp.status}")
        except Exception as exc:
            failures.append(f"{fname}:{str(exc)[:40]}")
    return {
        "product_id": pid,
        "count": len(urls),
        "saved": len(saved),
        "failed": len(failures),
        "dir": str(out_dir),
        "images": [str(out_dir / f) for f in saved],
        "failures": failures[:8],
        "miid_stale": detail.get("miid_stale"),
    }


async def extract_recommendations(product_url_or_id: str, max_items: int = 12, min_score: int = 1) -> dict:
    """[A2 游走原语] 直接 goto 商品详情页(粗查路径, 不走足迹/收藏/细查), 提取同类推荐.

    2026-08-20 用户设计: A2(多轮游走)最接近人类行为 — 淘宝推荐算法的价值在跨页
    迭代(进一个新详情页 → 推荐给新同类 → 再进)。游走原语必须轻量:
    - 不用足迹/收藏链路(fine) — 慢 + 耗收藏配额 + 足迹链路标签管理复杂
    - 不模拟点击进细查 — 浪费上下文
    - 直接 goto item.htm(粗查路径) — 快; 但无 mi_id 非个性化, 推荐质量需实机验证

    返回 rank_recommendations 的 {items, total_raw, kept, dropped_noise, capped}。
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    # 推荐区块在主文档最底部: 粗查也先滚到底触发懒加载, 再提取, 最后滚回顶部。
    from src.browser.scroll import scroll_to_bottom

    await scroll_to_bottom(page)
    await human_delay(1.2, 2.0)
    from src.extract.recommend import rank_recommendations
    from src.extract.selectors import RECOMMEND_JS

    raw_rec = await page.evaluate(RECOMMEND_JS)
    result = rank_recommendations(raw_rec or [], max_items=max_items, min_score=min_score)
    # URL 诊断(2026-08-20 用户疑点): 粗查是 URL 拼接 goto item.htm(无 mi_id 参数),
    # 但实测页面 URL 可能被淘宝 JS 注入 mi_id(登录态/SPA 重写)。记录实际落地 URL,
    # 判断"URL 拼接是否真的进入无 mi_id 页面" — 这决定 A2 游走原语的语义。
    try:
        result["landed_url"] = (page.url or "")[:220]
        from src.extract.miid import miid_from_url

        result["landed_has_miid"] = bool(miid_from_url(page.url or ""))
    except Exception:
        pass
    return result


async def probe_entry(product_url_or_id: str, entry: str = "url") -> dict:
    """[一次性诊断] 用指定进入方式访问商品详情页, 捕获 详情/推荐/评论/问答/优惠价.

    2026-08-20 用户设计: 研究三种粗查进入方式(直接输入URL / 推荐goto / 搜索goto)
    进入的详情页差异。entry 取值:
      url       = 直接输入 URL(裸 item.htm?id=X, 无 referer)
      recommend = 从推荐上下文 goto(同 url, 但语义为推荐进入)
      search    = 模拟搜索进入(带 referer = 搜索页 URL)
    每种进入方式只跑一次(一次性对比实验, 不重复)。滚动到底触发推荐区懒加载,
    再 evaluate ENTRY_PROBE_JS 捕获五项信号。零写操作, captcha 仍人工交接。
    """
    from urllib.parse import quote

    from src.browser.pacing import human_delay
    from src.browser.session import get_session
    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"

    entry = str(entry or "url").strip().lower()
    goto_kwargs: dict = {"wait_until": "domcontentloaded"}
    if entry == "search":
        # 模拟搜索进入: 带 referer=搜索页(历史经验: referer 影响 SSR 详情渲染)
        goto_kwargs["referer"] = f"https://s.taobao.com/search?q={quote(pid)}&tab=all"
    elif entry == "recommend":
        # 推荐进入: 从推荐上下文 goto(裸 URL, 但语义为推荐来源)
        pass
    # entry == "url": 裸 URL 直接进入

    await page.goto(url, **goto_kwargs)
    await session.guard_captcha(page)

    # 滚动到底触发推荐区懒加载(推荐区块在页面最底部), 再回顶部。
    from src.browser.scroll import scroll_to_bottom

    await scroll_to_bottom(page)
    await human_delay(1.0, 1.8)
    from src.extract.selectors import ENTRY_PROBE_JS

    probe = await page.evaluate(ENTRY_PROBE_JS)
    probe["entry"] = entry
    probe["goto_url"] = url
    probe["referer"] = goto_kwargs.get("referer")
    # 落地 URL(可能被淘宝 JS 重写注入 mi_id/spm)
    probe["landed_url"] = (page.url or "")[:240]
    return probe
