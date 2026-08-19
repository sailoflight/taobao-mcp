"""Tests for the favorites markdown renderer (买家回顾收藏常用) — 打磨轮次 15."""

from __future__ import annotations

from src.extract.favorite import _favorites_markdown


def test_favorites_markdown_renders_rows():
    data = {"count": 2, "favorites": [
        {"price": "1359.15", "title": "拓竹A1 3D打印机", "fav_count": "1万+人收藏"},
        {"price": "23.4", "title": "激光护目镜", "fav_count": "29人收藏"},
    ]}
    md = _favorites_markdown(data)
    assert "### 收藏夹(2 个)" in md
    assert "| 价格¥ | 收藏人数 | 商品 |" in md
    assert "1359.15 | 1万+人收藏 | 拓竹A1" in md
    assert "23.4 | 29人收藏 | 激光护目镜" in md


def test_favorites_markdown_empty():
    md = _favorites_markdown({"count": 0, "favorites": []})
    assert "### 收藏夹(0 个)" in md
    assert "(空)" in md
