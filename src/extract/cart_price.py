"""购物车读取(只读): 每件商品的结构化解析 — 标题/型号/优惠/实际到手价.

买家挑选/下单前常用: 一眼看清购物车里每件的实际到手价(店铺优惠后/平台加补后/立减)与
标价。只读, 不写入、不收藏、不发送任何消息。
"""

from __future__ import annotations

import re

from src.errors import CaptchaError, SelectorDriftError  # noqa: F401 (re-raised, never swallowed)


def _num(s: str) -> str:
    """Normalize a space-separated price token:
       '33 . 75' -> '33.75'  (decimal point as its own token)
       '259 1'   -> '259'    (trailing lone integer = the quantity input, not price)
       '10'      -> '10'"""
    s = s.strip()
    if not s:
        return ""
    toks = s.split()
    if len(toks) >= 3 and toks[1] == ".":
        return "".join(toks)
    if len(toks) == 2 and toks[0].isdigit() and toks[1].isdigit():
        return toks[0]
    return s.replace(" ", "")


def _quantity_from_text(text: str) -> int:
    """Pure: the cart quantity stepper value embedded in an item block's text.

    The cart page renders the quantity input's value as a lone integer right after
    the price tokens and before the trailing 移入收藏/删除 actions, e.g.
      '￥ 259 3 移入收藏 删除' -> qty 3
      '￥ 33 . 75 ￥ 42 . 25 移入收藏 删除' -> qty 1 (no explicit stepper value)
    A space-separated decimal ('42 . 25') is a PRICE (the '25' is the mantissa, not a
    qty); only a trailing lone integer whose predecessor is also an integer is the qty.
    """
    t = text or ""
    m = re.search(r"￥\s*([\d\s.]+)\s*(?:移入收藏|删除)", t)
    if not m:
        return 1
    toks = m.group(1).strip().split()
    if len(toks) >= 2 and toks[-1].isdigit() and toks[-2].isdigit():
        # "... <int> <int>" → the last integer is the quantity (mirrors _num's rule)
        try:
            q = int(toks[-1])
            if q > 0:
                return q
        except ValueError:
            pass
    return 1


def _qty_of(item: dict) -> int:
    """Pure: an item's parsed quantity (>=1). Missing/garbage → 1 (single unit)."""
    q = item.get("quantity")
    try:
        return max(1, int(q))
    except (TypeError, ValueError):
        return 1


def _parse_cart_item(text: str) -> dict:
    """Parse one cart-item block's flattened text into structured fields.

    Price renderings seen on the cart page:
      店铺优惠后 ￥ 33 . 75 ￥ 42 . 25          -> after ¥33.75, original ¥42.25
      平台加补后 ￥ 15 . 83 距加入降 ￥ 0 . 15 ￥ 15 . 98 -> platform-after ¥15.83, saving ¥0.15, ¥15.98
      ￥ 10                                   -> plain ¥10
    `quantity` is the cart line's pack/piece count (>=1), parsed from the stepper value.
    """
    t = text
    title = re.split(r"信用卡支付|退货宝|88VIP|消费券|官方立减|超级立减|免息|颜色分类|规格|商品规格|配件类型|店铺优惠|平台加补后|￥", t)[0].strip()
    variant = ""
    m = re.search(
        r"(?:颜色分类|规格|商品规格|配件类型)[：:]\s*"
        r"((?:(?!颜色分类|规格|商品规格|配件类型)[^店铺优惠平台￥])+)",
        t,
    )
    if m:
        variant = m.group(1).strip()[:48]
    savings = None
    m = re.search(r"(?:超级|官方|消费券)?立减([\d.]+)元", t)
    if m:
        savings = float(m.group(1))

    after = platform_after = orig = None
    m = re.search(r"店铺优惠后\s*￥\s*([\d\s.]+)", t)
    if m:
        after = _num(m.group(1))
    m = re.search(r"平台加补后\s*￥\s*([\d\s.]+)", t)
    if m:
        platform_after = _num(m.group(1))
    nums = re.findall(r"￥\s*([\d\s.]+)", t)
    if nums:
        last = _num(nums[-1])
        if last and last != after and last != platform_after:
            orig = last
    if not after and not platform_after:
        m = re.search(r"￥\s*([\d\s.]+)", t)
        if m:
            after = _num(m.group(1))
    return {
        "title": title[:72],
        "variant": variant,
        "savings": savings,
        "after_price": after,
        "platform_after": platform_after,
        "original_price": orig,
        "quantity": _quantity_from_text(t),
    }


