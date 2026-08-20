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

import pytest

import src.config as config_mod
import src.extract.search as search_mod
from src.errors import CaptchaError


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


# ---- _goto_search_page 的宽泛导航回退绝不能吞掉验证码超时(audit HIGH-3) ----

def test_goto_search_propagates_captcha_from_home_nav(monkeypatch):
    """回首页导航里 guard_captcha 超时抛 CaptchaError → 上浮, 绝不吞掉后继续直接 URL。"""
    from src.errors import CaptchaError

    class FakePage:
        url = "about:blank"   # 不在淘宝域 → 进入回首页 + guard 分支

        async def goto(self, url, **kw):
            pass

    class FakeSession:
        async def guard_captcha(self, page):
            raise CaptchaError()

        def _candidate_pages(self, page=None):
            return [page]

    async def no_delay(*a, **k):
        return None

    import src.browser.session as session_mod

    monkeypatch.setattr(session_mod, "get_session", lambda: FakeSession())
    import src.browser.pacing as pacing_mod

    monkeypatch.setattr(pacing_mod, "human_delay", no_delay)

    with pytest.raises(CaptchaError):
        asyncio.run(search_mod._goto_search_page(FakePage(), "https://s.taobao.com/search?q=x", "x"))


def test_goto_search_propagates_captcha_from_search_box_submit(monkeypatch):
    """搜索框提交轮询里 guard_captcha 超时抛 CaptchaError → 上浮, 不退回直接 URL。"""
    from src.errors import CaptchaError

    class FakeLocator:
        async def count(self):
            return 1

        async def click(self, **kw):
            return None

        async def fill(self, text):
            return None

    class FakePage:
        url = "https://www.taobao.com"   # 已在淘宝域 → 跳过回首页分支, 走搜索框

        def locator(self, sel):
            return SimpleNamespace(first=FakeLocator())

        @property
        def keyboard(self):
            return SimpleNamespace(press=self._press)

        async def _press(self, key):
            return None

        async def goto(self, url, **kw):
            pass

    class FakeSession:
        async def guard_captcha(self, page):
            raise CaptchaError()

        def _candidate_pages(self, page=None):
            return [page]

    async def no_delay(*a, **k):
        return None

    import src.browser.session as session_mod

    monkeypatch.setattr(session_mod, "get_session", lambda: FakeSession())
    import src.browser.pacing as pacing_mod

    monkeypatch.setattr(pacing_mod, "human_delay", no_delay)

    with pytest.raises(CaptchaError):
        asyncio.run(search_mod._goto_search_page(FakePage(), "https://s.taobao.com/search?q=x", "x"))


# ---- parse_search: guard must re-run AFTER pagination clicks (audit MED-6 extension) ----

def _make_parse_search_page():
    """Fake page that drives parse_search down the direct-URL search-box path:
    no search box (count()=0), a pagination 下一页 click advances the URL to page=2,
    EXTRACT_JS returns []."""

    class _FakeFirst:
        def __init__(self, page):
            self._page = page

        async def count(self):
            return 0

        async def click(self, **kw):
            self._page.url = "https://s.taobao.com/search?q=x&tab=all&page=2"

    class _FakePage:
        def __init__(self):
            self.url = "https://www.taobao.com"
            self._first = _FakeFirst(self)

        def locator(self, sel):
            return SimpleNamespace(first=self._first)

        async def goto(self, url, **kw):
            pass

        async def wait_for_load_state(self, state):
            pass

        async def evaluate(self, js):
            return []

        class _Mouse:
            async def wheel(self, dx, dy):
                pass

        @property
        def mouse(self):
            return self._Mouse()

    return _FakePage()


