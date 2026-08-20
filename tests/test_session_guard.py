"""Captcha-guard clearance detection (2026-08-20 regression test).

Bug: guard_captcha() polled _looks_blocked() using page.query_selector(), which
matches elements even when hidden. After the human solved an image-select
captcha, Taobao leaves the baxia/slider widget hidden in the DOM (display:none /
zero-size) and the tab may stay on sec.taobao.com for a beat — so the poll never
saw "cleared" and the tool stayed stuck until CaptchaError. Fix: only a VISIBLE
widget counts as blocked; bare URL hints (except login walls) no longer block.
"""

from __future__ import annotations

import asyncio

from src.browser.session import BrowserSession, get_session


class FakeElement:
    def __init__(self, visible: bool):
        self._visible = visible

    async def is_visible(self) -> bool:
        return self._visible


class FakePage:
    """Minimal stand-in for a Playwright page used by _looks_blocked()."""

    def __init__(self, url: str, visible_sels: set[str] | None = None):
        self._url = url
        self._visible_sels = visible_sels or set()

    @property
    def url(self) -> str:
        return self._url

    # New guard walks every frame of every page — provide a frame view where the
    # main frame IS the page itself (top-document selectors run on it).
    @property
    def main_frame(self):
        return self

    @property
    def frames(self) -> list:
        return []

    def is_closed(self) -> bool:
        return False

    async def query_selector(self, sel: str):
        if sel in self._visible_sels:
            return FakeElement(True)
        # A solved-but-left-behind widget: present in DOM but hidden.
        return FakeElement(False)


def _looks_blocked(sess: BrowserSession, page: FakePage) -> bool:
    return asyncio.run(sess._looks_blocked(page))


def test_hidden_leftover_widget_is_not_blocked():
    # Solved captcha: slider widget still in DOM but hidden -> must NOT block.
    sess = get_session()
    page = FakePage("https://s.taobao.com/search?q=pctg", visible_sels=set())
    assert _looks_blocked(sess, page) is False


def test_visible_slider_is_blocked():
    sess = get_session()
    page = FakePage("https://s.taobao.com/search", visible_sels={"#nc_1_n1z"})
    assert _looks_blocked(sess, page) is True


def test_visible_baxia_dialog_is_blocked():
    sess = get_session()
    page = FakePage("https://s.taobao.com/search", visible_sels={".baxia-dialog"})
    assert _looks_blocked(sess, page) is True


def test_sec_url_alone_after_solve_is_not_blocked():
    # Tab still on sec.taobao.com after the human solved the captcha, widget gone.
    sess = get_session()
    page = FakePage("https://sec.taobao.com/x?data=abc", visible_sels=set())
    assert _looks_blocked(sess, page) is False


def test_sec_url_with_visible_widget_is_blocked():
    sess = get_session()
    page = FakePage("https://sec.taobao.com/x", visible_sels={"iframe[src*='baxia']"})
    assert _looks_blocked(sess, page) is True


def test_punish_url_without_widget_is_not_blocked():
    sess = get_session()
    page = FakePage("https://s.taobao.com/punish?redirect=1", visible_sels=set())
    assert _looks_blocked(sess, page) is False


def test_login_wall_is_always_blocked_even_without_widget():
    # The QR page IS the block on its own (authoritative).
    sess = get_session()
    page = FakePage("https://login.taobao.com/member/login.jhtml", visible_sels=set())
    assert _looks_blocked(sess, page) is True


def test_captcha_in_other_tab_is_detected():
    """验证码在新标签页也必须被检测到(2026-08-20: 搜索提交后新页同现两验证码)。"""
    from types import SimpleNamespace

    sess = get_session()
    # main page has nothing; the "new tab" holds a visible baxia slider.
    main = FakePage("https://s.taobao.com/search?q=pctg", visible_sels=set())
    newtab = FakePage("https://s.taobao.com/search", visible_sels={"#nc_1_n1z"})
    sess.context = SimpleNamespace(pages=[main, newtab])
    try:
        assert _looks_blocked(sess, main) is True
        # Once the human solves it in the new tab, the widget is gone → not blocked.
        newtab._visible_sels = set()
        assert _looks_blocked(sess, main) is False
    finally:
        sess.context = None


def test_captcha_inside_iframe_is_detected():
    """滑块/选图常在 baxia iframe 内部: 主文档 query_selector 看不见, 必须遍历 frame。"""
    class FakeFrame:
        def __init__(self, visible_sels):
            self._visible_sels = visible_sels

        async def query_selector(self, sel: str):
            return FakeElement(True) if sel in self._visible_sels else FakeElement(False)

    class FakePageWithFrame(FakePage):
        @property
        def frames(self):
            return [self._frame]

        @property
        def main_frame(self):
            return self

    sess = get_session()
    page = FakePageWithFrame("https://s.taobao.com/search", visible_sels=set())
    page._frame = FakeFrame({"iframe[src*='baxia']"})
    assert _looks_blocked(sess, page) is True
    # iframe 内验证码被人工通过后隐藏 → 不再阻塞。
    page._frame._visible_sels = set()
    assert _looks_blocked(sess, page) is False