def _compute_total(items: list[dict]) -> tuple[float, int]:
    """Pure: 到手价合计 + 排除件数(缺货/下架 不可买, 不计入; 未含运费).

    Each line's 到手价 (after_price / platform_after) is a PER-UNIT figure, so the
    line contributes unit_price × quantity (fix: qty was previously ignored).
    """
    total = 0.0
    excluded = 0
    for it in items:
        if "缺货" in it["title"] or "下架" in it["title"]:
            excluded += 1
            continue
        p = it.get("after_price") or it.get("platform_after")
        if p:
            try:
                total += float(p) * _qty_of(it)
            except ValueError:
                pass
    return round(total, 2), excluded


def _group_by_shop(items: list[dict]) -> list[dict]:
    """Pure: 按店铺分组小计(件数/到手价合计/排除缺货下架), 按合计降序.

    `items` = 行数; `total` = Σ(unit 到手价 × quantity) — qty-weighted subtotal.
    """
    by_shop: dict[str, dict] = {}
    for it in items:
        sh = it.get("shop") or "?"
        g = by_shop.setdefault(sh, {"items": 0, "total": 0.0, "excluded": 0})
        g["items"] += 1
        if "缺货" in it["title"] or "下架" in it["title"]:
            g["excluded"] += 1
            continue
        p = it.get("after_price") or it.get("platform_after")
        if p:
            try:
                g["total"] += float(p) * _qty_of(it)
            except ValueError:
                pass
    return [{"shop": k, "items": v["items"], "total": round(v["total"], 2),
             "excluded": v["excluded"]}
            for k, v in sorted(by_shop.items(), key=lambda kv: -kv[1]["total"])]


def _cart_unit_price(item: dict) -> float | None:
    """Pure: 型号含 'N个装' 时算每件到手价(共享 helper, 防漂移)."""
    from src.extract.units import unit_price_from_label

    return unit_price_from_label(item.get("variant", "") or "",
                                 item.get("after_price") or item.get("platform_after"))


def _cart_markdown(data: dict, with_tag: bool = False) -> str:
    """Pure: 把 list_cart 的 data 渲染成可读 markdown(店铺小计 + 每件, 含单价).

    with_tag=True 时加"海运/空运"列(留空让买家填, 交接代购分路线用).
    """
    note = data.get("total_est_note", "")
    lines = [f"### 购物车({data.get('count', 0)} 件) — {note}\n"]
    bs = data.get("by_shop") or []
    if bs:
        lines.append("| 店铺 | 件数 | 小计(到手)¥ | 缺货/下架 |\n|---|---|---|---|")
        for g in bs:
            lines.append(f"| {g.get('shop')} | {g.get('items')} | {g.get('total')} | {g.get('excluded')} |")
        lines.append("")
    head = "| 商品 | 型号 | 到手¥ | 单价¥ | 标价¥ | 店铺 |"
    sep = "|---|---|---|---|---|---|"
    if with_tag:
        head += " 海运/空运 |"
        sep += "---|"
    lines.append(head + "\n" + sep)
    for it in (data.get("items") or []):
        p = it.get("after_price") or it.get("platform_after") or "-"
        u = _cart_unit_price(it)
        u_cell = f"{u:.2f}" if u is not None else "-"
        row = f"| {it.get('title','')[:30]} | {it.get('variant','')[:16]} | {p} | {u_cell} | " \
              f"{it.get('original_price') or '-'} | {it.get('shop','')} |"
        if with_tag:
            row += "  |"
        lines.append(row)
    return "\n".join(lines)