class _ParseSearchSession:
    """Fake session: guard_captcha raises CaptchaError on the Nth call (1-indexed),
    so call #2 (after pagination) proves the post-pagination guard exists."""

    def __init__(self, page, raise_on_guard_call):
        self._page = page
        self._raise_on_guard_call = raise_on_guard_call
        self._guard_calls = 0

    async def start(self):
        return self._page

    async def guard_captcha(self, page=None):
        self._guard_calls += 1
        if self._raise_on_guard_call is not None and self._guard_calls == self._raise_on_guard_call:
            raise CaptchaError()

    async def dismiss_frequency_dialog(self, page=None):
        return True

    def _candidate_pages(self, page=None):
        return [page]


def _patch_parse_search_harness(monkeypatch, session):
    async def no_op(*a, **k):
        return None

    import src.browser.pacing as pacing_mod
    import src.browser.session as session_mod

    monkeypatch.setattr(session_mod, "get_session", lambda: session)
    monkeypatch.setattr(pacing_mod, "human_delay", no_op)
    monkeypatch.setattr(pacing_mod, "human_scroll", no_op)
    monkeypatch.setattr(search_mod, "_enforce_search_cooldown", no_op)


def test_parse_search_guard_after_pagination_propagates_captcha(monkeypatch):
    """点下一页翻页后再 guard: 若翻页触发了滑块, CaptchaError 必须上浮而不是拿墙当结果。"""
    page = _make_parse_search_page()
    # 第 1 次 guard(导航后)放行, 第 2 次 guard(翻页后, 新增)抛 CaptchaError
    _patch_parse_search_harness(monkeypatch, _ParseSearchSession(page, raise_on_guard_call=2))
    with pytest.raises(CaptchaError):
        asyncio.run(search_mod.parse_search("x", page_num=2))


def test_parse_search_guard_after_pagination_passes_normal(monkeypatch):
    """无风控时, 翻页后 guard 放行, 页面到达 page=2 并正常返回结果(不误伤正常路径)。"""
    page = _make_parse_search_page()
    session = _ParseSearchSession(page, raise_on_guard_call=None)
    _patch_parse_search_harness(monkeypatch, session)
    out = asyncio.run(search_mod.parse_search("x", page_num=2))
    assert out == []                     # EXTRACT_JS 返回 [] → 空结果, 无崩溃
    assert "page=2" in page.url          # 翻页点击确实推进了 URL
    assert session._guard_calls >= 2     # 导航后 + 翻页后 两次 guard 都跑了


def _make_landed_page2_page():
    """Fake page for page_num=3: the SPA rewrote the requested page=3 to land on page=2,
    and the pagination 下一页 click advances the URL to page=3."""

    class _FakeFirst:
        def __init__(self, page):
            self._page = page

        async def count(self):
            return 0

        async def click(self, **kw):
            self._page.url = "https://s.taobao.com/search?q=x&tab=all&page=3"

    class _FakePage:
        def __init__(self):
            self.url = "https://s.taobao.com/search?q=x&tab=all&page=2"
            self._first = _FakeFirst(self)

        def locator(self, sel):
            return SimpleNamespace(first=self._first)

        async def goto(self, url, **kw):
            pass

        async def wait_for_load_state(self, state):
            pass

        async def evaluate(self, js):
            return []

        class _Mouse:
            async def wheel(self, dx, dy):
                pass

        @property
        def mouse(self):
            return self._Mouse()

    return _FakePage()


def test_parse_search_page3_continues_past_intermediate_page(monkeypatch):
    """请求 page=3 而 SPA 落在 page=2 时, 分页循环必须 continue(已到中间页)继续点向
    page=3, 而不是 break 提前退出把第 2 页当第 3 页返回; 最终 URL 必须到 page=3。"""
    page = _make_landed_page2_page()
    session = _ParseSearchSession(page, raise_on_guard_call=None)
    _patch_parse_search_harness(monkeypatch, session)
    out = asyncio.run(search_mod.parse_search("x", page_num=3))
    assert out == []                     # EXTRACT_JS 返回 [] → 无崩溃
    assert "page=3" in page.url          # 中间页(page=2)不提前 break, 点到了 page=3
    assert session._guard_calls >= 3     # 回首页 guard + 导航后 guard + 翻页点击后 guard 都跑了
