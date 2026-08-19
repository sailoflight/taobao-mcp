"""Tests for the vendor-dossier markdown renderer (店铺档案导出) — 打磨轮次 77/78."""

from __future__ import annotations

from src.extract.linker import _dossier_markdown
from src.models import CartItem, OrderStatus, SellerMessage, VendorDossier


def _dossier() -> VendorDossier:
    return VendorDossier(
        seller="拓竹官方旗舰店",
        cart_items=[CartItem(seller="拓竹官方旗舰店", title="拓竹TPU送料模块", sku_id="1", quantity=1)],
        orders=[OrderStatus(order_id="3309", title="", status="运输中", carrier="申通",
                            tracking_no="T123", pickup_code=None, station=None)],
        thread=[SellerMessage(sender="拓竹官方旗舰店", text="您好", is_self=False)],
        unlinked=False,
    )


def test_dossier_markdown_sections():
    md = _dossier_markdown(_dossier())
    assert "### 拓竹官方旗舰店 店铺档案" in md
    assert "**购物车**:" in md and "| 拓竹TPU送料模块 | 1 |" in md
    assert "**订单**:" in md and "| 3309 | 运输中 | 申通 | T123 | - |" in md
    assert "**消息**:" in md and "[拓竹官方旗舰店] 您好" in md


def test_dossier_markdown_empty_and_unlinked():
    md = _dossier_markdown(VendorDossier(seller="某店", unlinked=True))
    assert "⚠️ 消息线程未能确证归属(unlinked)" in md
    assert "(无购物车/订单/消息)" in md