async def list_cart(max_items: int = 50, exclude_unavailable: bool = False) -> dict:
    """只读: 结构化列出购物车商品(标题/型号/优惠/实际到手价/标价). 无任何写入.

    exclude_unavailable=True 时过滤缺货/下架件, 只列可买件(采购清单常用).
    """
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    await page.goto("https://cart.taobao.com/cart.htm", wait_until="domcontentloaded")
    await page.wait_for_timeout(6000)
    await session.guard_captcha(page)   # a slider/punish wall on the cart → human handoff
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    blocks = await page.evaluate(
        """() => {
          // 祖先查找: 每个 cartItemInfo 向上找最近的含 cartShopInfo 的祖先取店名
          // (文档序单遍在页面有隐藏/克隆副本时会错位, 祖先法更稳)
          // 每个 item 块内找商品链接 id(product_id) — 供 compare 购物车模式精确关联型号
          // 数量: 行内数量步进器 input 的当前值(缺省 1) — 供快照证明(+1/还原)
          const out = [];
          document.querySelectorAll('[class*="cartItemInfo"]').forEach(e => {
            const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!t) return;
            let a = e.parentElement, shop = '';
            while (a) {
              const sh = a.querySelector('[class*="cartShopInfo"]');
              if (sh) { shop = (sh.innerText || '').trim().replace(/\\s+/g, ' '); break; }
              a = a.parentElement;
            }
            let pid = null, sku = null;
            const lnk = e.querySelector('a[href*="item.htm"], a[href*="detail.tmall.com"]');
            if (lnk) {
              const m = (lnk.getAttribute('href') || '').match(/[?&]id=(\\d{6,})/);
              if (m) pid = m[1];
              const ms = (lnk.getAttribute('href') || '').match(/[?&]skuId=(\\d+)/);
              if (ms) sku = ms[1];
            }
            let qty = 1;
            const qi = e.querySelector('input[class*="countValue"], input[type="number"], [class*="stepper"] input, [class*="Stepper"] input');
            if (qi) { const v = parseInt((qi.value || '').replace(/\\D/g, ''), 10); if (v > 0) qty = v; }
            out.push({ shop, pid, sku, qty, text: t });
          });
          return out;
        }"""
    )
    items: list[dict] = []
    seen: set = set()
    for b in blocks[:max_items]:
        r = _parse_cart_item(b.get("text", ""))
        r["shop"] = b.get("shop", "")
        r["product_id"] = b.get("pid")
        r["sku_id"] = b.get("sku")
        # prefer the live stepper value (authoritative); fall back to the text parse
        if isinstance(b.get("qty"), int) and b["qty"] > 0:
            r["quantity"] = b["qty"]
        key = (r["title"], r["variant"], r["after_price"] or r["platform_after"])
        if key in seen:
            continue  # the item block + a nested container both carry the text
        seen.add(key)
        items.append(r)
    if exclude_unavailable:  # 采购清单: 只留可买件
        items = [it for it in items if "缺货" not in it["title"] and "下架" not in it["title"]]
    # 按店铺分组小计(多店铺采购常用)
    by_shop_sorted = _group_by_shop(items)
    # 合计(到手价): 自算(购物车页自己的"合计"只算已勾选项, 默认全不勾是 ¥0, 且只读工具
    # 不应点勾选框)。
    total_est, excluded = _compute_total(items)
    return {
        "count": len(items),
        "items": items,
        "total_est": total_est,
        "total_est_note": f"合计(到手价,排除{excluded}件缺货/下架,未含运费)",
        "by_shop": by_shop_sorted,
    }


