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


def _match_cart_price(product_id: str, variants, cart_items: list[dict], *,
                      require_exact_sku: bool = False) -> dict:
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
    # 原子模式只接受权威目标 SKU 对应的 DOM 价格行，绝不靠相似型号文本猜价。
    for v in variants:
        label = _variant_label(v)
        expected_sku = str(getattr(v, "sku_id", "") or "")
        for it in rows_for_pid:
            if expected_sku and str(it.get("sku_id") or "") == expected_sku:
                cart_price = it.get("after_price") or it.get("platform_after")
                out[label] = {
                    "cart_price": float(cart_price) if cart_price else None,
                    "coarse_price": v.price,
                    "matched": True,
                }
                break
    if require_exact_sku:
        return out
    # 非原子 cart 口径保留型号文本匹配作为只读价格辅助。
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


def _xhr_cart_snapshot(cart_items) -> dict:
    """Build a complete authoritative snapshot keyed by product and exact SKU.

    SKU-less real lines are represented as ``(product_id, "")`` so they cannot
    disappear from a delta. Missing product IDs or invalid quantities make the
    whole snapshot unprovable and raise instead of being silently skipped.
    """
    from src.errors import CartSnapshotError

    snap: dict = {}
    for it in cart_items or []:
        pid = getattr(it, "product_id", None) if not isinstance(it, dict) else it.get("product_id")
        sku = getattr(it, "sku_id", None) if not isinstance(it, dict) else it.get("sku_id")
        if pid in (None, ""):
            raise CartSnapshotError("a cart line is missing product_id")
        raw_quantity = getattr(it, "quantity", None) if not isinstance(it, dict) else it.get("quantity")
        if isinstance(raw_quantity, bool):
            raise CartSnapshotError(f"cart line {pid}/{sku} has an invalid quantity")
        if isinstance(raw_quantity, int):
            quantity = raw_quantity
        elif isinstance(raw_quantity, str) and raw_quantity.strip().isdigit():
            quantity = int(raw_quantity.strip())
        else:
            raise CartSnapshotError(f"cart line {pid}/{sku} has an invalid quantity")
        if quantity <= 0:
            raise CartSnapshotError(f"cart line {pid}/{sku} has a non-positive quantity")
        key = (str(pid), "" if sku in (None, "") else str(sku))
        snap[key] = snap.get(key, 0) + quantity
    return snap


def _xhr_delta(pre, post) -> dict:
    """Pure: post − pre per (product_id, sku_id) over read_cart snapshots — the cart_atomic
    proof primitive."""
    a, b = _xhr_cart_snapshot(pre or []), _xhr_cart_snapshot(post or [])
    keys = set(a) | set(b)
    return {k: b.get(k, 0) - a.get(k, 0) for k in keys}


def _atomic_delta_ok(product_id: str, sku_id: str, delta: dict) -> tuple[bool, str]:
    """Pure: 加购后的 XHR 快照差(_xhr_delta)必须恰好是目标 (product_id, sku_id): +1, 其余全 0.

    Returns (ok, reason). ok=False 表示无法证明"只加了这一件" — 调用方绝不删除任何行,
    必须交人工检查。delta 键为 (product_id, sku_id)。
    """
    key = (str(product_id), str(sku_id))
    bad: list[str] = []
    for k, d in (delta or {}).items():
        if k == key:
            if d != 1:
                bad.append(f"目标 {key[0]}·{key[1]} 增量 {d}(预期 +1)")
        elif d != 0:
            bad.append(f"{k[0]}·{k[1]} 增量 {d}(预期 0)")
    if key not in (delta or {}):
        bad.append(f"目标 {key[0]}·{key[1]} 未出现(预期 +1)")
    if bad:
        return False, "; ".join(bad[:6]) + (f" (+{len(bad) - 6} more)" if len(bad) > 6 else "")
    return True, ""


def _delta_is_restored(delta: dict) -> bool:
    """Pure: 所有购物车差均为 0 → 加购已完整退回, 购物车与加购前完全一致."""
    return all(d == 0 for d in (delta or {}).values())


