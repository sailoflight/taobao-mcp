"""Unit tests for src/inventory.py — dinamic parsing, landed-cost allocation, dedup, categorize."""

import json

from src.inventory import (
    _qty,
    _to_num,
    accumulate_dinamic,
    categorize,
    inventory_rows,
)


def _order(oid, day, seller, items, shipping=None, paid=None, status="交易成功"):
    o = {"createDay": day, "seller": seller, "status": status, "items": items}
    if shipping is not None:
        o["shipping"] = shipping
    if paid is not None:
        o["order_paid"] = paid
    return {oid: o}


def _item(title, price, qty=1, variant="", item_id="111", pic="//img.alicdn.com/x.jpg"):
    return {"title": title, "variant": variant, "price": price, "qty": qty,
            "itemId": item_id, "pic": pic, "itemUrl": "https://item.taobao.com/item.htm?id=" + item_id}


# ── numeric coercion ─────────────────────────────────────────────────────────
def test_to_num_and_qty_edge_cases():
    assert _to_num("￥1,371.25") == 1371.25
    assert _to_num("¥6.00") == 6.0
    assert _to_num(None) == 0.0
    assert _to_num("") == 0.0
    assert _qty({"qty": None}) == 1
    assert _qty({"qty": "3"}) == 3
    assert _qty({"qty": 0}) == 1     # never divide by zero / negative
    assert _qty({"qty": -2}) == 1


# ── landed cost ──────────────────────────────────────────────────────────────
def test_landed_single_item_order_reconciles_to_paid():
    # the real-world case that prompted the column: ¥7 product + ¥6 shipping = ¥13 实付款
    by = _order("1001", "2026-06-24", "深圳市鑫达电子",
                [_item("932SQ420DGLF 贴片", "￥7.00")], shipping="￥6.00", paid="￥13.00")
    (r,) = inventory_rows(by, since="2025-01-01")
    assert r["unit"] == 7.0
    assert r["ship"] == 6.0
    assert r["landed_unit"] == 13.0
    assert r["landed_line"] == 13.0


def test_landed_multi_item_allocation_sums_to_paid():
    # shipping is ONE order-level fee spread by quantity across all units
    by = _order("1002", "2026-06-23", "tb44759054",
                [_item("主板", "￥1.50", qty=5, item_id="a"),
                 _item("螺旋桨", "￥2.00", qty=20, item_id="b")],
                shipping="￥7.00", paid="￥54.50")
    rows = inventory_rows(by, since="2025-01-01")
    assert len(rows) == 2
    total_landed = round(sum(r["landed_line"] for r in rows), 2)
    # 1.5*5 + 2*20 = 47.50 product + 7.00 shipping = 54.50 = 实付款
    assert abs(total_landed - 54.50) <= 0.05    # allocation rounding tolerance
    for r in rows:  # per-unit share identical across the order: 7/25 = 0.28
        assert r["landed_unit"] == round(r["unit"] + 0.28, 2)


def test_free_shipping_landed_equals_product():
    by = _order("1003", "2026-06-25", "veromoda官方奥莱旗舰店",
                [_item("上衣", "￥178.03", variant="深棕;L")])   # no shipping field (包邮)
    (r,) = inventory_rows(by, since="2025-01-01")
    assert r["ship"] == 0.0
    assert r["landed_line"] == r["line_total"] == 178.03


# ── filtering / dedup / flags ────────────────────────────────────────────────
def test_since_filter_and_dedup():
    by = {}
    by.update(_order("2001", "2024-12-30", "old", [_item("旧货", "￥1.00")]))
    by.update(_order("2002", "2025-01-02", "new", [_item("新货", "￥2.00"),
                                                   _item("新货", "￥2.00")]))  # duplicate line
    rows = inventory_rows(by, since="2025-01-01")
    assert [r["order_no"] for r in rows] == ["2002"]   # 2024 order excluded, dupe collapsed
    assert len(rows) == 1


def test_custom_link_flag_and_food_kind():
    by = {}
    by.update(_order("3001", "2026-06-22", "某厂家直批",
                     [_item("1元补差价", "￥0.98", qty=2840)]))
    by.update(_order("3002", "2026-06-27", "麦当劳麦乐送(某店)",
                     [_item("商家配送", "￥6.00")]))
    rows = {r["order_no"]: r for r in inventory_rows(by, since="2025-01-01")}
    assert rows["3001"]["custom"] is True              # opaque payment-link line flagged
    assert rows["3002"]["kind"] == "food/local"        # instant delivery marked, not goods
    assert rows["3001"]["kind"] == "goods"


# ── dinamic body parsing ─────────────────────────────────────────────────────
def test_accumulate_dinamic_joins_nodes_by_order_id():
    body = json.dumps({"data": {"data": {
        "shopInfo_42": {"fields": {"orderId": "42", "createDay": "2026-06-24",
                                   "sellerName": "深圳市鑫达电子"}},
        "orderStatus_42": {"fields": {"subTitle": "卖家已发货"}},
        "orderPayment_42": {"fields": {"actualFee": {"value": "￥13.00"},
                                       "pcPostFee": {"value": "￥6.00"}}},
        "orderItemInfo_42_42": {"fields": {"item": {
            "title": "932SQ420DGLF", "skuText": "SM-8", "quantity": 1, "itemId": "874",
            "priceInfo": {"actualTotalFee": "￥7.00"}, "pic": "//img.alicdn.com/p.jpg",
            "itemUrl": "https://item.taobao.com/item.htm?id=874&mi_id=zzz"}}},
    }}})
    by = {}
    accumulate_dinamic(body, by)
    o = by["42"]
    assert o["seller"] == "深圳市鑫达电子" and o["createDay"] == "2026-06-24"
    assert o["shipping"] == "￥6.00" and o["order_paid"] == "￥13.00"
    (it,) = o["items"]
    assert it["variant"] == "SM-8" and it["price"] == "￥7.00"
    assert "mi_id" not in it["itemUrl"]                # tracking param stripped
    accumulate_dinamic(body, by)                       # replay same page → no duplicate items
    assert len(by["42"]["items"]) == 1


# ── categorization fallback ──────────────────────────────────────────────────
def test_categorize_keyword_fallback(tmp_path):
    rows = [
        {"title": "ESP32-C6 SuperMini开发板", "item_id": "1"},
        {"title": "34V 3KW 纯正弦逆变器", "item_id": "2"},
        {"title": "神秘商品XYZ", "item_id": "3"},
    ]
    categorize(rows, prior_xlsx=str(tmp_path / "missing.xlsx"))   # no prior file → keywords
    assert rows[0]["category"] == "MCU / Dev board"
    assert rows[1]["category"] == "Inverter / UPS / Battery"
    assert rows[2]["category"] == "Other"
