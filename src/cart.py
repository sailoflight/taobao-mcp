"""Add-to-cart staging — gated, reversible (CLAUDE.md §0 scope item 2).

The cart is the hand-off to the China agent, who checks out (picks the forwarder
address + pays). This module ONLY clicks 加入购物车 — it NEVER touches 领券购买/立即购买,
never selects an address, never pays. Default is a dry preview; confirm=True actually adds.
"""

from __future__ import annotations

from src.errors import CaptchaError, ProductNotFoundError  # noqa: F401

_ADD_BTN = "加入购物车"
_SUCCESS_RE = r"加入购物车成功|已加入购物车|成功加入|添加成功|加购成功"

# Add to cart via the mtop.trade.addBag API using the page's own lib.mtop SDK (it handles
# signing + the login/ecode token). Robust on both Taobao and Tmall, where the SSR 加入购物车
# button isn't reliably clickable. Returns {ok, ret} — ret contains "SUCCESS::调用成功" on success.
_ADDBAG_JS = r"""async ([itemId, skuId, qty]) => {
  if (!(window.lib && window.lib.mtop)) return { ok: false, err: 'no lib.mtop' };
  try {
    const res = await window.lib.mtop.request({
      api: 'mtop.trade.addBag', v: '3.1', type: 'POST', ecode: 1, needLogin: true, dataType: 'json',
      data: { itemId: String(itemId), skuId: String(skuId), quantity: Number(qty) || 1,
              exParams: JSON.stringify({ id: String(itemId) }) }
    });
    const ret = (res && res.ret) ? String(res.ret) : '';
    return { ok: ret.indexOf('SUCCESS') >= 0, ret: ret };
  } catch (e) { return { ok: false, err: String(e).slice(0, 140) }; }
}"""


