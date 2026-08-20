"""Tests for the cart price parser (_num + _parse_cart_item) — 打磨轮次 4.

Covers: space-separated price tokens, the quantity-concat fix ('￥ 259 1' → ¥259),
店铺优惠后 / 平台加补后 / plain-price renderings, variant (颜色分类/规格/配件类型) extraction.
"""

from __future__ import annotations

from src.extract.cart_price import (
    _cart_markdown,
    _compute_total,
    _group_by_shop,
    _match_remove_row,
    _num,
    _parse_cart_item,
    _quantity_from_text,
    _remove_target_index,
    _row_matches_variant,
    cart_quantity_delta,
    cart_snapshot,
)


def test_num_decimal_point_as_token():
    assert _num("33 . 75") == "33.75"
    assert _num("15 . 9") == "15.9"
    assert _num("15 . 83") == "15.83"
    assert _num("4 . 9") == "4.9"


def test_num_drops_trailing_quantity():
    # 缺货/下架行: "￥ 259 1" 的尾随 1 是数量输入, 不是价格的一部分
    assert _num("259 1") == "259"
    assert _num("4499 1") == "4499"


def test_num_plain_integer():
    assert _num("10") == "10"
    assert _num("") == ""


def test_parse_shop_after_coupon_item():
    r = _parse_cart_item(
        "天鼠收纳箱特厚超大家用密封塑料衣服棉被玩具杂物特硬储物箱 信用卡支付 消费券超级立减8.5元"
        "退货宝7天价保88VIP退货包运费假一赔四 规格：1个装【天猫甄检 品质保障】"
        "颜色分类：特大号白色【56*41*32cm】4扣4轮-密封加强款 店铺优惠后 ￥ 33 . 75 ￥ 42 . 25 移入收藏 删除"
    )
    assert r["title"].startswith("天鼠收纳箱")
    assert r["variant"] == "1个装【天猫甄检 品质保障】"  # stops at the next variant marker
    assert r["savings"] == 8.5
    assert r["after_price"] == "33.75"
    assert r["original_price"] == "42.25"


def test_parse_platform_after_item():
    r = _parse_cart_item(
        "迷你烙铁小锡锅锡炉936/T12/JBC245化锡小烫台线材浸锡器黄铜焊勺 淘金币抵0.15元88VIP退货包运费"
        "颜色分类：单黄铜锡锅 1个 平台加补后 ￥ 15 . 83 距加入降 ￥ 0 . 15 ￥ 15 . 98 移入收藏 删除"
    )
    assert r["platform_after"] == "15.83"
    assert r["after_price"] is None
    assert r["original_price"] == "15.98"


def test_parse_plain_price_item():
    r = _parse_cart_item(
        "烫钻笔烫钻器专用烫头通用M4螺纹接口适用烫钻笔电烙铁 88VIP退货包运费7天无理由退货"
        "颜色分类：7个烫钻头 ￥ 10 移入收藏 删除"
    )
    assert r["after_price"] == "10"
    assert r["original_price"] == "10"


def test_parse_out_of_stock_item_no_qty_concat():
    r = _parse_cart_item(
        "款式缺货 拓竹TPU送料助力模块【X2D/P2S/H2系列/P1/X1】3D打印机配件 7天无理由退换假一赔四"
        "重新选择规格 ￥ 259 1 移入收藏 删除"
    )
    assert r["after_price"] == "259"
    assert r["original_price"] == "259"


def test_parse_removed_item_no_qty_concat():
    r = _parse_cart_item("商品下架 特斯拉v100 16g 32g显卡改装一比一复刻影音娱乐 ￥ 4499 1 移入收藏 删除")
    assert r["after_price"] == "4499"


def test_compute_total_sums_after_prices():
    items = [
        {"title": "天鼠收纳箱", "after_price": "33.75", "platform_after": None},
        {"title": "烙铁头", "after_price": "7.76", "platform_after": None},
    ]
    total, exc = _compute_total(items)
    assert total == 41.51
    assert exc == 0


def test_compute_total_uses_platform_after_fallback():
    items = [{"title": "锡锅", "after_price": None, "platform_after": "15.83"}]
    total, exc = _compute_total(items)
    assert total == 15.83