def _resolve_atomic_target(want: str | None, variants) -> tuple[object | None, str | None]:
    """Pure: 确定原子加购的目标型号(只做选择/判定, 不写购物车).

    want 非空(显式 sku) → 必须**唯一**命中"归一化精确全标签/属性"匹配:
      命中口径 = 归一化(want) 等于某型号的完整标签(_variant_label, 含属性名) 或 其属性值连串;
      0 个命中(部分/拼写不符) 或 >1 个命中(歧义) ⇒ 返回 (None, 可操作错误) — 调用方不得写入。
    want 为空(未指定 sku) → 确定性选"最低有货价"型号(价格并列按 sku_id 稳定排序);
      无有货带价型号 ⇒ (None, 错误)。返回 (target, None) 或 (None, err)。
    """
    vs = [v for v in (variants or [])]
    if not vs:
        return None, "该商品无型号数据, 无法原子加购 — 未写入"
    if want:
        w = _norm(str(want).strip())
        matches: list = []
        for v in vs:
            n_full = _norm(_variant_label(v))
            n_values = _norm("; ".join((v.properties or {}).values()))
            if w and (w == n_full or w == n_values):
                matches.append(v)
        if not matches:
            return None, (f"型号 {want!r} 无精确匹配(部分/拼写不一致) — 未写入购物车; "
                          f"请提供完整精确型号(如 {_variant_label(vs[0])!r})")
        if len(matches) > 1:
            return None, (f"型号 {want!r} 匹配不唯一(歧义, 命中 {len(matches)} 个型号) — 未写入购物车; "
                          f"请提供可唯一确定的完整型号")
        return matches[0], None
    # 未指定 sku: 确定性选最低有货价(价格并列按 sku_id 稳定, 保证可复现)
    priced = [v for v in vs if v.available and v.price is not None]
    if not priced:
        return None, "该商品没有有货且带价型号可加购 — 未写入购物车"
    return min(priced, key=lambda v: (float(v.price), str(v.sku_id), _norm(_variant_label(v)))), None



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


