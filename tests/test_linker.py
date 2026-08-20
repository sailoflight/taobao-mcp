"""Vendor-join tests — pure matcher + join logic, synthetic data only (no browser, no PII)."""

from __future__ import annotations

from src.extract.linker import (
    build_dossiers,
    merge_orders,
    normalize_seller,
    order_picture,
    render_dossier,
    same_vendor,
)
from src.models import CartItem, Conversation, OrderStatus, SellerMessage


def test_normalize_strips_suffix_and_punct():
    assert normalize_seller("好管家旗舰店") == normalize_seller("好管家")
    assert normalize_seller("DUMIK 中科德马克旗舰店").startswith("dumik")
    assert normalize_seller("瑞兴科技-服务器 ") == normalize_seller("瑞兴科技服务器")


def test_same_vendor_match_and_alias():
    assert same_vendor("好管家旗舰店", "好管家")          # suffix-stripped exact
    assert same_vendor("南京海雀显卡", "南京海雀显卡专营店")  # 专营店 suffix-stripped → exact
    assert not same_vendor("好管家", "双枪")               # different
    # alias map resolves an IM nick to its shop name
    assert same_vendor("客服小七", "好管家旗舰店", aliases={"客服小七": "好管家"})


def test_same_vendor_no_containment_false_positive():
    """Containment is GONE: two different shops sharing a prefix/substring are NOT the same
    vendor even though one normalized name is a substring of the other (audit fix)."""
    assert not same_vendor("小米官方店", "小米之家")          # 小米官方店→小米 vs 小米之家
    assert not same_vendor("乐天数码", "乐天旗舰店")          # 乐天数码 vs 乐天
    assert not same_vendor("华强北数码配件", "数码之家")
    # an explicit alias still bridges genuinely-the-same vendors
    assert same_vendor("小米官方店", "小米之家", aliases={"小米官方店": "小米之家"})
    assert not same_vendor("小米官方店", "小米之家", aliases={"乐天数码": "小米之家"})  # unrelated alias ignored


def test_build_dossiers_no_cross_vendor_contamination():
    """Two vendors with a shared prefix must NOT merge their cart/orders/threads (audit fix)."""
    cart = [CartItem(seller="小米官方店", title="MIX", quantity=1)]
    purchases = [{"order_id": "O1", "seller": "小米之家", "title": "扫地机", "status": "运输中"}]
    tracking = [OrderStatus(order_id="O1", title="", status="运输中", carrier="顺丰", tracking_no="SF1")]
    convos = [Conversation(seller="小米之家", last_message="你好"),
              Conversation(seller="小米官方店", last_message="官方客服在线")]
    dossiers = build_dossiers(cart, purchases, tracking, convos)
    by_seller = {d.seller: d for d in dossiers}

    xm = by_seller.get("小米官方店")
    assert xm is not None
    assert len(xm.cart_items) == 1            # only its own cart line
    assert xm.orders == []                    # NOT the 小米之家 order
    assert len(xm.thread) == 1 and xm.thread[0].text == "官方客服在线"  # only its own thread

    home = by_seller.get("小米之家")
    assert home is not None
    assert home.cart_items == []              # NOT the 小米官方店 cart line
    assert len(home.orders) == 1 and home.orders[0].tracking_no == "SF1"
    assert len(home.thread) == 1 and home.thread[0].text == "你好"

    # no dangling unlinked duplicates beyond the two real vendors
    assert len(dossiers) == 2


def test_merge_orders_joins_tracking_by_order_id():
    purchases = [{"order_id": "A1", "seller": "测试店", "title": "P100", "status": "运输中"}]
    tracking = [OrderStatus(order_id="A1", title="", status="运输中", carrier="中通",
                            tracking_no="ZT123", pickup_code="8-2-1", station="菜鸟驿站")]
    merged = merge_orders(purchases, tracking)
    assert len(merged) == 1
    o = merged[0]
    assert o.seller == "测试店" and o.title == "P100" and o.carrier == "中通"
    assert o.pickup_code == "8-2-1"


