"""收藏(collect)链路 — 为详情抓取生成"自然、固定位置、可复现"的 mi_id。

原理(用户设计,2026-08-18):与其随机点击广告位取 mi_id,不如在确认目标商品后:
  1. 收藏(或加购)该商品 → 它进入"我的收藏"的近似固定位置(最新收藏在最前);
  2. 从收藏夹模拟点击它 → 生成带完整参数(含 mi_id)的点击链接;
  3. 用该 mi_id 抓详情,且每次查询详情都重新走一遍 → 每条详情数据都配一个
     从"真人数据链"(自己收藏夹)点击出来的新 mi_id,规避随机/广告点击的可疑足迹。

效率低没关系(单次流程 ≈ 收藏 + 进收藏夹 + 点击 + 抓详情)。
"""

from __future__ import annotations


# 商品页"收藏/已收藏"按钮定位(限 button/span/div/i/a,非全 DOM)
FAV_BUTTON_JS = r"""() => {
  const out = { found: false };
  const cands = [...document.querySelectorAll('button, span, div, i, a')];
  for (const el of cands) {
    const t = (el.innerText || '').trim();
    if ((t === '收藏' || t === '已收藏') && el.children.length <= 2) {
      out.found = true; out.text = t; out.cls = String(el.className || '').slice(0, 60); out.tag = el.tagName;
      break;
    }
  }
  return out;
}"""

FAV_CLICK_JS = r"""() => {
  const cands = [...document.querySelectorAll('button, span, div, i, a')];
  for (const el of cands) {
    const t = (el.innerText || '').trim();
    if ((t === '收藏' || t === '已收藏') && el.children.length <= 2) {
      el.click();
      return { clicked: true, text: t, cls: String(el.className || '').slice(0, 60) };
    }
  }
  return { clicked: false };
}"""

# 收藏夹页面:商品链接 + 位置 + 是否带 mi_id
FAV_LIST_JS = r"""() => {
  const out = { itemLinks: [], total: 0 };
  const seen = new Set();
  document.querySelectorAll('a[href*="item.htm"], a[href*="detail.tmall.com"]').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (seen.has(href)) return; seen.add(href); out.total++;
    if (out.itemLinks.length < 15) {
      out.itemLinks.push({
        href: href.slice(0, 220), text: (a.innerText || '').trim().slice(0, 20),
        cls: String(a.className || '').slice(0, 40), hasMiId: /mi_id=/.test(href),
      });
    }
  });
  return out;
}"""


COLLECT_URL = "https://i.taobao.com/my_itaobao/itao-tool/collect"


