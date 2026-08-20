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

# --- detail-page 同类推荐/看了又看 (近似搜索通道, 2026-08-20) ---
# 搜索页被验证码风控(每次搜索都弹滑块), 但详情页(coarse/fine)零验证码。详情页底部
# 通常有 看了又看/猜你喜欢/同类商品 区块, 指向其它同类商品卡。这里从当前详情页 DOM
# 收集商品卡: 链接(id) + 标题 + ¥价格, 排除当前商品自身 → 作为"近似搜索"的同类候选。
# 关键: 推荐区块在主文档最底部, 主文档不滚到底则推荐卡未渲染 → 先滚到底触发渲染,
# 等懒加载, 再收集, 最后滚回顶部。卡片特征: 含标题 + ¥(无需"付款", 不是搜索结果卡)。
RECOMMEND_JS = r"""async () => {
  // 1) 主文档滚到底触发推荐区懒加载(推荐区块在页面最底部)
  window.scrollTo(0, document.documentElement.scrollHeight);
  await new Promise(r => setTimeout(r, 1200));
  window.scrollTo(0, document.documentElement.scrollHeight);
  await new Promise(r => setTimeout(r, 1200));

  // 2) 收集商品卡链接
  const curId = (location.href.match(/[?&]id=(\d{6,})/) || [])[1] || null;
  const sels=['a[href*="item.htm"]','a[href*="//item.taobao.com"]','a[href*="detail.tmall.com"]'];
  let links=[]; sels.forEach(s=>links.push(...document.querySelectorAll(s)));
  links=[...new Set(links)];
  const seen=new Set(); const rows=[];
  for(const a of links){
    const m=(a.getAttribute('href')||'').match(/[?&]id=(\d{6,})/); const id=m?m[1]:null;
    if(!id || id===curId || seen.has(id)) continue;
    let card=a, found=null;
    for(let i=0;i<8;i++){ if(!card) break; const t=card.innerText||'';
      if(t.includes('¥') && t.length<300){ found=card; break; } card=card.parentElement; }
    if(!found) continue; seen.add(id);
    const text=(found.innerText||'').replace(/\s+/g,' ').trim().slice(0,160);
    // 价格: 取第一个 ¥/￥ 金额
    const pm=(text.match(/[¥￥]\s*(\d+(?:\.\d+)?)/) || [])[1] || null;
    rows.push({id, text, price: pm ? parseFloat(pm) : null});
  }

  // 3) 滚回顶部, 不影响后续操作
  window.scrollTo(0, 0);
  return rows;
}"""


# --- reviews "view all" drawer (查看全部评价 opens an in-page Drawer, no URL change) ---
VIEW_ALL_LABELS = ("查看全部评价", "全部评价")
DRAWER_SELECTOR = '[class*="Drawer--"]'

