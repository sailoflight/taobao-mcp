"""购物车读取(只读): 每件商品的结构化解析 — 标题/型号/优惠/实际到手价.

买家挑选/下单前常用: 一眼看清购物车里每件的实际到手价(店铺优惠后/平台加补后/立减)与
标价。只读, 不写入、不收藏、不发送任何消息。
"""

from __future__ import annotations

import re


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


def _parse_cart_item(text: str) -> dict:
    """Parse one cart-item block's flattened text into structured fields.

    Price renderings seen on the cart page:
      店铺优惠后 ￥ 33 . 75 ￥ 42 . 25          -> after ¥33.75, original ¥42.25
      平台加补后 ￥ 15 . 83 距加入降 ￥ 0 . 15 ￥ 15 . 98 -> platform-after ¥15.83, saving ¥0.15, ¥15.98
      ￥ 10                                   -> plain ¥10
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
    }


def _compute_total(items: list[dict]) -> tuple[float, int]:
    """Pure: 到手价合计 + 排除件数(缺货/下架 不可买, 不计入; 未含运费)."""
    total = 0.0
    excluded = 0
    for it in items:
        if "缺货" in it["title"] or "下架" in it["title"]:
            excluded += 1
            continue
        p = it.get("after_price") or it.get("platform_after")
        if p:
            try:
                total += float(p)
            except ValueError:
                pass
    return round(total, 2), excluded


def _group_by_shop(items: list[dict]) -> list[dict]:
    """Pure: 按店铺分组小计(件数/到手价合计/排除缺货下架), 按合计降序."""
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
                g["total"] += float(p)
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
            // 数量输入框在购物车用非标准结构, 不在此提取(避免死代码)
            out.push({ shop, text: t });
          });
          return out;
        }"""
    )
    items: list[dict] = []
    seen: set = set()
    for b in blocks[:max_items]:
        r = _parse_cart_item(b.get("text", ""))
        r["shop"] = b.get("shop", "")
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
