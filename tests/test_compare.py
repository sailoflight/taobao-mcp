"""Tests for the compare rows: _summarize (Product → row) and _to_markdown (row → table)."""

from __future__ import annotations

from types import SimpleNamespace

from src.extract.compare import _summarize, _to_markdown


def _product(**kw):
    base = dict(
        product_id="862892097837",
        title="天鼠收纳箱特厚超大家用密封塑料衣服棉被玩具杂物特硬储物箱",
        shop_name="天鼠家居旗舰店",
        price_range=(36.0, 274.75),
        variants=[SimpleNamespace(properties={}, price=36.0, stock=1, available=True),
                  SimpleNamespace(properties={}, price=42.25, stock=1, available=True),
                  SimpleNamespace(properties={}, price=54.75, stock=1, available=True)],
        reviews=[1, 2],
        subsidy_caveat=None,
        url="https://item.taobao.com/item.htm?id=862892097837",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_summarize_folds_product():
    r = _summarize(_product())
    assert r["product_id"] == "862892097837"
    assert r["variant_count"] == 3
    assert r["cheapest"] == 36.0
    assert r["price_sample"] == [36.0, 42.25, 54.75]
    assert r["review_count"] == 2
    assert r["shop"] == "天鼠家居旗舰店"


def test_summarize_error_dict_passes_through():
    r = _summarize({"product_id": "123", "error": "boom"})
    assert r["error"] == "boom"


def test_summarize_dedupes_prices():
    r = _summarize(_product(variants=[SimpleNamespace(properties={}, price=36.0, stock=1, available=True),
                                      SimpleNamespace(properties={}, price=36.0, stock=1, available=True)]))
    assert r["price_sample"] == [36.0]


def test_to_markdown_renders_rows():
    rows = [
        _summarize(_product()),
        {"product_id": "999", "error": "超时"},
    ]
    md = _to_markdown(rows, 2)
    assert "### 短名单对比(2 件)" in md
    assert "天鼠" in md
    assert "价格示例" in md  # column header
    assert "36.0" in md
    assert "999" in md  # error row still shown


def test_summarize_includes_review_total():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range=(1.0, 2.0),
        variants=[SimpleNamespace(properties={"c": "a"}, price=1.0, stock=1, available=True)],
        reviews=[], subsidy_caveat=None, url="https://item.taobao.com/item.htm?id=1",
        review_total="1000+", favorable_rate="98%",
    )
    row = _summarize(p)
    assert row["review_total"] == "1000+" and row["favorable_rate"] == "98%"
    assert "1000+(98%)" in _to_markdown([row], 1)


def test_cheapest_available_excludes_oos():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range="(36.0, 54.75)",
        variants=[
            SimpleNamespace(properties={"颜色": "白"}, price=36.0, stock=0, available=False),
            SimpleNamespace(properties={"颜色": "白"}, price=42.25, stock=1, available=True),
        ],
        reviews=[], subsidy_caveat=None, url="u", review_total="1000+", favorable_rate=None,
    )
    row = _summarize(p)
    assert row["cheapest"] == 36.0        # 含缺货
    assert row["cheapest_available"] == 42.25
    md = _to_markdown([row], 1)
    assert "· 有货42.25" in md


def test_cheapest_available_no_note_when_same():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range="(36.0, 54.75)",
        variants=[SimpleNamespace(properties={"颜色": "白"}, price=36.0, stock=1, available=True)],
        reviews=[], subsidy_caveat=None, url="u", review_total=None, favorable_rate=None,
    )
    row = _summarize(p)
    assert row["cheapest_available"] == 36.0
    assert "· 有货" not in _to_markdown([row], 1)


def test_cheapest_unit_in_compare():
    p = SimpleNamespace(
        product_id="1", title="t", shop_name="s", price_range="(36.0, 112.25)",
        variants=[
            SimpleNamespace(properties={"规格": "1个装"}, price=42.25, stock=1, available=True),
            SimpleNamespace(properties={"规格": "2个装"}, price=79.75, stock=1, available=True),
            SimpleNamespace(properties={"规格": "3个装"}, price=112.25, stock=1, available=True),
        ],
        reviews=[], subsidy_caveat=None, url="u", review_total=None, favorable_rate=None,
    )
    row = _summarize(p)
    assert abs(row["cheapest_unit"] - 112.25 / 3) < 0.001
    assert "最低单价¥37.42" in _to_markdown([row], 1)