async def export_cart_markdown(max_items: int = 50, exclude_unavailable: bool = False,
                               filename: str = "", title: str = "") -> dict:
    """只读: 把购物车渲染成 markdown 并落盘 output/cart_<ts>.md(采购清单交接代购用).

    复用 list_cart(只读浏览) + _cart_markdown。返回 {path, count, markdown}。
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from src.config import load_config

    data = await list_cart(max_items=max_items, exclude_unavailable=exclude_unavailable)
    md = _cart_markdown(data, with_tag=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    from src.config import safe_filename
    fname = safe_filename(filename, f"cart_{ts}.md")
    out_dir = Path(load_config().output.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / fname
    head = f"> 导出时间: {ts}"
    if title:
        head += f" — {title}"
    path.write_text(head + "\n\n" + md + "\n", encoding="utf-8")
    return {"path": str(path), "count": data.get("count", 0), "markdown": head + "\n\n" + md}


# --- 购物车删除(原子模式的"退多少"半边; 只删目标行, 绝不碰其他) ---
CART_REMOVE_JS = r"""() => {
  // 定位含"删除"操作入口的 cartItemInfo 行; 返回每行 {idx, pid, sku, href, text, has_del}
  // `idx` = querySelectorAll NodeList 中的稳定行号(供精确点击同一行, 不用标题前缀匹配);
  // `href` = 该行商品链接原文(供 sku 路径点击前复核精确 skuId/pid)。
  const out = [];
  document.querySelectorAll('[class*="cartItemInfo"]').forEach((e, idx) => {
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t) return;
    let pid = null, sku = null, href = null;
    const lnk = e.querySelector('a[href*="item.htm"], a[href*="detail.tmall.com"]');
    if (lnk) {
      href = lnk.getAttribute('href') || '';
      const m = href.match(/[?&]id=([0-9]{6,})/);
      if (m) pid = m[1];
      const ms = href.match(/[?&]skuId=([0-9]+)/);
      if (ms) sku = ms[1];
    }
    const del = [...e.querySelectorAll('[class*="cartOperationItem"], [class*="operation"] *, [class*="delete"], [class*="remove"]')]
      .find(x => (x.innerText || '').trim() === '删除');
    out.push({ idx, pid, sku, href: (href || '').slice(0, 240), text: t, has_del: !!del });
  });
  return out;
}"""

# Re-locate the exact cart row for sku_id removal: the row whose item href carries the
# exact skuId AND pid. Returns {idx, pid, sku, href} or null. This is the authoritative
# click target — NEVER a title-prefix first-match (two same-title rows differ by sku).
_FIND_SKU_ROW_JS = r"""([pid, sku]) => {
  const wantPid = String(pid), wantSku = String(sku);
  const rows = document.querySelectorAll('[class*="cartItemInfo"]');
  for (let i = 0; i < rows.length; i++) {
    const lnk = rows[i].querySelector('a[href*="item.htm"], a[href*="detail.tmall.com"]');
    if (!lnk) continue;
    const href = lnk.getAttribute('href') || '';
    const m = href.match(/[?&]id=([0-9]{6,})/);
    const ms = href.match(/[?&]skuId=([0-9]+)/);
    if (m && ms && m[1] === wantPid && ms[1] === wantSku) {
      return { idx: i, pid: m[1], sku: ms[1], href: href.slice(0, 240) };
    }
  }
  return null;
}"""

# Re-read the cartItemInfo row currently at NodeList index `idx` (variant-path recheck).
_ROW_AT_INDEX_JS = r"""([idx]) => {
  const rows = document.querySelectorAll('[class*="cartItemInfo"]');
  const e = rows[Number(idx)];
  if (!e) return null;
  return { text: (e.innerText || '').replace(/\s+/g, ' ').trim() };
}"""


def _match_remove_row(rows: list[dict], pid: str, variant: str = "",
                      sku_id: str | None = None) -> tuple[dict | None, str]:
    """Pure fail-closed matcher for a cart removal target.

    Rules (never guess — no product-only fallback):
      • sku_id supplied   → ONLY an exact skuId match is acceptable; never falls back
                            to variant or to product-first-line.
      • variant supplied  → ONLY a row whose parsed variant normalized-equals the
                            requested variant; no match → refuse (no product fallback).
      • neither supplied  → refuse (need an explicit exact key).

    Returns (matched_row, "") or (None, reason) where reason ∈
    {"sku_not_found", "variant_not_found", "need_sku_or_variant"}.
    """
    from .compare import _norm  # 复用规范化(与原子购物车/比价口径一致)

    candidates = [r for r in rows if str(r.get("pid")) == str(pid) and r.get("has_del")]
    if sku_id is not None:
        sku = str(sku_id)
        for r in candidates:
            if str(r.get("sku")) == sku:
                return r, ""
        return None, "sku_not_found"
    n_target = _norm(variant or "")
    if not n_target:
        return None, "need_sku_or_variant"
    for r in candidates:
        rv = _parse_cart_item(r.get("text", "") or "").get("variant", "") or ""
        if rv and _norm(rv) == n_target:
            return r, ""
    return None, "variant_not_found"


def _row_matches_variant(text: str, variant: str) -> bool:
    """Pure: does a cart row's text carry a variant normalized-equal to `variant`?"""
    from .compare import _norm

    rv = _parse_cart_item(text or "").get("variant", "") or ""
    return bool(rv) and _norm(rv) == _norm(variant or "")


