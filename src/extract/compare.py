"""批量对比(买家挑选商品时常用): 短名单商品一屏对比. 只读, 不收藏, 不发消息.

粗查定位阶段: 用户从搜索/收藏里圈出几个候选 → 本工具逐个 fetch_product(单会话顺序、
限速、不批量开 tab), 折叠成一行行对比(标题/店铺/价区间/型号数/最低价/评论数/补贴提示),
方便一眼挑出性价比。绝不触发收藏、绝不重新生成 mi_id、绝不发送任何消息。
"""

from __future__ import annotations

import asyncio
import re

from src.extract.units import unit_price_from_label


def _unit_price(v) -> float | None:
    """Pure: 型号标签含 'N个装' 时算每件单价(共享 helper, 防漂移)."""
    return unit_price_from_label("; ".join((v.properties or {}).values()), v.price)


def _variant_label(v) -> str:
    """变体的规格文本('颜色:黑; 尺寸:L') — 用于与购物车行 variant 匹配."""
    return "; ".join(f"{k}:{val}" for k, val in (v.properties or {}).items())


def _norm(text: str) -> str:
    """规范化文本用于宽松匹配: 去空白/全角/常见噪声, 小写."""
    if not text:
        return ""
    t = str(text).lower()
    t = re.sub(r"\s+", "", t)
    t = t.replace("（", "(").replace("）", ")").replace("：", ":").replace("，", ",")
    return t


def _match_cart_price(product_id: str, variants, cart_items: list[dict]) -> dict:
    """Pure: 把购物车到手价按型号文本匹配到粗查变体.

    返回 {sku_label: {"cart_price": 到手价, "coarse_price": 原价, "matched": bool}}.
    匹配策略(宽松, 因购物车 variant 文本与 SKU label 格式不完全一致):
      1) 同一 product_id 的购物车行;
      2) 规范化文本: 变体 label 与购物车 variant 一方包含另一方(双向子串),
         或逐 property 值双向包含;
      3) 同一商品多行命中时, 每行匹配到对应变体; 无精确命中的变体保留粗查价。
    """
    rows_for_pid = [it for it in cart_items if str(it.get("product_id")) == str(product_id)]
    if not rows_for_pid:
        return {}
    out: dict = {}
    # 优先精确: 变体 label 规范化 == 购物车 variant 规范化
    for v in variants:
        label = _variant_label(v)
        n_label = _norm(label)
        matched_row = None
        for it in rows_for_pid:
            n_var = _norm(it.get("variant", ""))
            if n_var and n_var == n_label:
                matched_row = it
                break
        if matched_row:
            cart_price = matched_row.get("after_price") or matched_row.get("platform_after")
            out[label] = {
                "cart_price": float(cart_price) if cart_price else None,
                "coarse_price": v.price,
                "matched": True,
            }
    # 剩余: 子串匹配(双向包含, 逐 property 值)
    for v in variants:
        label = _variant_label(v)
        if label in out:
            continue
        n_label = _norm(label)
        for it in rows_for_pid:
            n_var = _norm(it.get("variant", ""))
            if not n_var:
                continue
            hit = (n_var in n_label) or (n_label in n_var)
            if not hit:  # 逐 property 值
                hit = any(_norm(val) and (_norm(val) in n_var or n_var in _norm(val))
                          for val in (v.properties or {}).values())
            if hit:
                cart_price = it.get("after_price") or it.get("platform_after")
                out[label] = {
                    "cart_price": float(cart_price) if cart_price else None,
                    "coarse_price": v.price,
                    "matched": True,
                }
                break
    return out