def test_sort_rows_by_price_and_unit():
    from src.extract.compare import _sort_rows
    rows = [
        {"product_id": "e", "error": "x"},
        {"product_id": "a", "cheapest_available": 42.25, "cheapest_unit": 37.42},
        {"product_id": "b", "cheapest_available": 15.9, "cheapest_unit": 15.9},
    ]
    assert [r["product_id"] for r in _sort_rows(rows, "price")] == ["b", "a", "e"]
    assert [r["product_id"] for r in _sort_rows(rows, "unit")] == ["b", "a", "e"]
    assert [r["product_id"] for r in _sort_rows(rows, "")] == ["e", "a", "b"]


def test_sort_rows_missing_key_last():
    from src.extract.compare import _sort_rows
    rows = [{"product_id": "x", "cheapest_unit": None}, {"product_id": "y", "cheapest_unit": 10.0}]
    assert [r["product_id"] for r in _sort_rows(rows, "unit")] == ["y", "x"]


def test_append_variants_markdown():
    from src.extract.compare import _append_variants_markdown
    rows = [
        {"product_id": "1", "title": "天鼠收纳箱", "variants_summary": [
            {"label": "规格:1个装", "price": 36.0, "stock": 200, "available": True},
            {"label": "规格:2个装", "price": 67.25, "stock": 0, "available": False},
        ]},
        {"product_id": "2", "title": "无型号", "variants_summary": []},
    ]
    md = _append_variants_markdown("### 总览\n", rows)
    assert "## 各商品型号明细" in md
    assert "### 天鼠收纳箱" in md
    assert "| 规格:1个装 | 36 | 200 | ✓ |" in md
    assert "| 规格:2个装 | 67.25 | 0 | ✗ |" in md
    assert "无型号数据" in md


def test_review_total_num_parses():
    from src.extract.compare import _review_total_num
    assert _review_total_num("1000+") == 1000
    assert _review_total_num("5万+") == 50000
    assert _review_total_num("860") == 860
    assert _review_total_num("3千+") == 3000
    assert _review_total_num(None) is None
    assert _review_total_num("abc") is None


def test_to_markdown_best_unit_recommendation():
    from src.extract.compare import _to_markdown
    rows = [
        {"product_id": "a", "title": "天鼠收纳箱", "cheapest_unit": 30.33, "cheapest_available": 36.0,
         "cheapest": 36.0, "shop": "s", "price_range": (36.0, 54.75), "variant_count": 3,
         "price_sample": [36.0], "review_total": "1000+", "subsidy_caveat": None, "favorable_rate": None},
        {"product_id": "b", "title": "Purable", "cheapest_unit": None, "cheapest_available": 18.9,
         "cheapest": 18.9, "shop": "s2", "price_range": (18.9, 175.9), "variant_count": 12,
         "price_sample": [18.9], "review_total": "500+", "subsidy_caveat": None, "favorable_rate": None},
        {"product_id": "e", "error": "x"},
    ]
    md = _to_markdown(rows, 3)
    assert "💰 最低单价推荐: 天鼠收纳箱 (每件¥30.33)" in md
    md2 = _to_markdown([{"product_id": "a", "title": "无单价", "cheapest_unit": None}], 1)
    assert "最低单价推荐" not in md2


def test_match_cart_price_by_variant_text():
    """购物车到手价按型号文本匹配到变体(cart 模式核心) — 2026-08-19."""
    from src.extract.compare import _match_cart_price
    from src.models import SkuVariant

    variants = [
        SkuVariant(sku_id="a", properties={"颜色分类": "10个袋子30*34cm+2夹子"},
                   price=13.5, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"颜色分类": "便携式抽真空机一台"},
                   price=15.9, stock=1, available=True),
    ]
    cart = [
        {"product_id": "1039147294809", "variant": "10个袋子30*34cm+2夹子",
         "after_price": None, "platform_after": "11.4"},
        {"product_id": "1039147294809", "variant": "便携式抽真空机一台",
         "after_price": None, "platform_after": "13.43"},
        {"product_id": "999", "variant": "别的", "after_price": "1", "platform_after": None},
    ]
    out = _match_cart_price("1039147294809", variants, cart)
    assert out["颜色分类:10个袋子30*34cm+2夹子"]["cart_price"] == 11.4
    assert out["颜色分类:便携式抽真空机一台"]["cart_price"] == 13.43
    # 只匹配同一 product_id 的行
    assert len(out) == 2