async def _item_in_collect(page, pid: str) -> bool:
    """Is the product present in the 收藏夹? Broad matching (href / data attrs) + scroll."""
    await page.goto(COLLECT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    await page.wait_for_timeout(1000)
    try:
        if (await page.locator(f'a[href*="{pid}"]').count()) > 0:
            return True
        if (await page.locator(f'[data-item-id*="{pid}"], [data-id*="{pid}"], [data-tmall-id*="{pid}"]').count()) > 0:
            return True
    except Exception:
        pass
    return False


def _btn_state(html: str) -> str:
    """Infer favorite state from #collectBtn outerHTML: 'favorited' | 'not' | 'unknown'."""
    if not html:
        return "unknown"
    if "icon-taobaoyishoucang" in html or "已收藏" in html or "rgb(255, 80, 0)" in html:
        return "favorited"
    if "icon-taobaoshoucang" in html or ">收藏<" in html:
        return "not"
    return "unknown"


async def _btn_outer(page) -> str:
    try:
        return await page.evaluate(
            """() => { const e = document.getElementById('collectBtn'); return e ? e.outerHTML : ''; }"""
        )
    except Exception:
        return ""


async def ensure_favorited(page, pid: str) -> dict:
    """Ensure the product is favorited WITHOUT disturbing an existing favorite.

    Judge by the #collectBtn state (favorited = icon-taobaoyishoucang / 已收藏 / orange
    rgb(255,80,0)). If ALREADY favorited → DO NOT TOUCH (preserves its folder/position —
    never remove+re-favorite). Only when clearly NOT favorited do we click 收藏 (can only
    ADD), then verify the button flips. added_by_us=True ONLY when WE added it this round
    (the caller must un-favorite it afterwards — cleanup).
    """
    await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    btn = page.locator("#collectBtn").first
    if await btn.count() == 0:
        return {"already": None, "state": "no_collect_btn", "added_by_us": False, "clicked": 0}
    state0 = _btn_state(await _btn_outer(page))
    if state0 == "favorited":
        return {"already": True, "state": "already", "added_by_us": False, "clicked": 0}
    if state0 == "unknown":  # don't gamble — double-check the 收藏夹 before touching
        try:
            present = await _item_in_collect(page, pid)
        except Exception:
            present = False
        if present:
            return {"already": True, "state": "already_via_list", "added_by_us": False, "clicked": 0}
    # clearly not favorited → add (can only add)
    try:
        from src.browser.pacing import human_click

        await human_click(page, btn)
    except Exception as exc:
        return {"already": False, "state": "click_failed", "added_by_us": False, "clicked": 0,
                "error": str(exc)}
    await page.wait_for_timeout(1200)
    state1 = _btn_state(await _btn_outer(page))
    added = state1 == "favorited"
    return {
        "already": False,
        "state": "added" if added else "add_unverified",
        "added_by_us": added,
        "clicked": 1,
        "btn_state": state1,
    }


async def ensure_unfavorited(page, pid: str) -> dict:
    """Remove the favorite we added this round (cleanup — never leave residue).

    Per user rule: if WE favorited it this round, un-favorite after querying. Uses the
    button color signal; only clicks when the item is currently favorited.
    """
    await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    btn = page.locator("#collectBtn").first
    if await btn.count() == 0:
        return {"state": "no_btn", "clicked": False}
    if _btn_state(await _btn_outer(page)) != "favorited":
        return {"state": "not_favorited", "clicked": False}
    try:
        from src.browser.pacing import human_click

        await human_click(page, btn)
    except Exception as exc:
        return {"state": "click_failed", "clicked": False, "error": str(exc)}
    await page.wait_for_timeout(1200)
    after = _btn_state(await _btn_outer(page))
    return {"state": "removed" if after != "favorited" else "remove_failed", "clicked": True}


async def click_from_favorites(page, pid: str, added_by_us: bool = True) -> dict:
    """Open the 收藏夹, click the target card, return the opened item URL + fresh mi_id.

    A fresh favorite sits at the TOP of the list → for added_by_us we wait for the FIRST
    card to render (event-driven, no fixed 15s wait + no scroll-to-find — user's
    simplification) and click it directly. The click opens a NEW TAB with a tracking URL
    (spm=tbpc.mytb_itemcollect.item.goods, fresh mi_id) — validated live. For
    added_by_us=False we still click the top card but VERIFY the opened id == pid; a
    mismatch means the target is buried elsewhere → the caller falls back (we never
    disturb/re-order existing favorites).
    """
    from urllib.parse import parse_qs, urlparse

    from src.extract.miid import miid_from_url

    await page.goto(COLLECT_URL, wait_until="domcontentloaded")
    # Event-driven: wait for the first goodsItem card instead of a fixed long sleep.
    first = page.locator('[class*="goodsItem"]').first
    try:
        await first.wait_for(state="visible", timeout=18000)
    except Exception:
        try:  # a light scroll may be what triggers the JS list render
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2500)
            await first.wait_for(state="visible", timeout=10000)
        except Exception:
            return {"url": None, "reason": "no goodsItem cards rendered"}
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    card = page.locator('[class*="goodsItem"]').first
    if await card.count() == 0:
        return {"url": None, "reason": "no goodsItem cards rendered"}
    # Click the TITLE inside the card (the natural product link target) with human jitter —
    # a random point over the whole card can hit the hover overlay buttons (进入店铺/按图找
    # 相似) or the delete button, which don't navigate to the item.
    try:
        from src.browser.pacing import human_click

        title = card.locator('[class*="title"]').first
        target = title if await title.count() > 0 else card
        async with page.expect_popup(timeout=20000) as pi:
            await human_click(page, target)
        popup = await pi.value
        try:
            await popup.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await popup.wait_for_timeout(2500)
        url = (popup.url or "") if not popup.is_closed() else ""
    except Exception as exc:
        return {"url": None, "reason": f"click/popup: {str(exc)[:100]}"}

    miid = miid_from_url(url)
    qs = parse_qs(urlparse(url or "").query)
    opened_id = (qs.get("id") or [None])[0]
    return {
        "url": url[:240] if url else None,
        "mi_id": miid,
        "opened_id": opened_id,
        "matches_target": (opened_id == pid) if opened_id else False,
        "popup": popup,
    }