# --- 粗查入口对比实验(2026-08-20): 一次 evaluate 检查 详情/推荐/评论/问答/优惠价 ---
# 用于判定"某种进入方式进入的详情页具备哪些内容", 决定 A2 游走原语的进入语义。
ENTRY_PROBE_JS = r"""() => {
  const out = {
    url: (location.href || '').slice(0, 240),
    has_miid: /[?&]mi_id=/.test(location.href),
    has_spm: /[?&]spm=/.test(location.href),
    detail: { root: false, imgs: 0, scope: null },
    recommend: { anchors: 0, cards: 0 },
    review: { markers: 0, drawer: false },
    qa: { items: 0 },
    price: { promo: null, orig: null, seen: [] },
  };
  // 详情: .desc-root / #description .content / 详情图
  const dr = document.querySelector('.desc-root, .content-detail, [class*="desc-"], [class*="detail-"]');
  if (dr) {
    out.detail.root = true;
    out.detail.scope = String(dr.className || dr.id || '').slice(0, 40);
    out.detail.imgs = dr.querySelectorAll('img').length;
  }
  // 推荐: 商品链接数(近似推荐区块是否渲染)
  const links = document.querySelectorAll('a[href*="item.htm"], a[href*="detail.tmall.com"]');
  out.recommend.anchors = links.length;
  const seen = new Set();
  let cards = 0;
  links.forEach(a => {
    const m = (a.getAttribute('href') || '').match(/[?&]id=(\d{6,})/);
    if (m && !seen.has(m[1])) { seen.add(m[1]); let c = a;
      for (let i = 0; i < 8; i++) { if (!c) break; const t = c.innerText || '';
        if (t.includes('¥') && t.length < 300) { cards++; break; } c = c.parentElement; } }
  });
  out.recommend.cards = cards;
  // 评论: 查看全部评价 文案/抽屉
  const bodyText = document.body ? (document.body.innerText || '') : '';
  (['查看全部评价', '全部评价', '评论', '评价']).forEach(k => {
    if (bodyText.includes(k)) out.review.markers++;
  });
  out.review.drawer = !!document.querySelector('[class*="Drawer--"]');
  // 问答: 问大家/ask 容器
  out.qa.items = document.querySelectorAll('[class*="askAnswerItem"], [class*="qaItem"], [class*="QA"], [class*="question"]').length;
  // 优惠价: 平台加补后 / 优惠前 / 补贴
  const m1 = bodyText.match(/平台加补后\s*[¥￥]\s*(\d+(?:\.\d+)?)/);
  const m2 = bodyText.match(/优惠前\s*[¥￥]\s*(\d+(?:\.\d+)?)/);
  if (m1) out.price.promo = parseFloat(m1[1]);
  if (m2) out.price.orig = parseFloat(m2[1]);
  ['平台加补后', '优惠前', '补贴', '立减', '到手价'].forEach(k => {
    if (bodyText.includes(k)) out.price.seen.push(k);
  });
  return out;
}"""
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
# 2026-08-20: 改为「详情图数量稳定即停」 — 滚动中逐段收集, 若连续 3 轮 imgs 不再增长
# 即认为详情图已全部加载(后面只剩推广商品区), 提前停止, 不再滚到 SKU 面板最底部。
DESC_PANEL_JS = r"""async () => {
  const out = { panelFound: false, panelScrollable: false, scope: null, imgs: [], imgsAnyWidth: [] };
  const seen = new Set(), seenAny = new Set();   // 跨轮去重(闭包, 不在 collect 内新建)
  const collect = () => {
    const sels = ['.desc-root', '.content-detail', '[class*="desc-"]', '[class*="detail-"]'];
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el) {
        out.scope = s;
        el.querySelectorAll('img').forEach(e => {
          const u = e.getAttribute('data-ks-lazyload') || e.getAttribute('data-src') || e.getAttribute('src') || '';
          if (!u) return;
          if (e.width >= 700 && !seen.has(u)) { seen.add(u); out.imgs.push(u); }
          if (!seenAny.has(u)) { seenAny.add(u); out.imgsAnyWidth.push(u); }
        });
        return;
      }
    }
  };
  const panel = document.getElementById('tbpcDetail_SkuPanelBody');
  if (panel) {
    out.panelFound = true;
    const sh = panel.scrollHeight, ch = panel.clientHeight;
    out.panelScrollable = sh > ch;
    collect();
    let top = 0;
    let lastCount = out.imgs.length;
    let stable = 0;
    while (top < sh - ch && stable < 3) {
      panel.scrollTop = top;
      top += 300;
      await new Promise(r => setTimeout(r, 120));
      collect();
      if (out.imgs.length === lastCount) {
        stable += 1;      // 本轮无新详情图 → 稳定计数 +1
      } else {
        stable = 0;       // 还有新图 → 继续滚
      }
      lastCount = out.imgs.length;
    }
    await new Promise(r => setTimeout(r, 900));
  } else {
    collect();
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
  // 细查(mi_id)页价格结构: "平台加补后￥42.79 | 优惠前￥51" 双价并列(2026-08-20 实证)。
  // 返回 {after, before, raw}: after=平台加补后(真实到手价), before=优惠前, raw=匹配到的文本。
  // 有界扫描 (CLAUDE.md B.3): 只遍历价格类节点, 绝不扫全 DOM 做 innerText 全量循环
  // (那会在 SSR 详情页把标签页卡死)。取含 平台加补后 的最短价格节点。
  const sels = ['[class*="price"]', '[class*="Price"]', '[class*="subsidy"]',
                '[class*="discount"]', '[class*="promo"]'];
  const nodes = [...document.querySelectorAll(sels.join(','))];
  let best=null, bestLen=9999;
  for (const e of nodes) {
    const t=(e.innerText||'').replace(/\s+/g,'');
    if (t.includes('平台加补后') && /\d/.test(t) && t.length<60 && t.length<bestLen) { best=t; bestLen=t.length; }
  }
  if (!best) return null;
  // 容忍 "平台加补后 ￥42.79" / "平台加补后￥42.79" / "平台加补后：42.79"
  const mAfter = best.match(/平台加补后[:：]?\s*[¥￥]?\s*([\d.]+)/);
  const mBefore = best.match(/优惠前[:：]?\s*[¥￥]?\s*([\d.]+)/);
  return {
    after: mAfter ? mAfter[1] : null,
    before: mBefore ? mBefore[1] : null,
    raw: best.slice(0, 60),
  };
}"""