def _remove_target_index(rows: list[dict], pid: str, variant: str = "",
                         sku_id: str | None = None) -> tuple[int | None, str]:
    """Pure: which cartItemInfo NodeList index is the exact removal target.

    Mirrors the browser re-location so the selector is unit-testable WITHOUT a browser.
    NEVER uses title-prefix first-match — with two rows sharing the same product-title
    prefix (different SKU/variant), the returned index is the EXACT sku/variant row:
      • sku_id   → the row whose href skuId == sku_id.
      • variant  → the row whose normalized variant == target.
    Returns (index, "") or (None, reason ∈ sku_not_found/variant_not_found/
    need_sku_or_variant/no_row_index).
    """
    matched, miss = _match_remove_row(rows, pid, variant, sku_id)
    if matched is None:
        return None, miss
    idx = matched.get("idx")
    if not isinstance(idx, int):
        return None, "no_row_index"
    return idx, ""


def cart_snapshot(items: list[dict]) -> dict[tuple, int]:
    """Pure: {(product_id, sku_id): quantity} from list_cart items.

    Lines without a sku_id key as ("<pid>", "") — stable enough for a delta proof
    within one short atomic flow. Used to PROVE an add landed (+1 on one key) and
    that a removal restored the cart (all deltas back to 0).
    """
    out: dict[tuple, int] = {}
    for it in items or []:
        pid = str(it.get("product_id") or "")
        sku = str(it.get("sku_id")) if it.get("sku_id") is not None else ""
        out[(pid, sku)] = out.get((pid, sku), 0) + _qty_of(it)
    return out


def cart_quantity_delta(pre: list[dict], post: list[dict]) -> dict[tuple, int]:
    """Pure: post − pre per (product_id, sku_id) — the cart_atomic proof primitive.

    • exactly one key == +1  → the add landed on that exact sku.
    • that key returns to 0 after removal → the cart was restored.
    • any key that was >0 BEFORE the add and still != its pre value after the remove
      means a pre-existing quantity was merged/lost → do NOT claim restoration.
    """
    a, b = cart_snapshot(pre or []), cart_snapshot(post or [])
    keys = set(a) | set(b)
    return {k: b.get(k, 0) - a.get(k, 0) for k in keys}


