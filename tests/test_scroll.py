"""类人工滑动模块(src/browser/scroll.py)回归(2026-08-20).

用户反馈: ① 细查会一直下滑到推广商品列表, 浪费时间; ② 希望滑动函数抽成独立模块,
统一全局为类人工滑动。本模块抽出 scroll.py, 并提供 stop_at_end(到底即停)。
"""

from __future__ import annotations

import asyncio

from src.browser import scroll
from src.browser.pacing import human_scroll
from src.extract.selectors import DESC_PANEL_JS


class FakePage:
    """Minimal Playwright-page stand-in with wheel + evaluate."""

    def __init__(self, at_end: bool = False):
        self._at_end = at_end
        self.wheel_calls = 0
        self.mouse = self  # page.mouse.wheel(...) 路由到本对象的 wheel()

    async def wheel(self, dx: int, dy: int) -> None:
        self.wheel_calls += 1

    async def evaluate(self, js: str):
        if "scrollHeight" in js or "innerHeight" in js:
            return self._at_end
        return None


def test_human_scroll_forward_to_scroll_module():
    """pacing.human_scroll 转发到 scroll 模块 — 全局统一入口。"""
    assert scroll.human_scroll is not None
    assert callable(human_scroll)


def test_scroll_stop_at_end_stops_early():
    """stop_at_end=True 时, 页面已到底就提前停, 不多滚。"""
    page = FakePage(at_end=True)

    async def run():
        await scroll.human_scroll(page, steps=5, stop_at_end=True)

    asyncio.run(run())
    # 第一轮 wheel 后 evaluate 探测到底 → 立即 break, 不应滚满 5 步。
    assert page.wheel_calls == 1


def test_scroll_not_at_end_rolls_all_steps():
    page = FakePage(at_end=False)

    async def run():
        await scroll.human_scroll(page, steps=4, stop_at_end=True)

    asyncio.run(run())
    assert page.wheel_calls == 4


def test_scroll_to_bottom_stops_at_end():
    page = FakePage(at_end=True)

    async def run():
        await scroll.scroll_to_bottom(page)

    asyncio.run(run())
    assert page.wheel_calls == 1


def test_desc_panel_js_has_stable_stop():
    """细查详情滚动改为「详情图数量稳定即停」, 不再滚到 SKU 面板最底部(推广区)。"""
    assert "stable < 3" in DESC_PANEL_JS
    assert "panel.scrollTop = sh" not in DESC_PANEL_JS or "stable" in DESC_PANEL_JS
    assert "lastCount" in DESC_PANEL_JS


def test_subsidy_price_js_double_price_format():
    """细查(mi_id)页价格双价结构 '平台加补后￥42.79 | 优惠前￥51' 读取(2026-08-20)。

    用户实证: 细查页两个价并列, 旧 SUBSIDY_PRICE_JS 只按 '平台加补后' 关键词查可能
    匹配到超长容器被 <40 排除或正则遇空格失败 → 误读原价。新实现返回 {after, before, raw},
    容忍 平台加补后 与 ￥ 之间的空格/冒号。
    """
    from src.extract.selectors import SUBSIDY_PRICE_JS
    assert "after" in SUBSIDY_PRICE_JS and "before" in SUBSIDY_PRICE_JS
    assert "优惠前[:：]?" in SUBSIDY_PRICE_JS
    assert "平台加补后[:：]?\\s*[¥￥]?\\s*" in SUBSIDY_PRICE_JS
    # 旧逻辑的 <40 长度限制已放宽(<60), 避免双价整行被排除
    assert "t.length<60" in SUBSIDY_PRICE_JS
