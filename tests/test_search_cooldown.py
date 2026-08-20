"""搜索间强制冷却(search_cooldown_s) 回归(2026-08-20).

Bug: 每次搜索都直接 goto s.taobao.com/search?q=...(爬虫式跳转), 且 batch 里多个
搜索 op 连发 —— 上一个刚 loaded 27ms 后立即 goto 下一个, 每次都触发滑块验证码。
Fix: ① 全局跨调用冷却 anti_risk.search_cooldown_s(默认45s); ② 优先用页面顶部搜索
框输入关键词回车(拟人路径), 找不到搜索框才退回直接 URL。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import src.config as config_mod
import src.extract.search as search_mod


def _fake_config(cooldown: float):
    """返回一个 anti_risk.search_cooldown_s=cooldown 的最小配置替身。"""
    return SimpleNamespace(
        anti_risk=SimpleNamespace(search_cooldown_s=cooldown),
        output=SimpleNamespace(dir="/tmp/pytest-basetemp"),
    )


def test_cooldown_zero_disabled():
    """cooldown<=0 时不等待, 只刷新时间戳。"""
    old_last = search_mod._last_search_at
    old_load = config_mod.load_config
    try:
        config_mod.load_config = lambda: _fake_config(0.0)
        search_mod._last_search_at = 0.0
        asyncio.run(search_mod._enforce_search_cooldown())
        assert search_mod._last_search_at > 0.0
    finally:
        search_mod._last_search_at = old_last
        config_mod.load_config = old_load


def test_cooldown_waits_between_calls():
    """两次紧邻调用之间被强制拉开到配置间隔(用极小间隔避免慢测试)。"""
    old_last = search_mod._last_search_at
    old_load = config_mod.load_config
    try:
        config_mod.load_config = lambda: _fake_config(0.3)
        search_mod._last_search_at = 0.0
        asyncio.run(search_mod._enforce_search_cooldown())  # 第一次: 立即返回
        t0 = time.monotonic()
        asyncio.run(search_mod._enforce_search_cooldown())  # 第二次: 必须等待 ~0.3s
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.28, f"expected cooldown wait, got {elapsed:.3f}s"
    finally:
        search_mod._last_search_at = old_last
        config_mod.load_config = old_load
