"""Centralized layout-coupled selectors + drift guard (CLAUDE.md Phase 6).

Every selector / embedded-data anchor / extraction snippet that depends on
Taobao's (drifting) page structure lives HERE, so a layout change is a one-file
patch. Use require() to fail loudly with SelectorDriftError instead of returning
a silently-wrong empty result.
"""

from __future__ import annotations

from src.errors import SelectorDriftError

# --- product detail: embedded ICE.js context (var b = {...loaderData.home.data.res...})
ICE_ANCHORS = ("var b = {", "window.__ICE_APP_CONTEXT__", "var b={")
RES_SKU_BASE_KEY = "skuBase"
RES_SKU_CORE_KEY = "skuCore"          # res.skuCore.sku2info

# --- reviews: rendered DOM cards (no rate XHR on the new page)
REVIEW_CARD_SELECTOR = '[class*="Comment--"]'
REVIEW_EXTRACT_JS = r"""() => {
  const cards = [...document.querySelectorAll('[class*="Comment--"]')];
  return cards.map(c => {
    const content = c.querySelector('[class*="content--"]');
    const meta = c.querySelector('[class*="meta--"]');
    const photoImgs = c.querySelectorAll('[class*="album--"] img, [class*="photo--"] img').length;
    return {
      text: content ? content.innerText.trim() : '',
      meta: meta ? meta.innerText.replace(/\n+/g, ' ').trim() : '',
      has_images: photoImgs > 0,
    };
  }).filter(r => r.text);
}"""

# --- search: pure-DOM result cards (climb to smallest ¥+付款 ancestor)
SEARCH_EXTRACT_JS = r"""() => {
  const sels=['a[href*="item.htm"]','a[href*="//item.taobao.com"]','a[href*="detail.tmall.com"]'];
  let links=[]; sels.forEach(s=>links.push(...document.querySelectorAll(s)));
  links=[...new Set(links)];
  const seen=new Set(); const rows=[];
  for(const a of links){
    const m=(a.getAttribute('href')||'').match(/[?&]id=(\d{6,})/); const id=m?m[1]:null;
    if(!id||seen.has(id)) continue;
    let card=a, found=null;
    for(let i=0;i<8;i++){ if(!card) break; const t=card.innerText||'';
      if(t.includes('¥')&&(t.includes('付款')||t.includes('人付'))&&t.length<260){found=card;break;} card=card.parentElement; }
    if(!found) continue; seen.add(id);
    rows.push({id, text:(found.innerText||'').replace(/\s+/g,' ').trim()});
  }
  return rows;
}"""

# --- Q&A: rendered DOM (best-effort)
QA_EXTRACT_JS = r"""() => {
  const items = [...document.querySelectorAll('[class*="askAnswerItem"], [class*="qaItem"], [class*="QA"]')];
  return items.map(it => {
    const q = it.querySelector('[class*="question"], [class*="ask"]');
    const a = it.querySelector('[class*="answer"], [class*="reply"]');
    return { question: q ? q.innerText.trim() : '', answer: a ? a.innerText.trim() : '' };
  }).filter(x => x.question);
}"""


# --- reviews "view all" drawer (查看全部评价 opens an in-page Drawer, no URL change) ---
VIEW_ALL_LABELS = ("查看全部评价", "全部评价")
DRAWER_SELECTOR = '[class*="Drawer--"]'
REVIEW_DRAWER_SCROLL_JS = r"""() => {
  const drawer = document.querySelector('[class*="Drawer--"]');
  if(!drawer) return -1;
  let best=null, bestGap=0;
  drawer.querySelectorAll('*').forEach(e=>{
    const gap=e.scrollHeight-e.clientHeight; const st=getComputedStyle(e).overflowY;
    if(gap>bestGap && (st==='auto'||st==='scroll')){bestGap=gap;best=e;}
  });
  const el=best||drawer; el.scrollTop=el.scrollHeight; return bestGap;
}"""

# Auto-generated "default good review" boilerplate — exclude from real written reviews.
DEFAULT_REVIEW_MARKERS = (
    "该用户觉得商品非常好", "此用户没有填写评价", "此用户没有填写文字评价",
    "此用户未填写评价", "未填写评价内容", "系统默认好评", "系统默认评价",
    "默认好评", "默认评价", "评价方未及时", "卖家未及时",
)


# --- full detail (详情图长图) recon: how does the bottom 详情 section load? ---
# Lightweight — targeted selectors only, never a full-DOM scan (B.3 wedge rule).
DESC_RECON_JS = r"""() => {
  const out = { iframes: [], alicdn: [], descElCount: 0, imgTotal: 0 };
  document.querySelectorAll('iframe').forEach(f => {
    const s = f.src || f.getAttribute('data-src') || '';
    if (s) out.iframes.push(s.slice(0, 220));
  });
  const imgs = [...document.querySelectorAll('img')];
  out.imgTotal = imgs.length;
  const srcs = [];
  for (const i of imgs) {
    const s = i.getAttribute('src') || i.getAttribute('data-src') || i.getAttribute('data-lazyload') || '';
    if (/alicdn|taobaocdn/.test(s)) srcs.push(s);
  }
  out.alicdn = [...new Set(srcs)].slice(0, 24);
  out.descElCount = document.querySelectorAll(
    '[class*="desc"], [id*="desc"], [class*="Description"], [class*="detail-title"]'
  ).length;
  return out;
}"""