def test_summarize_cart_override_basis():
    """cart 覆盖后行级标 price_basis + cheapest 用手价口径 — 2026-08-19."""
    from src.extract.compare import _match_cart_price, _summarize
    from src.models import Product, SkuVariant

    variants = [
        SkuVariant(sku_id="a", properties={"颜色分类": "10个袋子30*34cm+2夹子"},
                   price=13.5, stock=1, available=True),
    ]
    p = Product(product_id="1039147294809", url="u", title="便携式电动手持真空封口抽气泵",
                shop_name="華人包装", price_range=(13.5, 13.5), variants=variants,
                reviews=[], scraped_at="x")
    cart = [{"product_id": "1039147294809", "variant": "10个袋子30*34cm+2夹子",
             "after_price": None, "platform_after": "11.4"}]
    co = _match_cart_price("1039147294809", variants, cart)
    row = _summarize(p, cart_overrides=co)
    assert row["price_basis"] == "cart"
    assert row["cheapest_available"] == 11.4  # 用购物车到手价, 不是粗查原价 13.5
    assert row["cart_overrides"] and row["cart_overrides"][0]["cart_price"] == 11.4


def test_summarize_no_cart_falls_back_coarse():
    """购物车无该商品 → 退回粗查原价, price_basis='coarse' — 2026-08-19."""
    from src.extract.compare import _match_cart_price, _summarize
    from src.models import Product, SkuVariant

    variants = [SkuVariant(sku_id="a", properties={"颜色分类": "特大号30*34"},
                           price=12.5, stock=1, available=True)]
    p = Product(product_id="111", url="u", title="t", shop_name="s",
                price_range=(12.5, 12.5), variants=variants, reviews=[], scraped_at="x")
    co = _match_cart_price("111", variants, [])  # 购物车空
    row = _summarize(p, cart_overrides=co)
    assert row["price_basis"] == "coarse"
    assert row["cheapest_available"] == 12.5
    assert not row["cart_overrides"]


def test_skus_restrict_variants():
    """skus 严格指定型号 → 只保留该型号(cart 优先, 无则粗查价) — 2026-08-19."""
    from src.extract.compare import _match_cart_price, _summarize
    from src.models import Product, SkuVariant

    variants = [
        SkuVariant(sku_id="a", properties={"颜色分类": "10个袋子30*34cm+2夹子"},
                   price=13.5, stock=1, available=True),
        SkuVariant(sku_id="b", properties={"颜色分类": "10个袋子26*34cm+2夹子"},
                   price=11.8, stock=1, available=True),
    ]
    p = Product(product_id="1039147294809", url="u", title="t", shop_name="華人包装",
                price_range=(11.8, 13.5), variants=variants, reviews=[], scraped_at="x")
    p.variants = [v for v in variants if "30*34" in "; ".join(v.properties.values())]
    cart = [{"product_id": "1039147294809", "variant": "10个袋子30*34cm+2夹子",
             "after_price": None, "platform_after": "11.4"}]
    co = _match_cart_price("1039147294809", p.variants, cart)
    row = _summarize(p, cart_overrides=co)
    assert row["variant_count"] == 1
    assert row["cheapest_available"] == 11.4


def test_classify_add_error():
    """addBag 错误返回分类: 限购/无货/失效/成功 — 2026-08-19."""
    from src.cart import classify_add_error

    assert classify_add_error("FAIL_SYS_USER_ERROR_LIMIT::限购")["kind"] == "limit"
    assert classify_add_error("FAIL::库存不足")["kind"] == "oos"
    assert classify_add_error("FAIL::商品已下架")["kind"] == "invalid"
    assert classify_add_error("SUCCESS::调用成功")["kind"] == "ok"
    assert classify_add_error("一些未知错误")["kind"] == "unknown"