async def remove_cart_item(product_id: str, variant: str = "", qty: int | None = None,
                           sku_id: str | None = None, max_items: int = 100) -> dict:
    """只删购物车里"精确匹配"的那一行 — FAIL CLOSED, 绝不猜测、绝不误删其他行.

    定位规则(与 _match_remove_row 一致):
      ① sku_id 提供 → 只允许精确 skuId 匹配; 找不到精确匹配即返回 not_found,
         绝不退回型号/商品匹配(防止误删购物车原有行)。
      ② variant 提供(sku_id 未给) → 只接受该商品下型号文本规范化精确相等的行;
         无精确匹配即 not_found, 不做"按商品 id 删第一行"回退。
      ③ 两者都未提供 → 拒绝(need_sku_or_variant), 不按商品 id 猜测删除。
    删除走真实点击"删除"按钮 + 确认弹窗, 删除后重读购物车验证目标行确已消失。
    返回删除详情; 任何找不到/验证失败的情况都不删除任何行。
    qty 参数保留仅为接口兼容(淘宝删除是整行级, 不支持部分数量删除)。
    """
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    await page.goto("https://cart.taobao.com/cart.htm", wait_until="domcontentloaded")
    await page.wait_for_timeout(6000)
    # 刚导航到购物车后立即防滑块检查 — 真滑块/风控墙交给人工, CaptchaError 直接上抛,
    # 绝不能被当作"删除失败"吞掉。
    await session.guard_captcha(page)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    rows = await page.evaluate(CART_REMOVE_JS)
    pid = str(product_id)
    target_idx, miss = _remove_target_index(rows, pid, variant, sku_id)
    if target_idx is None:
        note = {
            "sku_not_found": f"购物车无该商品(id={pid})的精确 skuId={sku_id} 匹配行 — 未删除任何行(不做型号/商品回退).",
            "variant_not_found": f"购物车无该商品(id={pid})型号文本精确匹配 variant={variant!r} 的行 — 未删除任何行(不做商品回退).",
            "need_sku_or_variant": "删除需提供精确 sku_id 或精确型号(variant); 无凭据时拒绝删除, 不按商品id猜测.",
            "no_row_index": f"购物车目标行(id={pid})缺少稳定行号(idx) — 无法精确定位, 未删除任何行.",
        }.get(miss, "not_found")
        return {"removed": False, "reason": miss, "note": note}

    # 定位该行并点击删除 — 精确行, 绝不按标题前缀 first-match(同款不同型号会误删)
    from src.browser.pacing import human_delay

    try:
        idx = target_idx
        if sku_id is not None:
            # ① sku_id: 点击前用 JS 重新定位 href 精确含 skuId(+pid) 的行并复核其 href,
            #    防页面漂移/标题前缀误配; 复核不过则 FAIL CLOSED, 不点击。
            loc = await page.evaluate(_FIND_SKU_ROW_JS, [pid, str(sku_id)])
            if not loc or str(loc.get("sku")) != str(sku_id) or str(loc.get("pid")) != pid:
                return {"removed": False, "reason": "verify_failed",
                        "note": f"购物车行 href 复核未通过(id={pid}, skuId={sku_id}) — 未删除任何行."}
            idx = loc.get("idx")
        else:
            # ② variant: 用扫描时的稳定行号 idx 定位同一行, 点击前对该行文本做一次复核。
            recheck = await page.evaluate(_ROW_AT_INDEX_JS, [idx])
            if not _row_matches_variant((recheck or {}).get("text", "") or "", variant):
                return {"removed": False, "reason": "verify_failed",
                        "note": f"行号 {idx} 的文本复核与目标型号不一致 — 未删除任何行."}
        row_el = page.locator('[class*="cartItemInfo"]').nth(idx)
        await row_el.scroll_into_view_if_needed(timeout=3000)
        btn = row_el.locator('[class*="cartOperationItem"], [class*="delete"], [class*="remove"]') \
            .filter(has_text="删除").first
        await btn.click(timeout=4000)
        await human_delay(1.0, 2.0)
        # 找确认按钮: 弹窗里的"确定/删除"按钮(非行内删除)
        confirm = page.locator('[class*="dialog"] [class*="btn"], [class*="confirm"] [class*="btn"], button:has-text("确定"), button:has-text("删除")').last
        if await confirm.count() > 0:
            await confirm.click(timeout=3000)
            await human_delay(1.0, 2.0)
        # 删除/确认动作后再次防滑块检查 — 滑块交给人工并上抛, 不返回"删除失败"假象。
        await session.guard_captcha(page)
    except CaptchaError:
        raise  # 真滑块/风控墙 → 交给人工, 绝不当成普通删除错误吞掉
    except SelectorDriftError:
        raise  # 布局漂移 → 明确抛给调用方修复选择器, 不伪装成删除失败
    except Exception as exc:
        return {"removed": False, "reason": "error", "error": str(exc)[:140]}

    # 验证: 目标行已消失(按与匹配相同的精确键重新判定, 不做宽松回退)
    await page.wait_for_timeout(1500)
    rows2 = await page.evaluate(CART_REMOVE_JS)
    still, _ = _match_remove_row(rows2, pid, variant, sku_id)
    if still is None:
        return {"removed": True, "product_id": pid, "variant": variant, "sku_id": sku_id}
    return {"removed": False, "reason": "verify_failed",
            "note": f"点击删除后精确目标行仍在购物车(可能弹窗未确认) — 未继续删除, 请人工检查."}
