"""Live account readers for the vendor join (READ-ONLY): read_cart() + read_purchases().

read_cart()      — captures the cart `mtop.trade.query.bag` XHR → CartItem list (seller=shopTitle).
read_purchases() — parses 已买到的宝贝 order cards → [{order_id, seller, title, status}].
Both feed src/extract/linker.py's join. No writes.
"""

from __future__ import annotations

import json
import re

from src.models import CartItem

_CART_URL = "https://cart.taobao.com/cart.htm"
_BOUGHT_URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"

# Order cards: each has a 订单号, a link to the item, a shop, and a status word. Best-effort —
# we take the smallest container that holds an order number + an item link.
PURCHASES_JS = r"""() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const STATUS = /交易成功|待发货|待收货|待评价|卖家已发货|退款成功|退款中|退款关闭|交易关闭/;
  const out = [];
  for (const el of document.querySelectorAll('div,li,section')) {
    const t = el.innerText || '';
    const m = t.match(/订单号[:：]?\s*(\d{15,})/);
    if (!m || t.length > 900) continue;
    const itemA = el.querySelector('a[href*="item.htm"], a[href*="detail.tmall"]');
    if (!itemA) continue;
    const shopA = el.querySelector('a[href*="//shop"], a[href*="shopId"], a[href*="user.taobao"], a[href*="店"]');
    const st = (t.match(STATUS) || [])[0] || '';
    out.push({ order_id: m[1], title: norm(itemA.innerText).slice(0, 56),
               seller: norm(shopA ? shopA.innerText : '').slice(0, 30), status: st });
  }
  // dedupe by order_id, prefer the entry that actually found a seller + title
  const by = {};
  for (const r of out) {
    const cur = by[r.order_id];
    if (!cur || (r.seller && !cur.seller) || (r.title && !cur.title)) by[r.order_id] = r;
  }
  return Object.values(by).slice(0, 60);
}"""


async def read_cart(max_items: int = 200) -> list[CartItem]:
    """Cart lines as CartItem(seller, title, sku_id, quantity), parsed from the query.bag XHR."""
    from src.browser.pacing import human_delay
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    bodies: list[str] = []

    async def on_resp(resp):
        u = resp.url
        if "mtop" in u and any(k in u.lower() for k in ("query.bag", "bag", "cart")):
            try:
                t = await resp.text()
                if "itemId" in t:  # the cart-data response (query.bag) carries itemId per line
                    bodies.append(t)
            except Exception:
                pass

    page.on("response", on_resp)
    for _ in range(2):  # the cart-data XHR fires ~5 s in; give it room before scrolling, retry once
        await page.goto(_CART_URL, wait_until="domcontentloaded")
        await session.guard_captcha(page)
        await human_delay(4.5, 5.5)
        for _ in range(3):
            await page.mouse.wheel(0, 2200)
            await human_delay(1.2, 1.8)
        if bodies:
            break
    try:
        page.remove_listener("response", on_resp)
    except Exception:
        pass

    items: list[CartItem] = []
    seen: set = set()
    for body in bodies:
        try:
            data = json.loads(body)
        except Exception:
            a = body.find("{")
            try:
                data = json.loads(body[a:body.rfind("}") + 1])
            except Exception:
                continue

        def walk(o, shop=None):
            # shopTitle lives on the shop-group node now; carry it down to the item lines.
            if isinstance(o, dict):
                if o.get("shopTitle"):
                    shop = o.get("shopTitle")
                if o.get("itemId") and o.get("title"):   # an item line
                    sku = o.get("sku")
                    sid = None
                    if isinstance(sku, str):
                        m = re.search(r'"skuId"\s*:\s*"?(\d+)', sku)
                        sid = m.group(1) if m else None
                    elif isinstance(sku, dict):
                        sid = sku.get("skuId")
                    q = o.get("quantity")
                    qty = q if isinstance(q, int) else 1
                    key = (o.get("itemId"), sid)
                    if key not in seen:
                        seen.add(key)
                        items.append(CartItem(
                            seller=shop or "?",
                            title=str(o.get("title"))[:60],
                            sku_id=str(sid) if sid else None,
                            quantity=qty,
                        ))
                for v in o.values():
                    walk(v, shop)
            elif isinstance(o, list):
                for v in o:
                    walk(v, shop)

        walk(data)
    return items[:max_items]


async def read_purchases(max_orders: int = 40) -> list[dict]:
    """Purchased orders as [{order_id, seller, title, status}] from 已买到的宝贝 (best-effort)."""
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    await page.goto(_BOUGHT_URL, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_delay(3, 4)
    await human_scroll(page, 3)
    await human_delay(2, 3)
    rows = await page.evaluate(PURCHASES_JS)
    return rows[:max_orders]