def test_compute_total_excludes_oos_removed():
    items = [
        {"title": "天鼠收纳箱", "after_price": "33.75", "platform_after": None},
        {"title": "款式缺货 拓竹TPU", "after_price": "259", "platform_after": None},
        {"title": "商品下架 特斯拉", "after_price": "4499", "platform_after": None},
    ]
    total, exc = _compute_total(items)
    assert total == 33.75
    assert exc == 2


def test_compute_total_empty():
    assert _compute_total([]) == (0, 0)


def test_parse_cart_item_garble_no_crash():
    r = _parse_cart_item("### 完全不可解析的乱码行 ###")
    assert r["title"] == "### 完全不可解析的乱码行 ###"
    assert r["variant"] == ""
    assert r["after_price"] is None
    assert r["original_price"] is None
    assert r["savings"] is None


def test_parse_cart_item_bad_price_no_crash():
    # 无法解析的价格 → 优雅降级为 None, 不抛异常
    r = _parse_cart_item("某商品 ￥ 不是数字 移入收藏 删除")
    assert not r["after_price"]  # 空串/None 均可 — 优雅降级不崩溃
    assert r["title"] == "某商品"


def test_group_by_shop_groups_and_sums():
    items = [
        {"title": "天鼠收纳箱", "after_price": "33.75", "platform_after": None, "shop": "天鼠家居旗舰店"},
        {"title": "烙铁头", "after_price": "7.76", "platform_after": None, "shop": "容邦电子"},
        {"title": "锡锅", "after_price": None, "platform_after": "15.83", "shop": "一方电子"},
    ]
    out = _group_by_shop(items)
    assert len(out) == 3
    assert out[0]["shop"] == "天鼠家居旗舰店" and out[0]["total"] == 33.75  # 降序
    assert out[1]["total"] == 15.83  # platform_after 兜底
    assert out[2]["total"] == 7.76


def test_group_by_shop_excludes_oos_and_sorts():
    items = [
        {"title": "天鼠收纳箱", "after_price": "33.75", "platform_after": None, "shop": "A店"},
        {"title": "款式缺货 拓竹", "after_price": "259", "platform_after": None, "shop": "A店"},
        {"title": "商品下架 特斯拉", "after_price": "4499", "platform_after": None, "shop": "B店"},
        {"title": "大件", "after_price": "100", "platform_after": None, "shop": "B店"},
    ]
    out = _group_by_shop(items)
    assert out[0]["shop"] == "B店" and out[0]["total"] == 100.0 and out[0]["excluded"] == 1
    assert out[1]["shop"] == "A店" and out[1]["total"] == 33.75 and out[1]["excluded"] == 1


def test_group_by_shop_empty():
    assert _group_by_shop([]) == []


def test_cart_markdown_renders_tables():
    data = {
        "count": 3,
        "total_est_note": "合计(到手价,排除1件缺货/下架,未含运费)",
        "by_shop": [{"shop": "A店", "items": 2, "total": 33.75, "excluded": 1}],
        "items": [
            {"title": "天鼠收纳箱", "variant": "特大号白色", "after_price": "33.75",
             "original_price": "42.25", "shop": "A店"},
        ],
    }
    md = _cart_markdown(data)
    assert "### 购物车(3 件)" in md
    assert "A店 | 2 | 33.75 | 1" in md
    assert "天鼠收纳箱 | 特大号白色 | 33.75 | - | 42.25 | A店" in md  # 单价列(无"个装"→-)


def test_cart_markdown_empty_items():
    md = _cart_markdown({"count": 0, "total_est_note": "n", "by_shop": [], "items": []})
    assert "### 购物车(0 件)" in md


def test_parse_unlabeled_two_prices():
    # 无"店铺优惠后/平台加补后"标签时的双价格: 第一个为到手价, 第二个为标价
    r = _parse_cart_item("某商品 ￥ 15 . 9 ￥ 18 . 9 移入收藏 删除")
    assert r["after_price"] == "15.9"
    assert r["original_price"] == "18.9"


def test_exclude_unavailable_filter():
    items = [
        {"title": "天鼠收纳箱", "after_price": "33.75", "platform_after": None, "shop": "A店"},
        {"title": "款式缺货 拓竹", "after_price": "259", "platform_after": None, "shop": "B店"},
        {"title": "商品下架 特斯拉", "after_price": "4499", "platform_after": None, "shop": "C店"},
    ]
    kept = [it for it in items if "缺货" not in it["title"] and "下架" not in it["title"]]
    assert len(kept) == 1 and kept[0]["title"] == "天鼠收纳箱"