def _summarize(p, cart_overrides: dict | None = None) -> dict:
    """Fold a Product (or an error dict) into one comparison row.

    cart_overrides: _match_cart_price 的结果 — 命中型号用购物车到手价覆盖原价,
    行级标 price_basis='cart'|'mixed'|'coarse' 供调用方感知优惠口径。
    """
    if isinstance(p, dict) and p.get("error"):
        return {"product_id": p.get("product_id"), "error": p.get("error")[:140]}
    variants = getattr(p, "variants", None) or []
    co = cart_overrides or {}
    # 有效价 = 购物车到手价(命中) 否则 粗查价; 按变体逐条算, 空/重复 label 不互相覆盖
    eff_prices: list[tuple[float | None, bool]] = []  # (effective_price, available)
    for v in variants:
        label = _variant_label(v)
        ov = co.get(label)
        eff = ov["cart_price"] if (ov and ov.get("matched") and ov.get("cart_price") is not None) else v.price
        eff_prices.append((eff, v.available))
    prices = sorted({p_ for p_, _ in eff_prices if p_ is not None})
    avail_prices = sorted({p_ for p_, avail in eff_prices if avail and p_ is not None})
    unit_prices = []
    for v, (eff, avail) in zip(variants, eff_prices):
        if not avail or eff is None:
            continue
        u = unit_price_from_label("; ".join((v.properties or {}).values()), eff)
        if u is not None:
            unit_prices.append(u)
    matched_labels = {k for k, v in co.items() if v.get("matched") and v.get("cart_price") is not None}
    if co and matched_labels:
        basis = "cart" if len(matched_labels) >= len(variants) else "mixed"
    else:
        basis = "coarse"
    return {
        "product_id": getattr(p, "product_id", None),
        "title": (getattr(p, "title", "") or "")[:70],
        "shop": getattr(p, "shop_name", None),
        "price_range": getattr(p, "price_range", None),
        "variant_count": len(variants),
        "cheapest": min(prices) if prices else None,
        "cheapest_available": min(avail_prices) if avail_prices else None,  # 不含缺货价
        "cheapest_unit": min(unit_prices) if unit_prices else None,  # 有货最低单价(按'N个装')
        "price_sample": prices[:6],
        "review_count": len(getattr(p, "reviews", None) or []),
        "review_total": getattr(p, "review_total", None),
        "favorable_rate": getattr(p, "favorable_rate", None),
        "subsidy_caveat": getattr(p, "subsidy_caveat", None),
        "url": getattr(p, "url", None),
        "price_basis": basis,
        "cart_overrides": [{"label": k, "cart_price": v["cart_price"], "coarse_price": v["coarse_price"]}
                           for k, v in co.items() if v.get("matched")],
    }


def _to_markdown(rows: list[dict], count: int) -> str:
    """Render compare rows as a readable markdown table (for the GUI/in-chat)."""
    head = ("### 短名单对比({} 件)\n\n"
            "| 商品 | 店铺 | 价区间 | 型号数 | 价格示例(¥) | 评论 | 价格口径 | 补贴/提示 |\n"
            "|---|---|---|---|---|---|---|---|").format(count)
    lines = [head]
    for r in rows:
        if "error" in r:
            lines.append(f"| `{r.get('product_id')}` | — | — | — | — | — | — | ⚠️ {r['error'][:24]} |")
            continue
        caveat = (r.get("subsidy_caveat") or "")[:18]
        sample = ", ".join(str(p) for p in (r.get("price_sample") or [])[:5])
        rt = r.get("review_total") or r.get("review_count") or 0
        fr = r.get("favorable_rate")
        review_cell = f"{rt}" + (f"({fr})" if fr else "")
        pr = r.get("price_range")
        ca = r.get("cheapest_available")
        c = r.get("cheapest")
        cu = r.get("cheapest_unit")
        basis = r.get("price_basis", "coarse")
        basis_cell = {"cart": "🛒到手价", "mixed": "🛒+原价", "coarse": "原价"}.get(basis, basis)
        pr_cell = str(pr) if pr else "—"
        if ca is not None and (c is None or ca != c):
            pr_cell += f" · 有货{ca:g}"
        if cu is not None:
            pr_cell += f" · 最低单价¥{cu:.2f}"
        lines.append(
            f"| {r.get('title','')[:30]} | {r.get('shop') or ''} "
            f"| {pr_cell} | {r.get('variant_count')} "
            f"| {sample} | {review_cell} | {basis_cell} | {caveat} |"
        )
    # 最低单价推荐(有货最低单价最小的商品) — 买家一眼看最优
    unit_rows = [(r.get("title") or r.get("product_id"), r.get("cheapest_unit"))
                 for r in rows if r.get("cheapest_unit") is not None]
    if unit_rows:
        best_title, best_unit = min(unit_rows, key=lambda t: t[1])
        lines.append("")
        lines.append(f"💰 最低单价推荐: {best_title[:28]} (每件¥{best_unit:.2f})")
    return "\n".join(lines)