# 收藏后出现的弹窗/Toast(判断收藏状态的核心信号)
FAV_POPUP_JS = r"""() => {
  const out = { popups: [], bodyHas: {} };
  const sels = ['[class*="toast"]','[class*="Toast"]','[class*="message"]','[class*="Message"]',
                '[class*="tip"]','[class*="Tip"]','[class*="dialog"]','[class*="Dialog"]',
                '[class*="notify"]','[class*="Notify"]','[class*="success"]','[class*="Success"]'];
  const seen = new Set();
  sels.forEach(s => document.querySelectorAll(s).forEach(e => {
    const t = (e.innerText || '').trim();
    if (t && !seen.has(t) && t.length < 60) {
      seen.add(t);
      const vis = !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
      out.popups.push({ sel: s, text: t, cls: String(e.className || '').slice(0, 50), visible: vis });
    }
  }));
  const body = document.body ? document.body.innerText : '';
  for (const kw of ['收藏成功','收藏宝贝成功','已收藏','取消收藏','已取消收藏','取消成功','收藏夹','重复收藏']) {
    out.bodyHas[kw] = body.includes(kw);
  }
  return out;
}"""


async def _read_fav_popup(page) -> dict:
    """Snapshot the favorite popup/toast + body keywords right now."""
    try:
        return await page.evaluate(FAV_POPUP_JS)
    except Exception:
        return {}