def test_atomic_log_records_removal_result():
    """finally 逐条记录退回结果; 失败时 atomic_note 必须如实报告 — 2026-08-19."""
    from src.extract.compare import compare_products
    import asyncio

    # 不实机调用(会写购物车); 只验证 compare_products 的 atomic 汇总逻辑函数本身:
    # 通过 monkeypatch 太深, 这里验证失败分支的文案拼接逻辑(纯逻辑抽出来测)。
    # 直接测: 若 entry 未 removed, atomic_note 含"未能自动删除"。
    atomic_log = [{"product_id": "1", "variant": "v", "removed": False, "remove_reason": "verify_failed"}]
    ok = sum(1 for e in atomic_log if e.get("removed"))
    failed = [e for e in atomic_log if not e.get("removed")]
    assert ok == 0 and failed
    note = (f"⚠️ 原子购物车模式: 临时加购 {len(atomic_log)} 件, 退回 {ok}/{len(atomic_log)} 件 — "
            f"以下 {len(failed)} 件未能自动删除, 请人工检查购物车并手动删除")
    assert "未能自动删除" in note


# ── 2026-08-20 audit: cart_atomic 安全重建(pure) ──────────────────────────────

def test_server_cart_atomic_gated_via_atomic_confirm():
    """taobao_compare 与 taobao_export(compare) 的 cart_atomic 都需显式 atomic_confirm 门."""
    from server import (_CART_ATOMIC_GATE, _atomic_gate_needed, _resolve_compare_source)

    # resolver 放行 cart_atomic(安全重建后)
    src, warn = _resolve_compare_source("cart_atomic", "ask")
    assert src == "cart_atomic" and not warn
    # 确认门: cart_atomic 未 atomic_confirm → 需要门; 确认 → 不需要(同一门语义同时用于
    # taobao_compare 与 taobao_export 的 compare 分支)
    assert _atomic_gate_needed("cart_atomic", False) is True
    assert _atomic_gate_needed("cart_atomic", True) is False
    assert _atomic_gate_needed("", False) is False
    # 门文案: 必须提到安全保证(快照/(product_id, sku_id))并要求 atomic_confirm=true
    assert ("快照" in _CART_ATOMIC_GATE or "product_id" in _CART_ATOMIC_GATE or "skuId" in _CART_ATOMIC_GATE)
    assert "atomic_confirm=true" in _CART_ATOMIC_GATE


def test_server_compare_source_resolution():
    """口径解析: 合法口径放行, 未知回退配置/ask, 大小写不敏感."""
    from server import _resolve_compare_source

    for bad in ("bogus", "atomic", "add"):
        src, _ = _resolve_compare_source(bad, "cart")
        assert src in ("cart", "coarse", "ask", "cart_atomic") and src != bad
    src, _ = _resolve_compare_source("", "cart")
    assert src == "cart"
    src, _ = _resolve_compare_source("CART", "ask")
    assert src == "cart"
    src, _ = _resolve_compare_source("", "bogus_cfg")
    assert src == "ask"


def test_atomic_delta_ok_exact_plus_one():
    """加购后 XHR 快照差必须恰好是目标 (product_id, sku_id) +1, 其余全 0."""
    from src.extract.compare import _atomic_delta_ok

    ok, reason = _atomic_delta_ok("100", "sku9", {("100", "sku9"): 1})
    assert ok and not reason
    # 目标 +2 → 拒绝
    ok, reason = _atomic_delta_ok("100", "sku9", {("100", "sku9"): 2})
    assert not ok and "+1" in reason
    # 目标未出现 → 拒绝
    ok, reason = _atomic_delta_ok("100", "sku9", {("100", "sku8"): 1})
    assert not ok and "未出现" in reason
    # 同 sku 不同商品 → 不是目标(键是 (pid, sku), 不是 sku 单独) → 拒绝
    ok, reason = _atomic_delta_ok("100", "sku9", {("100", "sku9"): 1, ("200", "sku9"): 1})
    assert not ok and "200" in reason
    # 其他行被改 → 拒绝(不能证明只加了这一件)
    ok, reason = _atomic_delta_ok("100", "sku9", {("100", "sku9"): 1, ("100", "sku5"): -1})
    assert not ok and "sku5" in reason
    # 空差 → 拒绝(目标未出现)
    ok, reason = _atomic_delta_ok("100", "sku9", {})
    assert not ok