def _append_variants_markdown(md: str, rows: list[dict]) -> str:
    """Pure: 给对比 markdown 追加每个商品的全型号价表(完整报告)."""
    md += "\n\n---\n\n## 各商品型号明细\n"
    for r in rows:
        vs = r.get("variants_summary") or []
        title = r.get('title') or str(r.get('product_id'))
        if not vs:
            md += "\n### " + title + "(无型号数据)\n"
            continue
        md += "\n### " + title + "\n\n"
        md += "| 型号 | 价格¥ | 库存 | 有货 | 选项图 |\n|---|---|---|---|---|\n"
        for v in vs[:200]:
            price = f"{v['price']:g}" if v["price"] is not None else "-"
            ok = "✓" if v["available"] else "✗"
            img = f"![图]({v['image']})" if v.get("image") else "-"
            md += f"| {v['label'] or '-'} | {price} | {v['stock'] if v['stock'] is not None else '-'} | {ok} | {img} |\n"
        if len(vs) > 200:
            md += f"| … 共 {len(vs)} 个型号(前 200 显示) |\n"
    return md


_CN_REVIEW_RE = re.compile(r"(\d+)\s*(万|千)?")


def _review_total_num(rt) -> int | None:
    """Pure: 把 '1000+' / '5万+' / '860' 解析成数值(过滤/排序用), 否则 None."""
    if rt is None:
        return None
    s = str(rt).replace(",", "").replace("+", "").strip()
    m = _CN_REVIEW_RE.search(s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2) or ""
    return n * 10000 if unit == "万" else n * 1000 if unit == "千" else n


def _sort_rows(rows: list[dict], sort_by: str = "") -> list[dict]:
    """Pure: 对比行排序 — '' 原序 / 'price' 按有货最低价升 / 'unit' 按最低单价升; 错误行排最后."""
    def key(r):
        f = {"price": r.get("cheapest_available"), "unit": r.get("cheapest_unit")}.get(sort_by)
        return f if f is not None else float("inf")
    if sort_by not in ("price", "unit"):
        return list(rows)
    return sorted(rows, key=key)


async def _atomic_add_read_remove(pid: str, p, want: str | None, co: dict, atomic_log: list[dict]) -> dict:
    """原子购物车模式的一步: 加购指定型号 → 读到手价 → 记录待退(真正删除在 finally).

    只对"不在购物车"的型号做; 加购必须显式指定 sku(want), 否则不写购物车(返回原 co)。
    加购走 add_to_cart(confirm=True, options=型号各组值) — 真实芯片点击+skuId 校验,
    失败抛错并分类原因(限购/无货/失效)。成功后在 atomic_log 记账(供 finally 精确退回),
    然后重新读购物车拿该型号到手价, 合并进 co。
    """
    from src.extract.cart_price import list_cart
    from src.cart import add_to_cart

    variants = [v for v in (getattr(p, "variants", None) or [])]
    if not variants:
        return co
    # 确定要加购的型号: want 指定 → 匹配; 否则默认第一个有货型号
    target = None
    if want:
        for v in variants:
            label = _variant_label(v)
            if want in label or _norm(want) == _norm(label) or _norm(label) in _norm(want):
                target = v
                break
    if target is None:
        avail = [v for v in variants if v.available and v.price is not None]
        target = avail[0] if avail else variants[0]
    label = _variant_label(target)
    options = list((target.properties or {}).values())
    if not options:
        return co  # 无型号文本无法加购 — 不写购物车
    # 加购(必须指定型号; 失败会抛带原因的错)
    await add_to_cart(pid, options=options, qty=1, confirm=True, cheapest_available=False)
    # 重新读购物车 → 拿该型号到手价 + 精确定位刚加行的 sku_id(供 finally 精确退回)
    try:
        fresh = (await list_cart(max_items=100)).get("items", []) or []
    except Exception:
        fresh = []
    sku_id = None
    n_opt = _norm("; ".join(options))
    for it in fresh:
        if str(it.get("product_id")) == str(pid):
            n_var = _norm(str(it.get("variant", "")))
            if n_opt and (n_opt in n_var or n_var in n_opt):
                sku_id = it.get("sku_id")
                break
    atomic_log.append({"product_id": pid, "variant": "; ".join(options), "sku_id": sku_id})
    new_co = _match_cart_price(pid, [target], fresh)
    # 合并: 新命中覆盖
    for k, v in new_co.items():
        if v.get("matched"):
            co[k] = v
    return co