def test_cart_unit_price():
    from src.extract.cart_price import _cart_unit_price
    assert _cart_unit_price({"variant": "1个装", "after_price": "33.75"}) == 33.75
    assert _cart_unit_price({"variant": "2个装", "after_price": "67.25"}) == 33.625
    assert _cart_unit_price({"variant": "单个", "after_price": "33.75"}) is None
    assert _cart_unit_price({"variant": "1个装", "after_price": "abc"}) is None


def test_cart_markdown_unit_column():
    data = {
        "count": 1, "total_est_note": "n",
        "by_shop": [{"shop": "天鼠", "items": 1, "total": 33.75, "excluded": 0}],
        "items": [{"title": "天鼠收纳箱", "variant": "特大号白色1个装", "after_price": "33.75",
                   "original_price": "42.25", "shop": "天鼠"}],
    }
    md = _cart_markdown(data)
    assert "| 商品 | 型号 | 到手¥ | 单价¥ | 标价¥ | 店铺 |" in md
    assert "33.75 | 33.75" in md


def test_cart_markdown_with_tag_column():
    from src.extract.cart_price import _cart_markdown
    data = {"count": 1, "total_est_note": "x", "by_shop": [],
            "items": [{"title": "收纳箱", "variant": "特大号白色", "after_price": "33.75",
                       "original_price": "42.25", "shop": "s"}]}
    md = _cart_markdown(data, with_tag=True)
    assert "| 商品 | 型号 | 到手¥ | 单价¥ | 标价¥ | 店铺 | 海运/空运 |" in md
    assert "42.25 | s |  |" in md
    assert "海运/空运" not in _cart_markdown(data)


# ── quantity parsing + qty-weighted totals/shop subtotals ─────────────────────
def test_quantity_from_text():
    assert _quantity_from_text("某商品 ￥ 259 3 移入收藏 删除") == 3   # explicit stepper value
    assert _quantity_from_text("某商品 ￥ 259 1 移入收藏 删除") == 1
    assert _quantity_from_text("某商品 ￥ 33 . 75 ￥ 42 . 25 移入收藏 删除") == 1  # no stepper → 1
    assert _quantity_from_text("某商品 ￥ 42 . 25 2 移入收藏 删除") == 2   # qty after last price
    assert _quantity_from_text("") == 1
    assert _quantity_from_text("某商品 ￥ 0 移入收藏 删除") == 1   # 0 is not a positive qty
    assert _quantity_from_text(None) == 1


def test_parse_cart_item_exposes_quantity():
    r = _parse_cart_item("某商品 颜色分类：红色 ￥ 259 3 移入收藏 删除")
    assert r["quantity"] == 3
    assert _parse_cart_item("某商品 ￥ 10 移入收藏 删除")["quantity"] == 1


def test_compute_total_multiplies_by_quantity():
    items = [
        {"title": "收纳箱", "after_price": "33.75", "platform_after": None, "quantity": 3},
        {"title": "烙铁头", "after_price": "7.76", "platform_after": None, "quantity": 2},
        {"title": "锡锅", "after_price": None, "platform_after": "15.83", "quantity": 1},
    ]
    total, exc = _compute_total(items)
    assert total == round(33.75 * 3 + 7.76 * 2 + 15.83 * 1, 2)  # 33.75*3 + 7.76*2 + 15.83


def test_compute_total_defaults_to_qty_one():
    items = [{"title": "收纳箱", "after_price": "33.75", "platform_after": None}]  # no quantity key
    assert _compute_total(items) == (33.75, 0)


def test_group_by_shop_multiplies_by_quantity():
    items = [
        {"title": "A", "after_price": "10", "platform_after": None, "shop": "店1", "quantity": 5},
        {"title": "B", "after_price": "20", "platform_after": None, "shop": "店2", "quantity": 3},
        {"title": "款式缺货 X", "after_price": "999", "platform_after": None, "shop": "店1", "quantity": 2},
    ]
    out = {g["shop"]: g for g in _group_by_shop(items)}
    assert out["店1"]["total"] == 50.0      # 10×5, 缺货行不计入
    assert out["店1"]["items"] == 2         # 行数不变(件数语义保持为行数)
    assert out["店1"]["excluded"] == 1
    assert out["店2"]["total"] == 60.0      # 20×3