# --- full detail: fetch the 详情 via mtop.taobao.detail.getdetail (page's own lib.mtop) ---
# The H5 desc shell (h5.m.taobao.com/awp/core/detail.htm) pulls its pictures from this API;
# calling it in-page reuses the page's signed mtop SDK (same pattern as cart's addBag).
GETDETAIL_JS = r"""async (itemId) => {
  const lib = window.lib && window.lib.mtop;
  if (!lib) return { ok: false, err: 'no lib.mtop' };
  try {
    const res = await lib.request({
      api: 'mtop.taobao.detail.getdetail', v: '6.0', type: 'GET', ecode: 1, dataType: 'json',
      data: { id: String(itemId) }
    });
    const data = (res && res.data) || {};
    const s = String(data.description || data.desc || data.content || '');
    const srcs = [];
    const re = /<img[^>]+src=["']([^"']+)["']/gi;
    let m; while ((m = re.exec(s)) !== null) srcs.push(m[1]);
    const alicdn = srcs.filter(x => /alicdn|taobaocdn/.test(x));
    return { ok: true, dataKeys: Object.keys(data).slice(0, 30), descLen: s.length,
             imgCount: srcs.length, alicdnCount: alicdn.length, imgSample: alicdn.slice(0, 12),
             descHead: s.slice(0, 240) };
  } catch (e) { return { ok: false, err: String(e) }; }
}"""

# Harvest images from a rendered (desc shell) page — targeted, no full-DOM scan.
DESC_HARVEST_JS = r"""() => {
  const srcs = [];
  document.querySelectorAll('img').forEach(i => {
    const s = i.getAttribute('src') || i.getAttribute('data-src') || i.getAttribute('data-lazyload') || '';
    if (/alicdn|taobaocdn/.test(s)) srcs.push(s);
  });
  return { total: document.querySelectorAll('img').length, alicdn: [...new Set(srcs)].slice(0, 20) };
}"""

# Harvest the 详情 strip from the PC page — the mechanism confirmed from the
# gao-tu extension (github.com/CJMF-i/gao-tu): the desc renders into
# #description .content (legacy) or .desc-root (new SSR), and lazy imgs carry
# the real URL in data-ks-lazyload / data-src, falling back to src.
DESC_SCOPE_JS = r"""() => {
  const out = { descId: false, descRoot: false, scope: null, imgs: [] };
  const desc = document.getElementById('description');
  if (desc) {
    out.descId = true;
    const c = desc.getElementsByClassName('content')[0];
    if (c) { out.scope = '#' + desc.id + ' .content'; _grab(c, out); return out; }
  }
  const root = document.querySelector('.desc-root');
  if (root) {
    out.descRoot = true;
    out.scope = '.desc-root';
    _grab(root, out);
  }
  return out;
  function _grab(scope, out) {
    const seen = new Set();
    scope.querySelectorAll('img').forEach(e => {
      const u = e.getAttribute('data-ks-lazyload') || e.getAttribute('data-src') || e.getAttribute('src') || '';
      if (u && !seen.has(u)) { seen.add(u); out.imgs.push(u); }
    });
  }
}"""

# --- full detail (详情图长图): the new SSR page renders 详情 inside the SKU-panel scroll
# container (#tbpcDetail_SkuPanelBody). The maintained userscript (greasyfork 460143,
# 2026-01) scrolls that inner div to trigger lazy images, then harvests
# .desc-root / .content-detail / [class*="desc-"] / [class*="detail-"] imgs (width>=700).
DESC_PANEL_JS = r"""async () => {
  const out = { panelFound: false, panelScrollable: false, scope: null, imgs: [], imgsAnyWidth: [] };
  const panel = document.getElementById('tbpcDetail_SkuPanelBody');
  if (panel) {
    out.panelFound = true;
    const sh = panel.scrollHeight, ch = panel.clientHeight;
    out.panelScrollable = sh > ch;
    let top = 0;
    while (top < sh - ch) {
      panel.scrollTop = top;
      top += 300;
      await new Promise(r => setTimeout(r, 120));
    }
    panel.scrollTop = sh;
    await new Promise(r => setTimeout(r, 900));
  }
  const sels = ['.desc-root', '.content-detail', '[class*="desc-"]', '[class*="detail-"]'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el) {
      out.scope = s;
      const seen = new Set(), seenAny = new Set();
      el.querySelectorAll('img').forEach(e => {
        const u = e.getAttribute('data-ks-lazyload') || e.getAttribute('data-src') || e.getAttribute('src') || '';
        if (!u) return;
        if (e.width >= 700 && !seen.has(u)) { seen.add(u); out.imgs.push(u); }
        if (!seenAny.has(u)) { seenAny.add(u); out.imgsAnyWidth.push(u); }
      });
      break;
    }
  }
  return out;
}"""