async def compare_products(product_ids: list[str], deep_price: bool = False, max_items: int = 10,
                           sort_by: str = "", detailed: bool = False,
                           min_review_total: int = 0, source: str = "coarse",
                           skus: list[str] | None = None) -> dict:
    """批量对比: 对每个短名单商品跑 parse_product(coarse), 折叠成对比行.

    Read-only — 不收藏、不重新生成 mi_id、不发送任何消息。deep_price=True 逐型号点芯片
    读平台加补后价(慢, 适合型号少的商品)。单会话顺序执行 + 限速, 不批量开 tab(CLAUDE.md §7.3)。
    detailed=True 时每行附 variants_summary(全型号价/库存/选项图), 供 with_variants 导出完整报告。
    min_review_total>0 时过滤掉评价数低于阈值的商品(避开低评价商品)。

    source:
      'cart'(默认): 先读购物车到手价(after_price/platform_after), 用型号文本匹配到对应
        变体 → 命中型号用购物车到手价覆盖原价(coarse 只取原价, 会漏长期优惠/补贴), 行级标
        price_basis='cart'|'mixed'|'coarse'。购物车没有该商品时自动退回 coarse 原价(零成本兜底)。
      'cart_atomic': 在 cart 基础上, 购物车**没有**的商品/型号 → 自动"加购指定型号 → 读到手价
        → 退回"(加了多少退多少, try/finally 兜底, 绝不污染用户购物车)。加购必须显式指定 sku;
        失败抛带原因的错(限购/无货/失效)。返回带 atomic_note。
      'coarse': 纯粗查原价(旧行为)。
    skus: 严格指定要比的型号文本列表(与 product_ids 一一对应; 或 {"pid": [型号,...]} 由
      server 层展平)。传了 sku 时, 只对该型号取价(购物车价优先, 无则粗查价/原子加购价)。
    """
    from src.extract.product import parse_product

    ids = [str(x) for x in product_ids[:max_items]]
    src = str(source).strip().lower()
    # 购物车数据(只读, 一次抓取)
    cart_items: list[dict] = []
    if src in ("cart", "cart_atomic"):
        try:
            from src.extract.cart_price import list_cart

            cart_items = (await list_cart(max_items=100)).get("items", []) or []
        except Exception:
            cart_items = []  # 购物车不可用 → 静默退回粗查
    # 原子模式的写账: 加了多少必须退多少(try/finally 兜底, 绝不污染用户购物车)
    atomic_log: list[dict] = []
    rows = []
    try:
        for i, pid in enumerate(ids):
            try:
                p = await asyncio.wait_for(parse_product(pid, deep_price=deep_price), timeout=45)
                # sku 严格指定: 只保留指定型号的变体(购物车优先, 无则粗查价)
                want = None
                if skus is not None and i < len(skus) and skus[i]:
                    want = str(skus[i]).strip()
                if want:
                    keep = []
                    for v in (getattr(p, "variants", None) or []):
                        label = _variant_label(v)
                        if want in label or _norm(want) == _norm(label) or _norm(label) in _norm(want):
                            keep.append(v)
                    if keep:
                        p.variants = keep
                co = _match_cart_price(pid, getattr(p, "variants", None) or [], cart_items)
                # cart_atomic: 该商品不在购物车(无匹配)时, 自动加购指定型号 → 读到手价 → 删除
                if src == "cart_atomic":
                    matched_ids = {k for k, v in (co or {}).items() if v.get("matched")}
                    variant_labels = [_variant_label(v) for v in (getattr(p, "variants", None) or [])]
                    unmatched = [lb for lb in variant_labels if lb not in matched_ids]
                    if unmatched:
                        co = await _atomic_add_read_remove(pid, p, want, co, atomic_log)
                row = _summarize(p, cart_overrides=co)
                if detailed:
                    row["variants_summary"] = [
                        {"label": _variant_label(v),
                         "price": v.price, "stock": v.stock, "available": v.available,
                         "image": v.image,  # 选项图 URL(尺寸/规格常印在图内)
                         "cart_price": (co.get(_variant_label(v)) or {}).get("cart_price")}
                        for v in (getattr(p, "variants", None) or [])
                    ]
                rows.append(row)
            except asyncio.TimeoutError:
                try:
                    from src.browser.session import get_session
                    await get_session().close()
                except Exception:
                    pass
                rows.append({"product_id": pid, "error": "timeout (单件>45s, 已重置浏览器)"})
            except Exception as exc:
                rows.append({"product_id": pid, "error": str(exc)[:140]})
    finally:
        # 原子模式兜底: 加了多少必须退多少(即使中间出错) — 绝不污染用户购物车.
        # 逐条记录退回结果, 失败绝不吞掉 — 必须如实报告让用户知道去检查购物车.
        if atomic_log:
            try:
                from src.extract.cart_price import remove_cart_item

                for entry in reversed(atomic_log):
                    try:
                        res = await remove_cart_item(entry["product_id"],
                                                     variant=entry.get("variant", ""),
                                                     sku_id=entry.get("sku_id"))
                        entry["removed"] = bool(res.get("removed"))
                        entry["remove_reason"] = res.get("reason") if not res.get("removed") else ""
                    except Exception as exc:
                        entry["removed"] = False
                        entry["remove_reason"] = str(exc)[:120]
            except Exception as exc:
                for entry in atomic_log:
                    entry.setdefault("removed", False)
                    entry.setdefault("remove_reason", str(exc)[:120])
    rows = _sort_rows(rows, sort_by)
    if min_review_total > 0:
        rows = [r for r in rows if (_review_total_num(r.get("review_total")) or 0) >= min_review_total]
    result = {"count": len(rows), "products": rows}
    if atomic_log:
        ok = sum(1 for e in atomic_log if e.get("removed"))
        failed = [e for e in atomic_log if not e.get("removed")]
        if not failed:
            result["atomic_note"] = (
                f"原子购物车模式: 临时加购 {len(atomic_log)} 件已全部退回 "
                f"(加了多少退多少); 你的购物车未受影响.")
        else:
            result["atomic_note"] = (
                f"⚠️ 原子购物车模式: 临时加购 {len(atomic_log)} 件, 退回 {ok}/{len(atomic_log)} 件 — "
                f"以下 {len(failed)} 件未能自动删除, 请人工检查购物车并手动删除(绝不能留在购物车): "
                + "; ".join(f"{e.get('product_id')}·{e.get('variant','')[:24]}({e.get('remove_reason','?')[:40]})"
                            for e in failed))
            result["atomic_failed_removals"] = failed
    return result