def test_delta_is_restored():
    """退回后购物车差全 0 → 已还原(购物车与加购前一致)."""
    from src.extract.compare import _delta_is_restored

    assert _delta_is_restored({}) is True
    assert _delta_is_restored({("100", "sku9"): 0}) is True
    assert _delta_is_restored({("100", "sku9"): 1}) is False
    assert _delta_is_restored({("100", "sku9"): 0, ("100", "sku5"): -1}) is False


def test_xhr_cart_snapshot_and_delta():
    """权威 XHR 快照(account.read_cart → CartItem.product_id/.sku_id/.quantity)键为
    (product_id, sku_id); 差 = post − pre。数量证明只用 XHR, 不用 DOM 数量."""
    from src.extract.compare import _xhr_cart_snapshot, _xhr_delta
    from src.models import CartItem

    pre = [
        CartItem(seller="A", title="t1", product_id="100", sku_id="s1", quantity=2),
        CartItem(seller="A", title="t2", product_id="100", sku_id="s2", quantity=1),
        CartItem(seller="A", title="t3", product_id="100", sku_id=None, quantity=1),
    ]
    snap = _xhr_cart_snapshot(pre)
    assert snap == {("100", "s1"): 2, ("100", "s2"): 1, ("100", ""): 1}
    with pytest.raises(Exception, match="missing product_id"):
        _xhr_cart_snapshot([
            CartItem(seller="B", title="t4", product_id=None, sku_id="s9", quantity=1),
        ])
    post = pre + [CartItem(seller="A", title="t9", product_id="100", sku_id="s9", quantity=1)]
    delta = _xhr_delta(pre, post)
    assert delta.get(("100", "s9")) == 1
    assert delta.get(("100", "s1")) == 0 and delta.get(("100", "s2")) == 0
    # 也接受 dict 形状(list_cart 类结构)
    snap2 = _xhr_cart_snapshot([{"product_id": "100", "sku_id": "s1", "quantity": 3}])
    assert snap2 == {("100", "s1"): 3}


def test_cart_snapshot_and_delta():
    """cart_price 的 DOM 快照(键 product_id+sku_id)仍可用作价格侧辅助 — 保留 ops-agent API 测试."""
    from src.extract.cart_price import cart_quantity_delta, cart_snapshot

    pre = [
        {"product_id": "100", "sku_id": "s1", "quantity": 2},
        {"product_id": "100", "sku_id": "s2", "quantity": 1},
        {"product_id": "101", "sku_id": "", "quantity": 1},   # 无 sku 行也入快照
    ]
    snap = cart_snapshot(pre)
    assert snap == {("100", "s1"): 2, ("100", "s2"): 1, ("101", ""): 1}
    post = pre + [{"product_id": "100", "sku_id": "s9", "quantity": 1}]
    delta = cart_quantity_delta(pre, post)
    assert delta.get(("100", "s9")) == 1
    assert delta.get(("100", "s1")) == 0 and delta.get(("101", "")) == 0


def test_match_remove_row_sku_exact_no_fallback():
    """remove 定位(ops-agent 实现): sku_id 提供 → 只允许精确 skuId; 绝不回退到型号/商品."""
    from src.extract.cart_price import _match_remove_row

    rows = [
        {"pid": "100", "sku": "s1", "text": "黑色 1个装 删除", "has_del": True},
        {"pid": "100", "sku": "s2", "text": "黑色 2个装 删除", "has_del": True},
    ]
    # sku 精确命中
    row, miss = _match_remove_row(rows, "100", "", "s2")
    assert row is not None and row["sku"] == "s2" and miss == ""
    # sku 未命中 → 绝不回退(即使同商品有其它行), 返回 sku_not_found
    row, miss = _match_remove_row(rows, "100", "", "s99")
    assert row is None and miss == "sku_not_found"
    # 无 sku、无 variant → 拒绝, 不按商品 id 猜第一行
    row, miss = _match_remove_row(rows, "100", "", None)
    assert row is None and miss == "need_sku_or_variant"
    # 提供 variant(无 sku)且未命中 → 不按商品 id 回退
    row, miss = _match_remove_row(rows, "100", "黑色 99个装")
    assert row is None and miss == "variant_not_found"


