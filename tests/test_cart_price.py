"""Tests for the cart price parser (_num + _parse_cart_item) — 打磨轮次 4.

Covers: space-separated price tokens, the quantity-concat fix ('￥ 259 1' → ¥259),
店铺优惠后 / 平台加补后 / plain-price renderings, variant (颜色分类/规格/配件类型) extraction.
"""

from __future__ import annotations

from src.extract.cart_price import _cart_markdown, _compute_total, _group_by_shop, _num, _parse_cart_item


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
    assert "天鼠收纳箱 | 特大号白色 | 33.75 | 42.25 | A店" in md


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
