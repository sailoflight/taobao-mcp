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