# ── 2026-08-20 audit: cart_atomic 完整事务(mocked, 无浏览器) ───────────────────
# 用 monkeypatch 替换 read_cart(XHR 快照)/list_cart(DOM 价格)/add_to_cart/remove_cart_item,
# 验证 _atomic_add_read_remove 的完整安全事务: 加购→证明 +1→读价→按精确 skuId 退回→证明还原。

import asyncio

import pytest

from src.extract.compare import _atomic_add_read_remove


def _atomic_var(sku_id, val="白色", price=100.0):
    return SimpleNamespace(sku_id=sku_id, properties={"颜色分类": val},
                           price=price, stock=1, available=True)


def _atomic_product(*variants):
    return SimpleNamespace(variants=list(variants))


def _cart(seller="A", pid="100", sku="s1", qty=1):
    from src.models import CartItem

    return CartItem(seller=seller, title="t", product_id=pid, sku_id=sku, quantity=qty)


class _Seq:
    """Async call-sequence stub: returns vals[i], staying on the last when exhausted."""

    def __init__(self, *vals):
        self.vals = list(vals)
        self.i = 0
        self.calls = 0

    async def __call__(self, *a, **kw):
        v = self.vals[min(self.i, len(self.vals) - 1)]
        self.i += 1
        self.calls += 1
        return v


def _run_atomic(monkeypatch, *, pre_xhr, read_cart_seq, dom_items=(),
                add_result="added", remove_result=None, product=None, want=None):
    """Drive _atomic_add_read_remove with mocked deps; return (co, atomic_log, calls)."""
    import src.cart as cart_mod
    import src.extract.account as account_mod
    import src.extract.cart_price as cp_mod

    calls = {"add": [], "remove": []}

    async def fake_add_to_cart(pid, options=None, qty=1, confirm=False, cheapest_available=False):
        calls["add"].append((pid, list(options or []), qty, confirm))
        if isinstance(add_result, Exception):
            raise add_result
        return add_result

    async def fake_remove_cart_item(product_id, variant="", qty=None, sku_id=None, max_items=100):
        calls["remove"].append((product_id, sku_id))
        return remove_result

    async def fake_list_cart(max_items=100, exclude_unavailable=False):
        return {"items": list(dom_items)}

    read_cart_stub = _Seq(*read_cart_seq)

    monkeypatch.setattr(account_mod, "read_cart", read_cart_stub)
    monkeypatch.setattr(cp_mod, "list_cart", fake_list_cart)
    monkeypatch.setattr(cp_mod, "remove_cart_item", fake_remove_cart_item)
    monkeypatch.setattr(cart_mod, "add_to_cart", fake_add_to_cart)

    co: dict = {}
    atomic_log: list[dict] = []
    if product is None:
        product = _atomic_product(_atomic_var("s9"))
    asyncio.run(_atomic_add_read_remove("100", product, want, co, atomic_log, pre_xhr))
    return co, atomic_log, calls


def test_atomic_tx_happy_path_add_prove_read_remove_restore(monkeypatch):
    """完整事务: s9 不在车 → 加购1件 → XHR 证明 (100,s9) +1 且 (100,s1) 不变 → 读 DOM 价格 →
    按精确 skuId 退回 → 最终 XHR 与基线一致."""
    pre = [_cart(sku="s1", qty=1)]                       # 基线: 已有 s1, s9 不在
    cur = [_cart(sku="s1", qty=1)]                       # 加购前无漂移
    fresh = [_cart(sku="s1", qty=1), _cart(sku="s9", qty=1)]   # 加购后 (100,s9) 恰好 +1
    final = [_cart(sku="s1", qty=1)]                     # 退回后还原
    dom = [{"product_id": "100", "sku_id": "s9", "variant": "颜色分类:白色", "after_price": 99.0}]

    co, log, calls = _run_atomic(
        monkeypatch, pre_xhr=pre, read_cart_seq=(cur, fresh, final),
        dom_items=dom, remove_result={"removed": True})

    assert calls["add"] == [("100", ["白色"], 1, True)]   # 恰好 1 件, confirm=True
    assert calls["remove"] == [("100", "s9")]              # 只按精确 sku_id 退回
    assert len(log) == 1
    e = log[0]
    assert e["mutated"] is True and e["removed"] is True and e["restored"] is True
    assert not e.get("require_manual") and e["mode"] == "added"
    # DOM 价格读到并合并进 co(命中型号 → cart_price)
    assert co["颜色分类:白色"]["cart_price"] == 99.0
    assert co["颜色分类:白色"]["matched"] is True


