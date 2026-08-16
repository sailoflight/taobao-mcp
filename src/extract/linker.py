"""Join cart + purchases + tracking + seller messages into one picture (CLAUDE.md §0 / SKILL §9).

Join keys: the **vendor (shop name)** links cart / purchases / IM threads; the **order_id**
links a purchase to its tracking + 取件码. The matcher and join below are **pure** (no browser)
so they're unit-tested offline; the live orchestrator (`full_picture`) calls the existing readers.

Matching is deliberately conservative: normalize shop names, allow a learned alias map, and mark
any IM thread that can't be confidently tied to a known vendor as `unlinked` rather than guess.
"""

from __future__ import annotations

import re

from src.models import CartItem, Conversation, OrderStatus, SellerMessage, VendorDossier

# Suffixes that decorate a shop's display name but aren't part of its identity. Stripped
# (repeatedly) so "好管家旗舰店" and the IM nick "好管家" resolve to the same key.
_SUFFIXES = (
    "官方旗舰店", "旗舰店", "官方store", "专卖店", "专营店", "企业店", "官方店",
    "官方", "自营店", "自营", "海外旗舰店", "工厂店", "总店", "店铺", "店", "商行", "网店",
)
_PUNCT = re.compile(r"[\s\-_·、，,。\.／/（）()【】\[\]！!~：:|]+")


def normalize_seller(name: str) -> str:
    """A stable key for matching shop / IM names: strip decorative suffixes + punctuation."""
    n = (name or "").strip()
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if len(n) > len(suf) and n.endswith(suf):
                n = n[: -len(suf)]
                changed = True
    return _PUNCT.sub("", n).lower()


def same_vendor(a: str, b: str, aliases: dict | None = None) -> bool:
    """True if two shop/IM names refer to the same vendor. Checks the alias map first, then
    a normalized exact match, then containment (one normalized name inside the other)."""
    if aliases:
        a = aliases.get(a, a)
        b = aliases.get(b, b)
    na, nb = normalize_seller(a), normalize_seller(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # containment only when both are non-trivial, to avoid 1-char false hits
    return len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na)


def _thread_of(conv: Conversation) -> list[SellerMessage]:
    """A thread's messages if it was opened, else a 1-line stand-in from its last message."""
    if conv.messages:
        return list(conv.messages)
    if conv.last_message:
        return [SellerMessage(sender=conv.seller, text=conv.last_message, is_self=False)]
    return []


def merge_orders(purchases: list[dict], tracking: list[OrderStatus]) -> list[OrderStatus]:
    """Join purchased orders (order_id, seller, title, status) to their tracking by order_id."""
    trk = {o.order_id: o for o in tracking}
    out: list[OrderStatus] = []
    for p in purchases:
        oid = str(p.get("order_id") or "")
        t = trk.get(oid)
        out.append(OrderStatus(
            order_id=oid,
            title=p.get("title") or (t.title if t else ""),
            status=p.get("status") or (t.status if t else "未知"),
            seller=p.get("seller"),
            carrier=t.carrier if t else None,
            tracking_no=t.tracking_no if t else None,
            latest=t.latest if t else None,
            pickup_code=t.pickup_code if t else None,
            station=t.station if t else None,
        ))
    return out


def build_dossiers(
    cart_items: list[CartItem],
    purchases: list[dict],
    tracking: list[OrderStatus],
    conversations: list[Conversation],
    aliases: dict | None = None,
) -> list[VendorDossier]:
    """Group cart + (purchases⋈tracking) + threads by vendor → one VendorDossier each. IM
    threads that match no known vendor are appended as `unlinked` dossiers."""
    orders = merge_orders(purchases, tracking)

    # canonical vendor display name per normalized key (first seen from cart, then orders)
    vendors: dict[str, str] = {}
    for src in (cart_items, orders):
        for it in src:
            name = it.seller or ""
            k = normalize_seller(name)
            if k and k not in vendors:
                vendors[k] = name

    dossiers: list[VendorDossier] = []
    for disp in vendors.values():
        d = VendorDossier(seller=disp)
        d.cart_items = [c for c in cart_items if same_vendor(c.seller, disp, aliases)]
        d.orders = [o for o in orders if same_vendor(o.seller or "", disp, aliases)]
        conv = next((c for c in conversations if same_vendor(c.seller, disp, aliases)), None)
        if conv:
            d.thread = _thread_of(conv)
        dossiers.append(d)

    for conv in conversations:  # threads with no matching vendor → surfaced, not guessed
        if not any(same_vendor(conv.seller, d.seller, aliases) for d in dossiers):
            dossiers.append(VendorDossier(seller=conv.seller, thread=_thread_of(conv), unlinked=True))
    return dossiers


