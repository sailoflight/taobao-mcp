"""Live account readers for the vendor join (READ-ONLY): cart and purchases.

Cart reads capture one coherent `mtop.trade.query.bag` response. Atomic callers can
require a proven snapshot so missing/invalid XHR is never confused with an empty cart.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from src.models import CartItem

_CART_URL = "https://cart.taobao.com/cart.htm"
_BOUGHT_URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"
_QUERY_BAG_API = "mtop.trade.query.bag"

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
  const by = {};
  for (const r of out) {
    const cur = by[r.order_id];
    if (!cur || (r.seller && !cur.seller) || (r.title && !cur.title)) by[r.order_id] = r;
  }
  return Object.values(by).slice(0, 60);
}"""


def _is_query_bag_url(url: str) -> bool:
    """Match only the authoritative cart API, never generic bag/cart URLs."""
    try:
        parsed = urlparse(str(url))
        query = {k.lower(): v for k, v in parse_qs(parsed.query).items()}
        api_values = [str(v).lower() for v in query.get("api", [])]
        path_parts = [part.lower() for part in parsed.path.split("/") if part]
        return _QUERY_BAG_API in api_values or _QUERY_BAG_API in path_parts
    except Exception:
        return False


def _load_cart_json(body: str) -> object | None:
    try:
        return json.loads(body)
    except Exception:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            return json.loads(body[start:end + 1])
        except Exception:
            return None


def _positive_quantity(value: object) -> int | None:
    """Return a positive integral quantity; null/invalid placeholders are ignored."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        quantity = int(value.strip())
        return quantity if quantity > 0 else None
    return None


def _parse_cart_bag_snapshot(body: str, max_items: int = 200) -> tuple[list[CartItem] | None, str | None]:
    """Parse one coherent query.bag body, including a valid empty cart."""
    data = _load_cart_json(body)
    if not isinstance(data, dict) or "data" not in data or not isinstance(data["data"], (dict, list)):
        return None, "query.bag response is not a recognized data envelope"
    api = str(data.get("api") or "").lower()
    if api and api != _QUERY_BAG_API:
        return None, f"query.bag response declares unexpected api={api}"
    ret = data.get("ret")
    if ret and "SUCCESS" not in " ".join(ret if isinstance(ret, list) else [str(ret)]).upper():
        return None, "query.bag response did not report success"

    items: list[CartItem] = []
    by_key: dict[tuple[str, str | None], int] = {}
    invalid_reason: str | None = None

    def walk(obj: object, shop: str | None = None) -> None:
        nonlocal invalid_reason
        if invalid_reason:
            return
        if isinstance(obj, dict):
            if obj.get("shopTitle"):
                shop = str(obj["shopTitle"])
            is_line = "quantity" in obj and any(key in obj for key in ("itemId", "sku", "title"))
            if is_line:
                quantity = _positive_quantity(obj.get("quantity"))
                if quantity is not None:
                    product_id = str(obj.get("itemId") or "").strip()
                    if not product_id:
                        invalid_reason = "cart line with a positive quantity is missing itemId"
                        return
                    sku = obj.get("sku")
                    sku_id: str | None = None
                    if isinstance(sku, str):
                        match = re.search(r'"skuId"\s*:\s*"?([^",}]+)', sku)
                        sku_id = match.group(1).strip() if match else None
                    elif isinstance(sku, dict) and sku.get("skuId") not in (None, ""):
                        sku_id = str(sku["skuId"])
                    key = (product_id, sku_id)
                    previous = by_key.get(key)
                    if previous is None:
                        by_key[key] = quantity
                        items.append(CartItem(
                            seller=shop or "?",
                            title=str(obj.get("title") or "?")[:60],
                            product_id=product_id,
                            sku_id=sku_id,
                            quantity=quantity,
                        ))
            for value in obj.values():
                walk(value, shop)
        elif isinstance(obj, list):
            for value in obj:
                walk(value, shop)

    walk(data["data"])
    if invalid_reason:
        return None, invalid_reason
    return (items if max_items <= 0 else items[:max_items]), None


def parse_cart_bag(body: str, max_items: int = 200) -> list[CartItem]:
    """Best-effort public parser retained for non-atomic account views."""
    items, _ = _parse_cart_bag_snapshot(body, max_items=max_items)
    return items or []


def _latest_valid_snapshot(bodies: list[str], max_items: int) -> tuple[list[CartItem] | None, str | None]:
    """Choose the latest valid response as one snapshot; never union responses."""
    last_reason = "no query.bag response was captured"
    for body in reversed(bodies):
        items, reason = _parse_cart_bag_snapshot(body, max_items=max_items)
        if items is not None:
            return items, None
        if reason:
            last_reason = reason
    return None, last_reason


async def read_cart(max_items: int = 200, *, require_snapshot: bool = False) -> list[CartItem]:
    """Read one authoritative cart snapshot; strict mode distinguishes failure from empty."""
    from src.browser.pacing import human_delay
    from src.browser.session import get_session
    from src.errors import CartSnapshotError

    session = get_session()
    page = await session.start()
    bodies: list[str] = []

    async def on_resp(resp):
        if not _is_query_bag_url(resp.url):
            return
        try:
            bodies.append(await resp.text())
        except Exception:
            return

    snapshot_limit = 0 if require_snapshot else max_items
    page.on("response", on_resp)
    try:
        for _ in range(2):
            await page.goto(_CART_URL, wait_until="domcontentloaded")
            await session.guard_captcha(page)
            await human_delay(4.5, 5.5)
            for _ in range(3):
                await page.mouse.wheel(0, 2200)
                await human_delay(1.2, 1.8)
            snapshot, _ = _latest_valid_snapshot(bodies, snapshot_limit)
            if snapshot is not None:
                break
    finally:
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass

    snapshot, reason = _latest_valid_snapshot(bodies, snapshot_limit)
    if snapshot is not None:
        return snapshot
    if require_snapshot:
        raise CartSnapshotError(reason or "invalid query.bag response")
    return []


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
