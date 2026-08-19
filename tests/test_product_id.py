"""Tests for _to_product_id (product id / URL → bare numeric id). Pure, no session."""

from __future__ import annotations

import pytest

from src.extract.product import _to_product_id


def test_bare_id():
    assert _to_product_id("862892097837") == "862892097837"


def test_taobao_item_url():
    assert _to_product_id("https://item.taobao.com/item.htm?id=759429259765") == "759429259765"


def test_tmall_url_with_extra_params():
    # mi_id / spm / upStreamPrice are stripped — only the numeric id is kept
    u = ("https://detail.tmall.com/item.htm?id=862892097837&mi_id=0000abc&spm=tbpc.mytb_itemcollect"
         "&upStreamPrice=2880&sku_properties=1627207%3A35751380524")
    assert _to_product_id(u) == "862892097837"


def test_numeric_substring_fallback():
    assert _to_product_id("some text with 736546459871 inside") == "736546459871"


def test_invalid_raises():
    with pytest.raises(Exception):
        _to_product_id("not-a-product")