# Click the 宝贝详情/详情 tab (the 详情 content is behind a tab on the new SSR page),
# then harvest the desc container. Records the tabs found + which was clicked + result.
DESC_TAB_PROBE_JS = r"""async () => {
  const out = { tabs: [], clicked: null, harvest: null };
  const cands = [...document.querySelectorAll('[role="tab"], button, [class*="tab"], [class*="Tab"]')];
  const seen = new Set();
  for (const el of cands) {
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (t && t.length <= 14 && /详情|宝贝详情|图文|Detail/i.test(t) && !seen.has(t)) {
      seen.add(t);
      out.tabs.push({ text: t, cls: String(el.className || '').slice(0, 60), tag: el.tagName });
    }
  }
  for (const el of cands) {
    const t = (el.innerText || '').trim();
    if (/^宝贝详情$|详情/.test(t)) { el.click(); out.clicked = t; break; }
  }
  await new Promise(r => setTimeout(r, 3000));
  const sels = ['.desc-root', '.content-detail', '[class*="desc-"]', '[class*="detail-"]'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el) {
      const h = { scope: s, imgs: [], imgsAny: [] };
      const seen2 = new Set(), seenAny = new Set();
      el.querySelectorAll('img').forEach(e => {
        const u = e.getAttribute('data-ks-lazyload') || e.getAttribute('data-src') || e.getAttribute('src') || '';
        if (!u) return;
        if (e.width >= 700 && !seen2.has(u)) { seen2.add(u); h.imgs.push(u); }
        if (!seenAny.has(u)) { seenAny.add(u); h.imgsAny.push(u); }
      });
      out.harvest = h;
      break;
    }
  }
  return out;
}"""

# Light DOM snapshot for the manual-watch tool (cheap selectors only — no full scans).
SNAPSHOT_JS = r"""() => {
  const out = { panel: !!document.getElementById('tbpcDetail_SkuPanelBody'),
                descRoot: !!document.querySelector('.desc-root'),
                descId: !!document.getElementById('description'),
                imgs: 0, alicdnSample: [] };
  const seen = new Set();
  document.querySelectorAll('img').forEach(e => {
    const u = e.getAttribute('src') || e.getAttribute('data-ks-lazyload') || e.getAttribute('data-src') || '';
    if (/alicdn|taobaocdn/.test(u)) { if (!seen.has(u)) { seen.add(u); if (out.alicdnSample.length < 8) out.alicdnSample.push(u.slice(0, 90)); } }
  });
  out.imgs = seen.size;
  return out;
}"""

# --- mi_id: recon the homepage for a stable ad/product position to auto-click ---
# Light: anchors to product pages + ad-like containers. Targeted queries only.
HOME_AD_RECON_JS = r"""() => {
  const out = { url: location.href, anchors: [], adLike: [] };
  const seen = new Set();
  document.querySelectorAll('a[href*="item.htm"], a[href*="detail.tmall.com"]').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (seen.has(href)) return; seen.add(href);
    if (out.anchors.length >= 25) return;
    out.anchors.push({
      href: href.slice(0, 220),
      hasMiId: /mi_id=/.test(href),
      hasSpm: /spm=/.test(href),
      text: (a.innerText || '').trim().slice(0, 18),
      cls: String(a.className || '').slice(0, 55),
    });
  });
  const adCls = ['ad', 'banner', 'promo', 'Recommend', 'recommend', 'feed', 'focus', 'cps'];
  const sel = adCls.map(k => `[class*="${k}"]`).join(',');
  document.querySelectorAll(sel).forEach(e => {
    if (e.querySelector && e.querySelector('a') && out.adLike.length < 30) {
      const link = e.querySelector('a');
      const href = link ? (link.getAttribute('href') || '') : '';
      out.adLike.push({ tag: e.tagName, cls: String(e.className || '').slice(0, 70), text: (e.innerText || '').trim().slice(0, 26), href: href.slice(0, 170) });
    }
  });
  return out;
}"""

# --- deep_price: read the live 平台加补后 (after-subsidy) price after selecting a variant ---
SUBSIDY_PRICE_JS = r"""() => {
  const all=[...document.querySelectorAll('*')];
  let best=null, bestLen=999;
  for (const e of all) {
    const t=(e.innerText||'').replace(/\s+/g,'');
    if (t.includes('平台加补后') && /\d/.test(t) && t.length<40 && t.length<bestLen) { best=t; bestLen=t.length; }
  }
  if (!best) return null;
  const m = best.match(/平台加补后[¥￥]?([\d.]+)/);
  return m ? m[1] : null;
}"""


def require(value, step: str, selector: str | None = None):
    """Return value, or raise SelectorDriftError if it's falsy/empty.

    Use at the boundary where a layout-dependent extraction *must* have produced
    something — so a changed page surfaces a clear, patchable error instead of a
    silently-empty result.
    """
    empty = value is None or (hasattr(value, "__len__") and len(value) == 0)
    if empty:
        raise SelectorDriftError(step=step, selector=selector)
    return value