async def add_to_cart(
    product_url_or_id: str,
    options: list[str] | None = None,
    qty: int = 1,
    confirm: bool = False,
) -> str:
    """Stage one product+variant+qty into the cart. Preview unless confirm=True.

    options = one option VALUE per group (e.g. ["P100 质保3年 以换代修"] or ["黑色","L"]).
    Reversible (cart only); never buys, never picks an address.
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.extract.product import _to_product_id, parse_product_html

    options = options or []
    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_scroll(page, 2)
    await human_delay(1.5, 2.5)

    product = parse_product_html(await page.content(), pid, url)
    group_names = {k for v in product.variants for k in v.properties}
    if product.variants and group_names and not options:
        choices = sorted({" / ".join(v.properties.values()) for v in product.variants})
        return ("Specify the variant to add via `options` (one value per group). Available: "
                + "; ".join(choices[:8]))

    # select each option (exact chip match → real selection)
    selected: list[str] = []
    for value in options:
        loc = page.get_by_text(value, exact=True).first
        if await loc.count() == 0:
            loc = page.get_by_text(value[:14], exact=False).first
        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
            await loc.click(timeout=4000)
            selected.append(value)
        except Exception as exc:
            raise ProductNotFoundError(f"could not select option {value!r} on product {pid}: {exc}")
        await human_delay(0.6, 1.2)

    # VALIDATE the clicks physically registered (CLAUDE.md Appendix B.8). A complete, valid
    # variant selection makes the live page set &skuId=… ; if it's absent, the chips did NOT
    # take (the failure mode that silently dropped half the jumper-wire order). Retry once,
    # then REFUSE to add rather than stage the wrong/incomplete item.
    import re as _re

    def _live_sku() -> str | None:
        m = _re.search(r"[?&]skuId=(\d+)", page.url)
        return m.group(1) if m else None

    sku_id = _live_sku()
    if options and not sku_id:
        for value in options:  # one retry pass of the chip clicks
            try:
                await page.get_by_text(value, exact=True).first.click(timeout=4000)
                await human_delay(0.5, 1.0)
            except Exception:
                pass
        sku_id = _live_sku()
    if options and not sku_id:
        raise ProductNotFoundError(
            f"variant {options} did not register on product {pid} (no skuId after clicking — "
            f"the chip selection was not validated). Refusing to add the wrong item; retry."
        )

    if qty and int(qty) != 1:
        try:
            await page.locator('input[class*="countValue"]').first.fill(str(int(qty)), timeout=3000)
            await human_delay(0.4, 0.9)
        except Exception:
            pass

    label = " / ".join(selected) or (product.title[:40] or pid)
    if not confirm:
        return (f"PREVIEW — ready to add: {product.title[:44]} · variant: {label} · qty {qty}. "
                f"Re-call with confirm=True to add it. (Cart only — never buys or picks an address.)")

    # PRIMARY add path: the mtop.trade.addBag API via the page's own lib.mtop SDK (it signs
    # + carries the login/ecode token). Works on BOTH Taobao and Tmall — unlike clicking 加入
    # 购物车, which the SSR detail.tmall.com page doesn't reliably expose. Needs the validated skuId.
    if sku_id:
        api = await page.evaluate(_ADDBAG_JS, [pid, sku_id, int(qty)])
        if api and api.get("ok"):
            await session.guard_captcha(page)
            return f"added to cart (API): {product.title[:44]} · {label} · qty {qty} · skuId {sku_id}."
        api_err = (api or {}).get("ret") or (api or {}).get("err") or "unknown"
    else:
        api_err = "no skuId"

    # Fallback: click the 加入购物车 button (works on classic Taobao item pages).
    try:
        btn = page.get_by_text(_ADD_BTN, exact=True).first
        await btn.scroll_into_view_if_needed(timeout=3000)
        await btn.click(timeout=5000)
    except Exception as exc:
        raise ProductNotFoundError(
            f"could not add product {pid}: addBag API said [{api_err}]; 加入购物车 click also failed: {exc}"
        )
    await human_delay(1.5, 2.5)
    await session.guard_captcha(page)  # adding can trigger a slider

    import re
    added = bool(re.search(_SUCCESS_RE, await page.evaluate("() => document.body ? document.body.innerText : ''")))
    head = "added to cart" if added else "clicked 加入购物车 (no success toast seen — check the cart)"
    sku_note = f" · skuId {sku_id}" if sku_id else ""
    return f"{head}: {product.title[:44]} · {label} · qty {qty}{sku_note}."


async def add_to_cart_batch(
    product_url_or_id: str,
    items: list[dict],
    confirm: bool = False,
) -> str:
    """Stage MANY variants of ONE product in a SINGLE page visit (anti-burst).

    INTERNAL/script-only helper — deliberately NOT registered as an MCP tool: the MCP
    surface stays one gated line per taobao_add_to_cart call so the human confirms each
    line. (If ever promoted to a tool, keep the confirm gate and reconcile against the
    cart data XHR afterwards.)

    items = [{"options": [v1, v2, ...], "qty": packs}, ...] — one entry per cart line.
    Preview-validates every chip exists (confirm=False, no writes); confirm=True selects
    each variant + adds via the addBag API, all on one loaded page, paced. Never buys/checks out.
    """
    from src.browser.pacing import human_delay, human_scroll
    from src.browser.session import get_session
    from src.extract.product import _to_product_id, parse_product_html

    pid = _to_product_id(product_url_or_id)
    session = get_session()
    page = await session.start()
    url = f"https://item.taobao.com/item.htm?id={pid}"
    await page.goto(url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_scroll(page, 2)
    await human_delay(1.5, 2.5)
    product = parse_product_html(await page.content(), pid, url)

    lines: list[str] = []
    added = 0
    for it in items:
        opts = list(it.get("options") or [])
        qty = int(it.get("qty", 1))
        label = " / ".join(opts)
        missing = [v for v in opts if await page.get_by_text(v, exact=True).count() == 0]
        if missing:
            lines.append(f"  ✗ {label} ×{qty} — chip not found: {missing}")
            continue
        if not confirm:
            lines.append(f"  • {label} ×{qty}")
            continue
        try:
            for v in opts:  # select each option group (exact chip)
                await page.get_by_text(v, exact=True).first.click(timeout=4000)
                await human_delay(0.4, 0.9)
            # VALIDATE the selection registered (Appendix B.8): skuId must appear in the URL,
            # else the chip clicks silently failed (this batch path's known failure mode).
            # Report an honest ✗ and skip the add rather than falsely confirming.
            import re as _re
            if not _re.search(r"[?&]skuId=(\d+)", page.url):
                lines.append(f"  ✗ {label} ×{qty} — selection not validated (no skuId)")
                await human_delay(0.6, 1.0)
                continue
            # Add via the addBag API (skuId validated above) — robust on Taobao + Tmall, AND it
            # avoids the success-dialog that used to block the next chip selection in this loop
            # (the original failure mode in B.8). No button click, no dialog to dismiss.
            sku_id = _re.search(r"[?&]skuId=(\d+)", page.url).group(1)
            api = await page.evaluate(_ADDBAG_JS, [pid, sku_id, qty])
            if api and api.get("ok"):
                lines.append(f"  ✓ {label} ×{qty}")
                added += 1
            else:
                err = (api or {}).get("ret") or (api or {}).get("err") or "addBag failed"
                lines.append(f"  ✗ {label} ×{qty} — {err}")
            await session.guard_captcha(page)
        except Exception as exc:
            lines.append(f"  ✗ {label} ×{qty} — {type(exc).__name__}")
        await human_delay(0.8, 1.6)  # human pacing between adds (one tab, no bursts)

    head = (f"PREVIEW — would add {len(items)} line(s) of «{product.title[:38]}» in one visit "
            f"(nothing added yet; re-call confirm=True):"
            if not confirm else
            f"Added {added}/{len(items)} line(s) of «{product.title[:38]}» to cart:")
    return head + "\n" + "\n".join(lines)