async def _atomic_add_read_remove(pid: str, p, want: str | None, co: dict,
                                  atomic_log: list[dict], pre_xhr: list | None) -> dict:
    """安全原子购物车比价一步(2026-08-20 rebuild): 加购→读到手价→退回, 全程可证明.

    数量证明一律用**权威 XHR**(account.read_cart → CartItem.sku_id/.quantity, 来自
    mtop.trade.query.bag), 不用 DOM 数量; DOM(list_cart)只用于读到手价。

    只对"购物车没有"的目标型号执行, 且每一步都要求**证明**才继续:
      1) 加购前 XHR 快照 = _xhr_cart_snapshot(pre_xhr){sku_id: qty}(调用方一次抓取)。
      2) 目标型号精确 sku_id 已在快照 → **零写入**, 直接从购物车数据读到手价。
      3) 否则 加购恰好 1 件(add_to_cart 内部校验 live skuId == 该型号 expected sku_id);
         重新读 XHR, 用 _xhr_delta 证明"目标 sku_id 恰好 +1 且其余行与快照完全一致" →
         才按**精确 sku_id** 退回(remove_cart_item 已 fail-closed, sku_id 提供时绝不回退到
         型号/商品); 无法证明 → **绝不删除**, 记 require_manual。
      4) 退回后再读 XHR, _xhr_delta 必须全 0(购物车与加购前一致), 否则 require_manual。
      任何一步"无法证明"都返回显式人工检查警告, 不做任何猜测性删除。
    """
    from src.extract.account import read_cart
    from src.extract.cart_price import list_cart, remove_cart_item
    from src.cart import add_to_cart

    variants = [v for v in (getattr(p, "variants", None) or [])]
    if not variants:
        return co
    # 目标型号: 显式 want → 唯一归一化精确全标签/属性匹配(歧义/部分 ⇒ 不写入 + 可操作错误);
    # 未指定 want → 确定性选最低有货价型号(价格并列按 sku_id 稳定), 结果中注明所选。
    target, resolve_err = _resolve_atomic_target(want, variants)
    if resolve_err:
        atomic_log.append({"product_id": pid, "variant": (want or ""), "sku_id": None,
                           "mode": "no_write", "mutated": False, "removed": True,
                           "error": resolve_err})
        return co  # 不写入购物车
    sku_id = str(target.sku_id)
    label = _variant_label(target)
    options = list((target.properties or {}).values())
    if not options:
        atomic_log.append({"product_id": pid, "variant": label, "sku_id": sku_id,
                           "mode": "no_write", "mutated": False, "removed": True,
                           "error": "该型号无属性文本, 无法经购物车加购 — 未写入"})
        return co
    pre = pre_xhr
    if pre is None:
        atomic_log.append({"product_id": pid, "variant": label, "sku_id": sku_id,
                           "mode": "skip_snapshot", "mutated": False, "removed": True,
                           "require_manual": True,
                           "remove_reason": "加购前无法取得权威 query.bag 基线快照 — 未执行加购, 请人工检查"})
        return co

    # 2) 精确 (product_id, sku_id) 已在购物车(XHR 快照命中) → 零写入, 直接读到手价
    if (str(pid), str(sku_id)) in _xhr_cart_snapshot(pre):
        try:
            dom = (await list_cart(max_items=100)).get("items", []) or []
        except Exception:
            dom = []
        new_co = _match_cart_price(pid, [target], dom, require_exact_sku=True)
        for k, v in new_co.items():
            if v.get("matched"):
                co[k] = v
        atomic_log.append({"product_id": pid, "variant": label, "sku_id": sku_id,
                           "chosen_variant": label, "auto_chosen": not want,
                           "mode": "read_only", "mutated": False, "removed": True})
        return co

    # 3) 加购前核对当前 XHR 快照 == 加购前(防上件残留/人工改车导致差被污染)
    try:
        cur_xhr = await read_cart(require_snapshot=True)
        unchanged = _delta_is_restored(_xhr_delta(pre, cur_xhr))
    except Exception as exc:
        atomic_log.append({"product_id": pid, "variant": label, "sku_id": sku_id,
                           "mode": "skip_snapshot", "mutated": False, "removed": True,
                           "require_manual": True,
                           "remove_reason": "加购前权威购物车快照不可证明 — 未执行加购: " + str(exc)[:120]})
        return co
    if not unchanged:
        atomic_log.append({"product_id": pid, "variant": label, "sku_id": sku_id,
                           "mode": "skip_drift", "mutated": False, "removed": True,
                           "require_manual": True,
                           "note": "加购前购物车 XHR 快照已不一致(上件可能未退回/人工改过) — 跳过加购, 请人工检查"})
        return co

    # 加购恰好 1 件(add_to_cart 内部会校验 live skuId == 目标 expected sku_id)
    try:
        await add_to_cart(pid, options=options, qty=1, confirm=True, cheapest_available=False)
    except Exception as exc:
        # The API may have committed before its response failed. State is unproven, so
        # record a possible mutation and never attempt an ambiguous automatic delete.
        atomic_log.append({"product_id": pid, "variant": label, "sku_id": sku_id,
                           "mode": "add_uncertain", "mutated": True, "removed": False,
                           "require_manual": True,
                           "remove_reason": "加购调用结果不确定(可能已写入) — 绝不自动删除, 请人工检查: " + str(exc)[:120]})
        return co
    entry = {"product_id": pid, "variant": label, "sku_id": sku_id,
             "chosen_variant": label, "auto_chosen": not want,
             "mode": "added", "mutated": True, "removed": False}
    atomic_log.append(entry)

    # 4) 重新读权威 XHR → 必须证明"目标 sku_id 恰好 0→1 且其余行不变", 否则不删
    try:
        fresh_xhr = await read_cart(require_snapshot=True)
        delta = _xhr_delta(pre, fresh_xhr)
    except Exception as exc:
        entry.update({"require_manual": True,
                      "remove_reason": "加购后权威购物车快照不可证明 — 绝不猜测删除, 请人工检查: " + str(exc)[:120]})
        return co
    ok, reason = _atomic_delta_ok(pid, sku_id, delta)
    if not ok:
        entry.update({"require_manual": True, "remove_reason": "无法证明只加了这一件: " + reason})
        return co
    # 读 DOM 到手价(含刚加行; 数量证明只信 XHR, 价格用 DOM)
    try:
        fresh_dom = (await list_cart(max_items=100)).get("items", []) or []
    except Exception:
        fresh_dom = []
    # 5) 按精确 sku_id 退回(fail-closed, 不回退到型号/商品)
    try:
        res = await remove_cart_item(pid, sku_id=sku_id)
        entry["removed"] = bool(res.get("removed"))
        entry["remove_reason"] = res.get("reason") if not res.get("removed") else ""
    except Exception as exc:
        entry["removed"] = False
        entry["remove_reason"] = str(exc)[:120]
    if not entry.get("removed"):
        entry["require_manual"] = True
    # 6) 退回后再读权威 XHR → 只在最终权威快照与加购前基线**逐键完全相等**(每个
    #    (product_id, sku_id) 数量都一致)时才声明已恢复 — 不因"目标已不在"就认定还原。
    try:
        final_xhr = await read_cart(require_snapshot=True)
        restored = bool(_xhr_cart_snapshot(final_xhr) == _xhr_cart_snapshot(pre))
    except Exception:
        restored = False
    entry["restored"] = bool(restored)
    if not restored:
        entry["require_manual"] = True
        entry.setdefault("remove_reason", (entry.get("remove_reason") or "") + " 最终购物车与加购前不一致 — 请人工检查")
    # 合并到手价(用 fresh_dom, 含刚加行)
    new_co = _match_cart_price(pid, [target], fresh_dom, require_exact_sku=True)
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
      'cart_atomic': 在 cart 基础上, 购物车**没有**的目标型号 → "加购恰好1件 → 读到手价 →
        退回"(安全版: 加购前快照 + 加购后证明目标 sku 恰好 +1 且其余行不变 + 按精确 skuId
        退回 + 退回后再核对购物车与快照一致; 无法证明则不删除并提示人工检查。已在购物车的
        型号零写入直接读到手价)。加购必须显式指定 sku; 失败抛带原因的错(限购/无货/失效)。
        返回带 atomic_note。
      'coarse': 纯粗查原价(旧行为)。
    skus: 严格指定要比的型号文本列表(与 product_ids 一一对应; 或 {"pid": [型号,...]} 由
      server 层展平)。传了 sku 时, 只对该型号取价(购物车价优先, 无则粗查价/原子加购价)。
    """
    from src.extract.product import parse_product

    ids = [str(x) for x in product_ids[:max_items]]
    src = str(source).strip().lower()
    # 购物车数据(只读, 一次抓取) — pre_items 是"加购前快照"的原始行, 原子加购全程用它核对
    cart_items: list[dict] = []
    if src in ("cart", "cart_atomic"):
        try:
            from src.extract.cart_price import list_cart

            cart_items = (await list_cart(max_items=100)).get("items", []) or []  # DOM: 只用于价格
        except Exception:
            cart_items = []  # 购物车不可用 → 静默退回粗查
    # 权威 XHR 快照(cart_atomic 的数量证明只信 account.read_cart / query.bag, 不用 DOM 数量)
    pre_xhr: list | None = []
    if src == "cart_atomic":
        try:
            from src.extract.account import read_cart

            pre_xhr = await read_cart(require_snapshot=True)
        except Exception:
            pre_xhr = None
    # 原子模式的写账: 加了多少必须退多少; 每一步"无法证明"都记 require_manual, 绝不猜测删除
    atomic_log: list[dict] = []
    rows = []
    try:
        for i, pid in enumerate(ids):
            try:
                p = await asyncio.wait_for(parse_product(pid, deep_price=deep_price), timeout=45)
                # sku 严格指定: 只在"唯一精确匹配"时收窄到该型号(歧义/部分 → 不收窄, 保持全型号
                # 展示; cart_atomic 路径随后由 _atomic_add_read_remove 记 no_write + 可操作错误,
                # 绝不写入购物车)
                want = None
                if skus is not None and i < len(skus) and skus[i]:
                    want = str(skus[i]).strip()
                if want:
                    _tgt, _resolve_err = _resolve_atomic_target(want, getattr(p, "variants", None) or [])
                    if _tgt is not None:
                        p.variants = [_tgt]
                    elif src != "cart_atomic":
                        raise ValueError(_resolve_err)
                co = _match_cart_price(pid, getattr(p, "variants", None) or [], cart_items)
                # cart_atomic: 该商品不在购物车(无匹配)时, 安全加购指定型号 → 读到手价 → 退回
                if src == "cart_atomic":
                    # DOM 文本可能模糊命中错误型号；原子模式始终用权威基线的精确
                    # (product_id, sku_id) 决定零写入还是执行可证明事务。
                    co = await _atomic_add_read_remove(pid, p, want, co, atomic_log, pre_xhr)
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
        # 原子模式安全网: 只重试"已加购但内联未退回"的条目(精确 sku_id, fail-closed)。
        # ⛔ 绝不重试 require_manual 条目(无法证明时不删, 交人工); 绝不回退到型号/商品。
        pending = [e for e in atomic_log
                   if e.get("mutated") and not e.get("removed") and not e.get("require_manual")]
        for entry in pending:
            try:
                from src.extract.cart_price import remove_cart_item

                res = await remove_cart_item(entry["product_id"], sku_id=entry.get("sku_id"))
                entry["removed"] = bool(res.get("removed"))
                entry["remove_reason"] = res.get("reason") if not res.get("removed") else ""
            except Exception as exc:
                entry["removed"] = False
                entry["remove_reason"] = str(exc)[:120]
            if not entry.get("removed"):
                entry["require_manual"] = True
    rows = _sort_rows(rows, sort_by)
    if min_review_total > 0:
        rows = [r for r in rows if (_review_total_num(r.get("review_total")) or 0) >= min_review_total]
    result = {"count": len(rows), "products": rows}
    if atomic_log:
        unremoved = [e for e in atomic_log if e.get("mutated") and not e.get("removed")]
        manual = [e for e in atomic_log if e.get("require_manual")]
        no_write = [e for e in atomic_log if e.get("mode") == "no_write"]
        auto_chosen = [e for e in atomic_log if e.get("auto_chosen")]
        if not unremoved and not manual:
            note = (f"原子购物车模式(安全版): {len(atomic_log)} 件全部零写入或已按精确 skuId 退回; "
                    f"购物车与加购前快照一致, 未受影响.")
            if auto_chosen:
                note += " 未指定 sku 的商品自动选了最低有货价型号: " + "; ".join(
                    f"{e.get('product_id')}·{e.get('chosen_variant','')}" for e in auto_chosen) + "."
            if no_write:
                note += " 另有未写入项(型号无法唯一匹配/无有货价, 未改动购物车): " + "; ".join(
                    f"{e.get('product_id')}·{e.get('variant','')[:16]}({e.get('error','')[:50]})"
                    for e in no_write) + "."
            result["atomic_note"] = note
        else:
            concern = unremoved + [e for e in manual if e not in unremoved]
            result["atomic_note"] = (
                f"⚠️ 原子购物车模式: {len(atomic_log)} 件中 {len(unremoved)} 件未能自动退回"
                + (f", {len(manual)} 件需人工确认" if manual else "")
                + " — 以下请人工检查购物车(无法证明时不自动删除; 若加购已发生, 该行可能仍在购物车, "
                  "请人工核对并手动清理): "
                + "; ".join(f"{e.get('product_id')}·{e.get('variant','')[:20]}({e.get('remove_reason','?')[:50]})"
                            for e in concern))
            if no_write:
                result["atomic_note"] += " 未写入项(型号无法唯一匹配/无有货价, 未改动购物车): " + "; ".join(
                    f"{e.get('product_id')}·{e.get('variant','')[:16]}({e.get('error','')[:60]})"
                    for e in no_write) + "."
            result["atomic_failed_removals"] = concern
        if no_write:
            result["atomic_skipped"] = no_write
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
                              out_dir: str = "output", filename: str = "",
                              source: str = "cart",
                              skus: list[str] | None = None) -> dict:
    """跑短名单对比并导出 xlsx(output/compare_<ts>.xlsx), 买家保留电子表格。

    Reuses compare_products; 唯一写入是本地产出文件(gitignored), 不收藏、
    不重新生成 mi_id、不发消息。返回路径。
    source/skus 透传给 compare_products — source=cart_atomic 时与 md 导出一致,
    由调用方(taobao_export)先做 atomic_confirm 门, 这里同样执行安全加购→读价→精确 skuId 退回。
    """
    from datetime import datetime
    from pathlib import Path

    from src.output.xlsx_writer import write_compare_xlsx

    data = await compare_products(product_ids, deep_price=deep_price, max_items=max_items,
                                  sort_by=sort_by, min_review_total=min_review_total,
                                  source=source, skus=skus)
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