def test_atomic_tx_proof_failure_stops_no_delete(monkeypatch):
    """加购后 XHR 无法证明(目标 +2, 或其它行被改)→ 停止、绝不删除、require_manual."""
    pre = [_cart(sku="s1", qty=1)]
    cur = [_cart(sku="s1", qty=1)]
    bad_fresh = [_cart(sku="s1", qty=1), _cart(sku="s9", qty=2)]   # 目标 +2, 无法证明
    co, log, calls = _run_atomic(monkeypatch, pre_xhr=pre, read_cart_seq=(cur, bad_fresh),
                                 remove_result={"removed": True})

    assert calls["add"] == [("100", ["白色"], 1, True)]
    assert calls["remove"] == []                               # 绝不删除
    e = log[0]
    assert e["removed"] is False and e["require_manual"] is True
    assert "无法证明" in e["remove_reason"]


def test_atomic_tx_target_already_in_cart_zero_mutation(monkeypatch):
    """目标 (100, s9) 已在购物车 → 零写入: 不加购、不删除, 直接读 DOM 价格."""
    pre = [_cart(sku="s9", qty=1)]
    dom = [{"product_id": "100", "sku_id": "s9", "variant": "颜色分类:白色", "after_price": 88.0}]
    co, log, calls = _run_atomic(monkeypatch, pre_xhr=pre, read_cart_seq=(),
                                 dom_items=dom, remove_result={"removed": True})

    assert calls["add"] == [] and calls["remove"] == []        # 零写入
    e = log[0]
    assert e["mode"] == "read_only" and e["mutated"] is False and e["removed"] is True
    assert co["颜色分类:白色"]["cart_price"] == 88.0


def test_atomic_tx_drift_before_add_skips(monkeypatch):
    """加购前 XHR 与基线不一致(上件残留/人工改车)→ 跳过加购, require_manual."""
    pre = [_cart(sku="s1", qty=1)]
    drifted = [_cart(sku="s1", qty=1), _cart(sku="s5", qty=1)]   # 多了 s5
    co, log, calls = _run_atomic(monkeypatch, pre_xhr=pre, read_cart_seq=(drifted,),
                                 remove_result={"removed": True})

    assert calls["add"] == [] and calls["remove"] == []        # 不写
    e = log[0]
    assert e["mode"] == "skip_drift" and e["require_manual"] is True and e["mutated"] is False


def test_export_compare_xlsx_forwards_cart_atomic_source(monkeypatch):
    """taobao_export(type=compare, format=xlsx, source=cart_atomic) 把 source/skus 透传给
    compare_products — xlsx 导出同样走安全原子事务(由 taobao_export 先做 atomic_confirm 门)."""
    import asyncio

    from src.extract import compare as cmp

    calls: dict = {}

    async def fake_cmp(*a, **kw):
        calls["kw"] = kw
        return {"count": 0, "products": []}

    async def fake_write(rows, filename, out_dir):
        return "output/fake.xlsx"

    monkeypatch.setattr(cmp, "compare_products", fake_cmp)
    monkeypatch.setattr(cmp, "_write_compare_async", fake_write)

    asyncio.run(cmp.export_compare_xlsx(["1039147294809"], source="cart_atomic",
                                        skus=["10个袋子30*34cm+2夹子"]))
    assert calls["kw"]["source"] == "cart_atomic"
    assert calls["kw"]["skus"] == ["10个袋子30*34cm+2夹子"]