# ── fail-closed removal matching (cart_atomic safe-restoration primitive) ─────
def _row(pid, sku, variant_text, has_del=True):
    return {"pid": str(pid), "sku": sku, "text": f"某商品 颜色分类：{variant_text} ￥ 10 移入收藏 删除",
            "has_del": has_del}


def test_match_remove_row_sku_exact_only_no_fallback():
    rows = [_row("1", "999", "红色"), _row("1", "888", "红色（10条）")]
    # exact skuId match wins
    got, reason = _match_remove_row(rows, "1", variant="", sku_id="888")
    assert got is not None and got["sku"] == "888" and reason == ""
    # skuId that exists for the pid but wrong → NOT_FOUND, never falls back to variant/product
    got, reason = _match_remove_row(rows, "1", variant="红色", sku_id="777")
    assert got is None and reason == "sku_not_found"
    # skuId never matches a different pid either
    got, reason = _match_remove_row(rows, "2", variant="", sku_id="999")
    assert got is None and reason == "sku_not_found"


def test_match_remove_row_variant_exact_only_no_product_fallback():
    rows = [_row("1", "999", "红色（10条）"), _row("1", "888", "黄色")]
    # exact normalized variant match
    got, reason = _match_remove_row(rows, "1", variant="红色（10条）", sku_id=None)
    assert got is not None and got["sku"] == "999" and reason == ""
    # substring is NOT a match: "红色" must not hit "红色（10条）"
    got, reason = _match_remove_row(rows, "1", variant="红色", sku_id=None)
    assert got is None and reason == "variant_not_found"
    # product-only fallback is GONE: a row exists for pid but variant doesn't match → refuse
    got, reason = _match_remove_row(rows, "1", variant="蓝色", sku_id=None)
    assert got is None and reason == "variant_not_found"
    # different pid → variant_not_found (no cross-pid guess)
    got, reason = _match_remove_row(rows, "9", variant="红色（10条）", sku_id=None)
    assert got is None and reason == "variant_not_found"


def test_match_remove_row_needs_explicit_key():
    rows = [_row("1", "999", "红色")]
    # neither sku nor variant → refuse even though the product has deletable rows
    got, reason = _match_remove_row(rows, "1", variant="", sku_id=None)
    assert got is None and reason == "need_sku_or_variant"
    # rows without a 删除 action are never candidates
    rows_nodel = [_row("1", "999", "红色", has_del=False)]
    got, reason = _match_remove_row(rows_nodel, "1", variant="红色", sku_id=None)
    assert got is None and reason == "variant_not_found"


# ── snapshot proof (+1 landed, restoration) for cart_atomic ──────────────────
def test_cart_snapshot_and_delta_prove_add_and_restore():
    pre = [{"product_id": "1", "sku_id": "a", "quantity": 2},
           {"product_id": "1", "sku_id": "b", "quantity": 1}]
    post = pre + [{"product_id": "1", "sku_id": "c", "quantity": 1}]   # atomic add of sku c
    delta = cart_quantity_delta(pre, post)
    assert delta[("1", "c")] == 1        # exactly +1 on the added sku — proves it landed
    assert delta[("1", "a")] == 0 and delta[("1", "b")] == 0
    # removal restores: post → pre ⇒ every delta returns to 0
    restored = cart_quantity_delta(post, pre)
    assert restored[("1", "c")] == -1
    # lines without sku_id key as ("<pid>", "")
    d = cart_quantity_delta([{"product_id": "7", "sku_id": None, "quantity": 1}], [])
    assert d == {("7", ""): -1}


# ── exact row targeting: never title-prefix first-match (two same-title rows) ──
def _remove_row(idx, sku, variant_text):
    """One CART_REMOVE_JS-style row (same product title across both rows)."""
    return {
        "idx": idx, "pid": "123", "sku": sku, "has_del": True,
        "href": f"https://item.taobao.com/item.htm?id=123&skuId={sku}",
        "text": f"天鼠收纳箱 颜色分类：{variant_text} ￥ 33 . 75 ￥ 42 . 25 移入收藏 删除",
    }