async def recon_collect(target_pid: str = "") -> dict:
    """One-pass 收藏夹 recon: JS-rendered goodsItem grid + the click-generated tracking URL.

    Waits for the grid (~12s + scroll), dumps card samples, then clicks the FIRST card
    (a fresh favorite sits at top) and captures the NEW-TAB tracking URL with its fresh
    mi_id (spm=tbpc.mytb_itemcollect.item.goods) — the exact mechanism the favorite
    fetch_detail uses. Pass target_pid to report whether the top card is that item.
    Read-only (a click + popup open, no writes).
    """
    from urllib.parse import parse_qs, urlparse

    from src.browser.session import get_session
    from src.extract.miid import miid_from_url

    session = get_session()
    page = await session.start()
    out: dict = {"target_pid": target_pid}
    await page.goto(COLLECT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(12000)
    for _ in range(4):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await page.wait_for_timeout(1200)
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    out["cards"] = await page.evaluate(
        """() => {
          const cs = [...document.querySelectorAll('[class*="goodsItem"]')];
          return {
            count: cs.length,
            firstTitle: cs.length ? ((cs[0].querySelector('[class*="title"]') || {}).innerText || '').slice(0, 44) : '',
            sample: cs.slice(0, 2).map(e => ({ cls: String(e.className || '').slice(0, 40), html: (e.outerHTML || '').slice(0, 300) })),
          };
        }"""
    )
    try:
        from src.browser.pacing import human_click

        card = page.locator('[class*="goodsItem"]').first
        if await card.count() > 0:
            title = card.locator('[class*="title"]').first
            target = title if await title.count() > 0 else card
            async with page.expect_popup(timeout=20000) as pi:
                await human_click(page, target)
            popup = await pi.value
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await popup.wait_for_timeout(2500)
            url = (popup.url or "") if not popup.is_closed() else ""
            out["clicked"] = {
                "url": url[:240],
                "mi_id": miid_from_url(url),
                "opened_id": (parse_qs(urlparse(url or "").query).get("id") or [None])[0],
                "matches_target": bool(target_pid and target_pid in (url or "")),
            }
            if not popup.is_closed():
                try:
                    await popup.close()
                except Exception:
                    pass
        else:
            out["clicked"] = {"error": "no goodsItem cards rendered"}
    except Exception as exc:
        out["clicked"] = {"error": str(exc)[:120]}
    return out


async def recon_favorite(product_url_or_id: str) -> dict:
    """诊断:收藏按钮真实结构 + 点击效果 + 收藏夹页面结构(用于修 ensure_favorited)。"""
    from src.browser.session import get_session
    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    out: dict = {"pid": pid}

    # 1) 商品页:收藏按钮 outerHTML + 父级
    await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    try:
        out["fav_button_dom"] = await page.evaluate(
            """() => {
              const els = [...document.querySelectorAll('[class*="RightButtonList"]')];
              return els.slice(0, 8).map(e => ({
                text: (e.innerText||'').trim().slice(0,24),
                cls: String(e.className||'').slice(0,60),
                outer: (e.outerHTML||'').slice(0,300),
              }));
            }"""
        )
    except Exception as exc:
        out["fav_button_dom_error"] = str(exc)

    # 2) 点 #collectBtn,并按时间采样弹窗(判断收藏状态的核心)
    try:
        out["popup_before"] = await _read_fav_popup(page)
        btn = page.locator("#collectBtn").first
        if await btn.count() > 0:
            await btn.click(timeout=6000)
            out["clicked"] = True
            samples = []
            for delay in (0.3, 0.9, 1.8, 3.0):
                await page.wait_for_timeout(int(delay * 1000) if delay < 1 else 600)
                samples.append({"t": delay, **await _read_fav_popup(page)})
            out["popup_samples"] = samples
        else:
            out["clicked"] = False
            out["reason"] = "no #collectBtn"
    except Exception as exc:
        out["click_error"] = str(exc)

    # 3) 收藏夹页面:真实链接结构(任意 href)+ 页面文本
    await page.goto(COLLECT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(4500)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    try:
        out["collect_all_anchors"] = await page.evaluate(
            """() => {
              const out = { anchors: [], bodyLen: 0, bodyHead: '' };
              out.bodyLen = document.body ? document.body.innerText.length : 0;
              out.bodyHead = (document.body ? document.body.innerText : '').slice(0, 120);
              const seen = new Set();
              document.querySelectorAll('a').forEach(a => {
                const href = a.getAttribute('href') || '';
                if (seen.has(href)) return; seen.add(href);
                if (out.anchors.length < 25) {
                  out.anchors.push({ href: href.slice(0, 180), text: (a.innerText||'').trim().slice(0,16), cls: String(a.className||'').slice(0,36) });
                }
              });
              return out;
            }"""
        )
        # #collectBtn 点击后的收藏态信号:按钮 DOM 变化
        await page.goto(f"https://item.taobao.com/item.htm?id={pid}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        out["collectbtn_before"] = await page.evaluate(
            """() => { const e = document.getElementById('collectBtn'); return e ? e.outerHTML.slice(0, 500) : null; }"""
        )
        btn = page.locator("#collectBtn").first
        if await btn.count() > 0:
            await btn.click(timeout=6000)
            await page.wait_for_timeout(800)
            out["collectbtn_after"] = await page.evaluate(
                """() => { const e = document.getElementById('collectBtn'); return e ? e.outerHTML.slice(0, 500) : null; }"""
            )
            out["toast_after"] = await _read_fav_popup(page)
    except Exception as exc:
        out["collect_detail_error"] = str(exc)

    return out
