"""Tests for the tracking-digest markdown renderer (代购转发用) — 打磨轮次 53."""

from __future__ import annotations

from src.extract.orders import _tracking_markdown
from src.models import OrderStatus


def _orders():
    return [
        OrderStatus(order_id="3316799065164009188", title="收纳箱", status="运输中",
                    carrier="申通", tracking_no="773437135802545"),
        OrderStatus(order_id="2", title="螺丝", status="待取件",
                    carrier="顺丰", tracking_no="SF123", pickup_code="123456", station="菜鸟驿站(东门)"),
    ]


def test_tracking_markdown_renders_rows():
    md = _tracking_markdown(_orders())
    assert "### 今日物流摘要(2 单)" in md
    assert "| 订单号 | 状态 | 物流 | 单号 | 取件码 | 驿站 |" in md
    assert "3316799065164009188 | 运输中 | 申通 | 773437135802545 | - | - |" in md
    assert "📦待取件 | 顺丰 | SF123 | 123456 | 菜鸟驿站(东门)" in md
    assert "3316799065164009188" in md.split("| 📦")[0]  # 无取件码订单不加📦