def test_remove_target_index_two_rows_same_title_different_sku():
    """Two rows share the SAME product-title prefix but differ by SKU/variant — the exact
    sku/variant row must be targeted, never the first title-prefix row (audit fix)."""
    rows = [_remove_row(0, "999", "红色"), _remove_row(1, "888", "红色（10条）")]
    # sku path → exact sku row (index 1), NOT the first row
    idx, miss = _remove_target_index(rows, "123", sku_id="888")
    assert idx == 1 and miss == ""
    idx, miss = _remove_target_index(rows, "123", sku_id="999")
    assert idx == 0 and miss == ""
    # variant path → exact normalized-variant row (index 1)
    idx, miss = _remove_target_index(rows, "123", variant="红色（10条）")
    assert idx == 1 and miss == ""
    # fail closed: wrong sku / no explicit key
    idx, miss = _remove_target_index(rows, "123", sku_id="777")
    assert idx is None and miss == "sku_not_found"
    idx, miss = _remove_target_index(rows, "123")
    assert idx is None and miss == "need_sku_or_variant"
    # a partial variant must NOT hit the longer sibling (exact normalized only)
    idx, miss = _remove_target_index(rows, "123", variant="红色")
    assert idx == 0 and miss == ""   # exact match to row 0 (红色), not 红色（10条）


def test_remove_target_index_missing_idx_fails_closed():
    rows = [{"pid": "1", "sku": "5", "text": "x 颜色分类：红 ￥ 1 移入收藏 删除", "has_del": True}]  # no idx
    idx, miss = _remove_target_index(rows, "1", sku_id="5")
    assert idx is None and miss == "no_row_index"


def test_row_matches_variant():
    t = "某商品 颜色分类：红色 ￥ 10 移入收藏 删除"
    assert _row_matches_variant(t, "红色") is True
    assert _row_matches_variant(t, "红色（10条）") is False     # normalized-exact, not substring
    assert _row_matches_variant("", "红色") is False
    assert _row_matches_variant(t, "") is False




# ── remove_cart_item: captcha guard + propagation (never a generic removal error) ──
def _fake_page_for_remove():
    """Minimal Playwright-ish fake: enough of page/locator for the remove click flow."""
    class _El:
        async def scroll_into_view_if_needed(self, *a, **k): return None
        async def click(self, *a, **k): return None
        def locator(self, sel): return _Loc()
        def filter(self, **k): return _Loc()

    class _Loc:
        @property
        def first(self): return self
        @property
        def last(self): return self
        async def count(self): return 0
        async def click(self, *a, **k): return None
        def nth(self, idx): return _El()
        def filter(self, **k): return self
        def locator(self, sel): return self

    class _Page:
        async def goto(self, *a, **k): return None
        async def wait_for_timeout(self, *a, **k): return None
        async def evaluate(self, js, *args):
            # Dispatch by EXPRESSION, never by a shared catch-all: each JS must get the
            # type remove_cart_item expects. CART_REMOVE_JS is a rows LIST (called first),
            # _FIND_SKU_ROW_JS / _ROW_AT_INDEX_JS are exact-find/recheck DICTs (later).
            s = str(js)
            if "wantSku" in s:                       # _FIND_SKU_ROW_JS → exact pid+sku dict
                return {"idx": 0, "pid": "123", "sku": "5",
                        "href": "https://x?id=123&skuId=5"}
            if "Number(idx)" in s:                   # _ROW_AT_INDEX_JS → row text for variant recheck
                return {"text": "某商品 颜色分类：红 ￥ 1 移入收藏 删除"}
            if "has_del" in s:                       # CART_REMOVE_JS → rows LIST
                return [{"idx": 0, "pid": "123", "sku": "5",
                         "text": "某商品 颜色分类：红 ￥ 1 移入收藏 删除", "has_del": True}]
            return None                              # scrollTo etc.
        def locator(self, sel): return _Loc()

    return _Page()


def test_remove_cart_item_propagates_captcha_from_navigation(monkeypatch):
    """A slider right after cart navigation must PROPAGATE CaptchaError, not return
    {"removed": False, "reason": "error"}."""
    import asyncio

    from src.errors import CaptchaError
    import src.browser.session as S
    import src.browser.pacing as P
    from src.extract import cart_price as CP

    class _Session:
        def __init__(self):
            self.n = 0
        async def start(self):
            return _fake_page_for_remove()
        async def guard_captcha(self, page=None):
            self.n += 1
            if self.n == 1:
                raise CaptchaError("slider on cart page")

    async def _noop(*a, **k): return None
    monkeypatch.setattr(S, "get_session", lambda: _Session())
    monkeypatch.setattr(P, "human_delay", _noop)
    try:
        asyncio.run(CP.remove_cart_item("123", sku_id="5"))
        assert False, "expected CaptchaError to propagate"
    except CaptchaError:
        pass


