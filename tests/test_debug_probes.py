"""2026-09-08 debug 探针扩展(config_detail/open_probe/cart_probe)的离线单测.

三探针主体需实机验证(Windows 部署, 人工在场); 此处只覆盖无需浏览器的纯守卫:
open_probe 未知来源拒答(不启会话)、商品链接生成。cart_probe 的 _to_product_id
取 id 行为由既有 test_product_id.py 覆盖; probe_cart_entry 购物车 DOM 部分实机验证。
"""

import asyncio

from src.extract.newtab import _taobao_link_href, probe_open_newtab


def test_taobao_link_href_build():
    assert _taobao_link_href("861510231125") == \
        "https://item.taobao.com/item.htm?id=861510231125"


def test_open_probe_rejects_unknown_source_offline():
    """未知来源必须在启动浏览器会话前拒答(纯本地守卫)."""
    out = asyncio.run(probe_open_newtab("861510231125", source="evil.com"))
    assert out.get("error", "").startswith("未知来源")
    assert "bare" in out["error"]