def test_build_dossiers_groups_by_vendor_and_flags_unlinked():
    cart = [CartItem(seller="好管家旗舰店", title="乌檀木菜板", sku_id="s1", quantity=1)]
    purchases = [{"order_id": "O9", "seller": "好管家", "title": "砧板", "status": "交易成功"}]
    tracking = [OrderStatus(order_id="O9", title="", status="交易成功", carrier="顺丰", tracking_no="SF9")]
    convos = [
        Conversation(seller="好管家", messages=[SellerMessage(sender="s", text="已发货", is_self=False)]),
        Conversation(seller="某陌生卖家", messages=[SellerMessage(sender="s", text="在吗", is_self=False)]),
    ]
    dossiers = build_dossiers(cart, purchases, tracking, convos)

    haoguanjia = next(d for d in dossiers if "好管家" in d.seller)
    assert len(haoguanjia.cart_items) == 1            # cart line joined
    assert len(haoguanjia.orders) == 1 and haoguanjia.orders[0].carrier == "顺丰"  # order+tracking joined
    assert haoguanjia.thread and haoguanjia.thread[0].text == "已发货"             # thread joined
    assert haoguanjia.unlinked is False

    stranger = next(d for d in dossiers if d.seller == "某陌生卖家")
    assert stranger.unlinked is True                  # no vendor → surfaced, not guessed
    assert stranger.thread and not stranger.cart_items and not stranger.orders


def test_order_picture():
    purchases = [{"order_id": "X7", "seller": "瑞兴科技服务器", "title": "服务器", "status": "运输中"}]
    tracking = [OrderStatus(order_id="X7", title="", status="运输中", carrier="申通", tracking_no="ST7")]

    # exact nick == shop name → thread linked
    convos = [Conversation(seller="瑞兴科技服务器", messages=[SellerMessage(sender="s", text="在", is_self=False)])]
    d = order_picture("X7", purchases, tracking, convos)
    assert d is not None and d.orders[0].tracking_no == "ST7"
    assert d.thread and d.thread[0].text == "在"

    # a substring-only nick (瑞兴科技 ⊂ 瑞兴科技服务器) is NOT linked — containment removed
    convos2 = [Conversation(seller="瑞兴科技", messages=[SellerMessage(sender="s", text="在", is_self=False)])]
    d2 = order_picture("X7", purchases, tracking, convos2)
    assert d2 is not None and d2.thread == []

    # an explicit alias still links the same vendor
    d3 = order_picture("X7", purchases, tracking, convos2, aliases={"瑞兴科技": "瑞兴科技服务器"})
    assert d3.thread and d3.thread[0].text == "在"

    assert order_picture("NOPE", purchases, tracking, convos) is None


def test_render_dossier_is_compact_markdown():
    from src.models import VendorDossier
    d = VendorDossier(
        seller="好管家",
        cart_items=[CartItem(seller="好管家", title="乌檀木菜板", quantity=2)],
        orders=[OrderStatus(order_id="O1", title="砧板", status="待取件", carrier="中通",
                            tracking_no="ZT1", pickup_code="9-1-2", station="驿站", seller="好管家")],
        thread=[SellerMessage(sender="s", text="已发货请查收", is_self=False)],
    )
    md = render_dossier(d)
    assert "好管家" in md and "乌檀木菜板" in md and "取件码 9-1-2" in md and "已发货" in md


def test_cart_item_product_id_round_trip():
    """CartItem.product_id(可选, 默认 None)贯通 vendor join — 供 cart_atomic (pid, sku) 快照."""
    c = CartItem(seller="好管家", title="乌檀木菜板", product_id="1001", sku_id="s1", quantity=2)
    assert c.product_id == "1001" and c.sku_id == "s1" and c.quantity == 2
    # 默认 None, 向后兼容
    assert CartItem(seller="好管家", title="乌檀木菜板").product_id is None
    # 经 build_dossiers 贯通到档案
    dossiers = build_dossiers([c], [], [], [])
    assert len(dossiers) == 1
    assert dossiers[0].cart_items[0].product_id == "1001"