def order_picture(
    order_id: str,
    purchases: list[dict],
    tracking: list[OrderStatus],
    conversations: list[Conversation],
    aliases: dict | None = None,
) -> VendorDossier | None:
    """The full picture for one order: the order (merged with its tracking) + its vendor's thread."""
    orders = merge_orders(purchases, tracking)
    target = next((o for o in orders if o.order_id == str(order_id)), None)
    if target is None:
        return None
    d = VendorDossier(seller=target.seller or "?", orders=[target])
    conv = next((c for c in conversations if same_vendor(c.seller, d.seller, aliases)), None)
    if conv:
        d.thread = _thread_of(conv)
    return d


def render_dossier(d: VendorDossier) -> str:
    """Compact markdown for one vendor's full picture."""
    tag = "  ⚠️ unlinked thread (no matching vendor)" if d.unlinked else ""
    lines = [f"### {d.seller}{tag}"]
    if d.cart_items:
        lines.append("**In cart:** " + "; ".join(f"{c.title[:28]} ×{c.quantity}" for c in d.cart_items))
    for o in d.orders:
        trk = f"{o.carrier or ''} {o.tracking_no or ''}".strip() or "—"
        pick = f" · 取件码 {o.pickup_code} @ {o.station}" if o.pickup_code else ""
        lines.append(f"**Order {o.order_id}** [{o.status}] {o.title[:24]} · {trk}{pick}")
    if d.thread:
        last = d.thread[-3:]
        lines.append("**Recent chat:** " + " | ".join(
            ("我: " if m.is_self else "卖家: ") + m.text[:30] for m in last))
    if not (d.cart_items or d.orders or d.thread):
        lines.append("_(nothing linked)_")
    return "\n".join(lines)


# ── learned alias map (shop-title ↔ IM-nick links) — local, gitignored (output/) ──────

def _aliases_path():
    from pathlib import Path

    from src.config import load_config
    return Path(load_config().output.dir) / "vendor_aliases.json"


def load_aliases() -> dict:
    import json
    try:
        return json.loads(_aliases_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_aliases(aliases: dict) -> None:
    import json
    try:
        p = _aliases_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # best-effort; never fail the dossier over the alias cache


# ── live orchestrator ─────────────────────────────────────────────────────────────────

async def full_picture(seller: str | None = None, order_id: str | None = None) -> list[VendorDossier]:
    """Live: read cart + purchases + tracking + messages, join, return VendorDossier(s).

    order_id → that order joined to its tracking + the vendor's thread; seller → that vendor's
    full dossier (cart + orders + opened thread); neither → overview of all linked vendors.
    """
    from src.extract.account import read_cart, read_purchases
    from src.extract.messages import read_messages
    from src.extract.orders import track_orders

    aliases = load_aliases()
    purchases = await read_purchases()
    tracking = await track_orders(only_active=True)  # served from the once/day cache

    if order_id:
        convos = await read_messages(max_conversations=30)
        d = order_picture(order_id, purchases, tracking, convos, aliases)
        if d and d.seller and d.seller != "?":
            opened = await read_messages(max_conversations=30, open_seller=d.seller)
            conv = next((c for c in opened if same_vendor(c.seller, d.seller, aliases)), None)
            if conv:
                d.thread = _thread_of(conv)
        return [d] if d else []

    cart = await read_cart()
    if seller:
        opened = await read_messages(max_conversations=40, open_seller=seller)
        dossiers = build_dossiers(cart, purchases, tracking, opened, aliases)
        matched = [d for d in dossiers if same_vendor(d.seller, seller, aliases)]
        return matched or [d for d in dossiers if seller in d.seller]

    convos = await read_messages(max_conversations=20)
    return build_dossiers(cart, purchases, tracking, convos, aliases)