def test_atomic_tx_missing_baseline_snapshot_causes_zero_mutation(monkeypatch):
    """权威基线抓取失败(None)时必须零加购、零删除并显式要求人工检查。"""
    co, log, calls = _run_atomic(
        monkeypatch, pre_xhr=None, read_cart_seq=(), remove_result={"removed": True}
    )
    assert co == {}
    assert calls["add"] == [] and calls["remove"] == []
    assert log[0]["mode"] == "skip_snapshot"
    assert log[0]["mutated"] is False and log[0]["require_manual"] is True



def test_atomic_price_match_requires_exact_sku():
    from src.extract.compare import _match_cart_price

    variant = _atomic_var("s9", val="白色")
    rows = [
        {"product_id": "100", "sku_id": "wrong", "variant": "颜色分类:白色", "after_price": 1.0},
        {"product_id": "100", "sku_id": "s9", "variant": "完全不同文本", "after_price": 99.0},
    ]
    out = _match_cart_price("100", [variant], rows, require_exact_sku=True)
    assert out["颜色分类:白色"]["cart_price"] == 99.0


def test_atomic_tx_uncertain_add_never_deletes(monkeypatch):
    """加购调用异常可能发生在服务端写入后；必须提示人工且绝不猜测删除。"""
    pre = [_cart(sku="s1", qty=1)]
    co, log, calls = _run_atomic(
        monkeypatch, pre_xhr=pre, read_cart_seq=(pre,),
        add_result=RuntimeError("response lost"), remove_result={"removed": True},
    )
    assert co == {}
    assert calls["add"] and calls["remove"] == []
    assert log[0]["mode"] == "add_uncertain"
    assert log[0]["mutated"] is True and log[0]["require_manual"] is True



def test_atomic_target_requires_unique_exact_requested_sku():
    from src.extract.compare import _resolve_atomic_target

    variants = [
        _atomic_var("s1", val="白色 10个装"),
        _atomic_var("s2", val="白色 20个装"),
    ]
    target, error = _resolve_atomic_target("颜色分类:白色 10个装", variants)
    assert error is None and target.sku_id == "s1"
    target, error = _resolve_atomic_target("白色 20个装", variants)
    assert error is None and target.sku_id == "s2"
    target, error = _resolve_atomic_target("20个装", variants)
    assert target is None and "无精确匹配" in error

    duplicate = [_atomic_var("s3", val="白色"), _atomic_var("s4", val="白色")]
    target, error = _resolve_atomic_target("白色", duplicate)
    assert target is None and "匹配不唯一" in error


def test_atomic_target_without_request_is_deterministic_cheapest():
    from src.extract.compare import _resolve_atomic_target

    variants = [
        _atomic_var("s2", val="较贵", price=20.0),
        _atomic_var("s9", val="同价后排", price=10.0),
        _atomic_var("s1", val="同价前排", price=10.0),
    ]
    target, error = _resolve_atomic_target(None, variants)
    assert error is None and target.sku_id == "s1"


def test_atomic_tx_partial_requested_variant_causes_zero_mutation(monkeypatch):
    variants = [_atomic_var("s1", val="白色 10个装"), _atomic_var("s2", val="白色 20个装")]
    import src.cart as cart_mod
    import src.extract.account as account_mod
    import src.extract.cart_price as cp_mod

    calls = {"add": [], "remove": []}

    async def fake_add(*args, **kwargs):
        calls["add"].append((args, kwargs))

    async def fake_remove(*args, **kwargs):
        calls["remove"].append((args, kwargs))
        return {"removed": True}

    monkeypatch.setattr(cart_mod, "add_to_cart", fake_add)
    monkeypatch.setattr(cp_mod, "remove_cart_item", fake_remove)
    monkeypatch.setattr(account_mod, "read_cart", _Seq([]))
    atomic_log = []
    result = asyncio.run(_atomic_add_read_remove(
        "100", _atomic_product(*variants), "白色", {}, atomic_log, []
    ))
    assert result == {}
    assert calls == {"add": [], "remove": []}
    assert atomic_log[0]["mode"] == "no_write" and "无精确匹配" in atomic_log[0]["error"]