async def export_compare_markdown(product_ids: list[str], deep_price: bool = False,
                                  max_items: int = 10, sort_by: str = "", with_variants: bool = False,
                                  min_review_total: int = 0, title: str = "",
                                  out_dir: str = "output", source: str = "cart",
                                  skus: list[str] | None = None) -> dict:
    """跑短名单对比并把 markdown 表落盘(output/compare_<ts>.md), 买家留档。

    Reuses compare_products (只读浏览) + _to_markdown; 唯一写入是本地产出文件
    (gitignored), 不收藏、不重新生成 mi_id、不发消息。返回路径 + markdown 内容。
    with_variants=True 时追加每个商品的全型号价表(完整报告)。
    source/skus 透传给 compare_products(购物车到手价优先 / 严格型号)。
    """
    from datetime import datetime
    from pathlib import Path

    data = await compare_products(product_ids, deep_price=deep_price, max_items=max_items,
                                  sort_by=sort_by, detailed=with_variants,
                                  min_review_total=min_review_total, source=source, skus=skus)
    rows = data.get("products") or []
    md = _to_markdown(rows, data.get("count", 0))
    if with_variants:
        md = _append_variants_markdown(md, rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    head = f"> 导出时间: {ts}"
    if title:
        head += f" — {title}"
    md = head + "\n\n" + md
    path = Path(out_dir) / f"compare_{ts}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md + "\n", encoding="utf-8")
    return {"path": str(path), "count": data.get("count", 0), "markdown": md}


async def export_compare_xlsx(product_ids: list[str], deep_price: bool = False,
                              max_items: int = 10, sort_by: str = "", min_review_total: int = 0,
                              out_dir: str = "output", filename: str = "") -> dict:
    """跑短名单对比并导出 xlsx(output/compare_<ts>.xlsx), 买家保留电子表格。

    Reuses compare_products (只读浏览); 唯一写入是本地产出文件(gitignored), 不收藏、
    不重新生成 mi_id、不发消息。返回路径。
    """
    from datetime import datetime
    from pathlib import Path

    from src.output.xlsx_writer import write_compare_xlsx

    data = await compare_products(product_ids, deep_price=deep_price, max_items=max_items, sort_by=sort_by, min_review_total=min_review_total)
    rows = data.get("products") or []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    from src.config import safe_filename
    fname = safe_filename(filename, f"compare_{ts}.xlsx")
    path = await _write_compare_async(rows, fname, out_dir)
    return {"path": path, "count": data.get("count", 0)}


async def _write_compare_async(rows: list[dict], filename: str, out_dir: str) -> str:
    """Run the (CPU-ish) xlsx write off the event loop thread."""
    import anyio

    from src.output.xlsx_writer import write_compare_xlsx

    return await anyio.to_thread.run_sync(write_compare_xlsx, rows, filename, out_dir)