# --- light price snapshot on a mi_id-entered page (favorite flow): what does the
# personalized channel actually show? Includes 平台加补后/补贴/优惠前/到手价/券后 lines. ---
PRICE_LINES_JS = r"""() => {
  const body = document.body ? document.body.innerText : '';
  const lines = body.split('\n').map(s => s.trim()).filter(Boolean);
  // join a bare ￥/¥ onto the following number so prices read as ￥36 not ￥ + 36
  const joined = [];
  for (let i = 0; i < lines.length; i++) {
    let l = lines[i];
    if (/^[¥￥]$/.test(l) && i + 1 < lines.length && /^[\d.,]/.test(lines[i + 1])) {
      l = l + lines[i + 1]; i++;
    }
    joined.push(l);
  }
  const ys = joined.filter(l => /[¥￥]|到手价|券后|立减|优惠|省/.test(l)).slice(0, 40);
  const kws = ['平台加补后','平台补贴','补贴','优惠前','到手价','券后','立减','促销','满减','红包','超级立减','店铺优惠'];
  return { priceLines: ys, hasKeywords: kws.filter(k => body.includes(k)) };
}"""

# --- direct price-node read (the displayed 店铺优惠后/price elements, not body text) ---
PRICE_NODE_JS = r"""() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('[class*="price"], [class*="Price"], [class*="discount"], [class*="subsidy"], [class*="promo"]').forEach(e => {
    const t = (e.innerText || '').trim().replace(/\s+/g, ' ');
    if (t && t.length < 60 && /[¥￥][\d.]/.test(t) && !seen.has(t)) {
      seen.add(t); out.push({ text: t, cls: String(e.className || '').slice(0, 40) });
    }
  });
  return out.slice(0, 20);
}"""

# --- SKU option chip discovery (for per-variant price sweeps) ---
CHIP_DISCOVER_JS = r"""() => {
  const seen = new Set();
  const out = [];
  const rx = /cm|特大|加大|超[大中小]?号|色分类|规格|个装|只装|件|米|kg|L\d|M\d|S\d/;
  document.querySelectorAll('div, span, li, button, a').forEach(e => {
    if (e.children.length > 2) return;             // leaf-ish chips only
    const t = (e.innerText || '').trim().replace(/\s+/g, ' ');
    if (t.length < 2 || t.length >= 46 || seen.has(t)) return;
    if (!rx.test(t)) return;
    let r = null;
    try { r = e.getBoundingClientRect(); } catch (_) {}
    if (r && r.width > 20 && r.height > 14 && r.width < 400) {  // chip-like dimensions
      seen.add(t);
      out.push({ text: t, cls: String(e.className || '').slice(0, 40), tag: e.tagName });
    }
  });
  return out.slice(0, 40);
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