def test_remove_cart_item_reraises_captcha_after_delete_confirm(monkeypatch):
    """A slider AFTER the delete/confirm action must re-raise CaptchaError (the broad
    except must not swallow it into a generic removal error)."""
    import asyncio

    from src.errors import CaptchaError
    import src.browser.session as S
    import src.browser.pacing as P
    from src.extract import cart_price as CP

    class _Session:
        def __init__(self):
            self.n = 0
        async def start(self):
            return _fake_page_for_remove()
        async def guard_captcha(self, page=None):
            self.n += 1
            if self.n == 2:   # navigation guard (1) passed; post-delete guard (2) trips
                raise CaptchaError("slider after delete")

    async def _noop(*a, **k): return None
    monkeypatch.setattr(S, "get_session", lambda: _Session())
    monkeypatch.setattr(P, "human_delay", _noop)
    try:
        asyncio.run(CP.remove_cart_item("123", sku_id="5"))
        assert False, "expected CaptchaError to propagate after delete/confirm"
    except CaptchaError:
        pass


def test_remove_cart_item_guard_calls_and_reraises_present():
    """Structural guard: remove_cart_item guards captcha after navigation AND after
    delete/confirm, and its broad except re-raises CaptchaError/SelectorDriftError."""
    import inspect

    from src.extract import cart_price as CP

    src = inspect.getsource(CP.remove_cart_item)
    assert src.count("guard_captcha(page)") >= 2        # nav + post-delete
    assert "except CaptchaError:" in src and "except SelectorDriftError:" in src
    # the re-raise must be a bare raise, not a swallowed return
    i = src.find("except CaptchaError:")
    j = src.find("except SelectorDriftError:", i)
    k = src.find("except Exception", j)
    assert "raise" in src[i:j] and "raise" in src[j:k]


def test_remove_cart_item_reraises_captcha_variant_path(monkeypatch):
    """The VARIANT-keyed removal path also reaches the post-delete captcha guard — the
    fake's _ROW_AT_INDEX_JS recheck must return the row text (not None), so the flow
    proceeds past the exact row re-check to the delete → post-delete guard."""
    import asyncio

    from src.errors import CaptchaError
    import src.browser.session as S
    import src.browser.pacing as P
    from src.extract import cart_price as CP

    class _Session:
        def __init__(self):
            self.n = 0
        async def start(self):
            return _fake_page_for_remove()
        async def guard_captcha(self, page=None):
            self.n += 1
            if self.n == 2:   # navigation guard (1) passed; post-delete guard (2) trips
                raise CaptchaError("slider after delete")

    async def _noop(*a, **k): return None
    monkeypatch.setattr(S, "get_session", lambda: _Session())
    monkeypatch.setattr(P, "human_delay", _noop)
    try:
        asyncio.run(CP.remove_cart_item("123", variant="红"))   # variant path, no sku_id
        assert False, "expected CaptchaError to propagate after delete/confirm (variant path)"
    except CaptchaError:
        pass


def test_list_cart_guards_captcha(monkeypatch):
    """list_cart must hand a slider/punish wall on the cart page to the human (guard_captcha
    called after navigation) — red before the guard existed, green after."""
    import asyncio

    import src.browser.session as S
    from src.extract import cart_price as CP

    class _Page:
        async def goto(self, *a, **k): return None
        async def wait_for_timeout(self, *a, **k): return None
        async def evaluate(self, js, *args):
            s = str(js)
            if "cartItemInfo" in s:
                return []      # no cart rows
            return None        # scrollTo etc.
        async def bring_to_front(self): return None

    class _Session:
        def __init__(self):
            self.guards = 0
        async def start(self):
            return _Page()
        async def guard_captcha(self, page=None):
            self.guards += 1

    sess = _Session()
    monkeypatch.setattr(S, "get_session", lambda: sess)
    data = asyncio.run(CP.list_cart())
    assert sess.guards >= 1          # navigation captcha guard was invoked
    assert data["count"] == 0
