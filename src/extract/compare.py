"""批量对比(买家挑选商品时常用): 短名单商品一屏对比. 只读, 不收藏, 不发消息.

粗查定位阶段: 用户从搜索/收藏里圈出几个候选 → 本工具逐个 fetch_product(单会话顺序、
限速、不批量开 tab), 折叠成一行行对比(标题/店铺/价区间/型号数/最低价/评论数/补贴提示),
方便一眼挑出性价比。绝不触发收藏、绝不重新生成 mi_id、绝不发送任何消息。
"""

from __future__ import annotations

import asyncio
import re


def _unit_price(v) -> float | None:
    """Pure: 型号标签含 'N个装' 时算每件单价(共享 helper, 防漂移)."""
    from src.extract.units import unit_price_from_label

    return unit_price_from_label("; ".join((v.properties or {}).values()), v.price)


def _summarize(p) -> dict:
    """Fold a Product (or an error dict) into one comparison row."""
    if isinstance(p, dict) and p.get("error"):
        return {"product_id": p.get("product_id"), "error": p.get("error")[:140]}
    variants = getattr(p, "variants", None) or []
    prices = sorted({v.price for v in variants if v.price is not None})
    avail_prices = sorted({v.price for v in variants if v.available and v.price is not None})
    unit_prices = [u for u in (_unit_price(v) for v in variants if v.available) if u is not None]
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
    }


def _to_markdown(rows: list[dict], count: int) -> str:
    """Render compare rows as a readable markdown table (for the GUI/in-chat)."""
    head = ("### 短名单对比({} 件)\n\n"
            "| 商品 | 店铺 | 价区间 | 型号数 | 价格示例(¥) | 评论 | 补贴/提示 |\n"
            "|---|---|---|---|---|---|---|").format(count)
    lines = [head]
    for r in rows:
        if "error" in r:
            lines.append(f"| `{r.get('product_id')}` | — | — | — | — | — | ⚠️ {r['error'][:24]} |")
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
        pr_cell = str(pr) if pr else "—"
        if ca is not None and (c is None or ca != c):
            pr_cell += f" · 有货{ca:g}"
        if cu is not None:
            pr_cell += f" · 最低单价¥{cu:.2f}"
        lines.append(
            f"| {r.get('title','')[:32]} | {r.get('shop') or ''} "
            f"| {pr_cell} | {r.get('variant_count')} "
            f"| {sample} | {review_cell} | {caveat} |"
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
        md += "| 型号 | 价格¥ | 库存 | 有货 |\n|---|---|---|---|\n"
        for v in vs[:200]:
            price = f"{v['price']:g}" if v["price"] is not None else "-"
            ok = "✓" if v["available"] else "✗"
            md += f"| {v['label'] or '-'} | {price} | {v['stock'] if v['stock'] is not None else '-'} | {ok} |\n"
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


async def compare_products(product_ids: list[str], deep_price: bool = False, max_items: int = 10,
                           sort_by: str = "", detailed: bool = False,
                           min_review_total: int = 0) -> dict:
    """粗查批量对比: 对每个短名单商品跑 parse_product, 折叠成对比行.

    Read-only — 不收藏、不重新生成 mi_id、不发送任何消息。deep_price=True 逐型号点芯片
    读平台加补后价(慢, 适合型号少的商品)。单会话顺序执行 + 限速, 不批量开 tab(CLAUDE.md §7.3)。
    detailed=True 时每行附 variants_summary(全型号价/库存), 供 with_variants 导出完整报告。
    min_review_total>0 时过滤掉评价数低于阈值的商品(避开低评价商品)。
    """
    from src.extract.product import parse_product

    ids = [str(x) for x in product_ids[:max_items]]
    rows = []
    for i, pid in enumerate(ids):
        try:
            p = await asyncio.wait_for(parse_product(pid, deep_price=deep_price), timeout=45)
            row = _summarize(p)
            if detailed:
                row["variants_summary"] = [
                    {"label": "; ".join(f"{k}:{val}" for k, val in (v.properties or {}).items()),
                     "price": v.price, "stock": v.stock, "available": v.available}
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
    rows = _sort_rows(rows, sort_by)
    if min_review_total > 0:
        rows = [r for r in rows if (_review_total_num(r.get("review_total")) or 0) >= min_review_total]
    return {"count": len(rows), "products": rows}


async def export_compare_markdown(product_ids: list[str], deep_price: bool = False,
                                  max_items: int = 10, sort_by: str = "", with_variants: bool = False,
                                  min_review_total: int = 0, title: str = "",
                                  out_dir: str = "output") -> dict:
    """跑短名单对比并把 markdown 表落盘(output/compare_<ts>.md), 买家留档。

    Reuses compare_products (只读浏览) + _to_markdown; 唯一写入是本地产出文件
    (gitignored), 不收藏、不重新生成 mi_id、不发消息。返回路径 + markdown 内容。
    with_variants=True 时追加每个商品的全型号价表(完整报告)。
    """
    from datetime import datetime
    from pathlib import Path

    data = await compare_products(product_ids, deep_price=deep_price, max_items=max_items,
                                  sort_by=sort_by, detailed=with_variants,
                                  min_review_total=min_review_total)
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
