"""list_favorites captcha/drift propagation (audit HIGH-3 gap).

list_favorites navigated without session.guard_captcha and converted render failures
into an empty list — so a slider/login wall looked like an empty 收藏夹. Now it guards
right after navigation and re-raises CaptchaError/SelectorDriftError instead of
reporting ordinary empty data on a wall. Genuine (non-wall) render failures still
degrade to the empty-with-error result.
"""

from __future__ import annotations

import asyncio

import pytest

from src.errors import CaptchaError, SelectorDriftError
from src.extract.favorite import FAV_ITEMS_JS, list_favorites


class _FakeElem:
    async def wait_for(self, **kw):
        return None


class _FakeLocator:
    @property
    def first(self):
        return _FakeElem()


class _FakePage:
    def __init__(self, evaluate_side_effect=None):
        self._evaluate_side_effect = evaluate_side_effect

    async def goto(self, url, **kw):
        pass

    def locator(self, sel):
        return _FakeLocator()

    async def evaluate(self, js):
        if self._evaluate_side_effect is not None and js == FAV_ITEMS_JS:
            raise self._evaluate_side_effect
        return []


class _FakeSession:
    def __init__(self, page, guard_exc=None):
        self.page = page
        self._guard_exc = guard_exc

    async def start(self):
        return self.page

    async def guard_captcha(self, page=None):
        if self._guard_exc is not None:
            raise self._guard_exc


def _install(monkeypatch, session):
    # list_favorites resolves get_session from src.browser.session at call time.
    import src.browser.session as session_mod

    monkeypatch.setattr(session_mod, "get_session", lambda: session)


def test_nav_guard_captcha_propagates(monkeypatch):
    """A wall right after navigation → CaptchaError propagates, NOT an empty favorites list."""
    session = _FakeSession(_FakePage(), guard_exc=CaptchaError())
    _install(monkeypatch, session)
    with pytest.raises(CaptchaError):
        asyncio.run(list_favorites())


def test_nav_guard_selector_drift_propagates(monkeypatch):
    session = _FakeSession(_FakePage(), guard_exc=SelectorDriftError(step="favorites"))
    _install(monkeypatch, session)
    with pytest.raises(SelectorDriftError):
        asyncio.run(list_favorites())


def test_render_selector_drift_propagates(monkeypatch):
    """A wall/drift appearing during card extraction → re-raised, not empty data."""
    page = _FakePage(evaluate_side_effect=SelectorDriftError(step="favorites grid"))
    _install(monkeypatch, _FakeSession(page))
    with pytest.raises(SelectorDriftError):
        asyncio.run(list_favorites())


def test_ordinary_render_failure_still_empty(monkeypatch):
    """A genuine (non-wall) render failure keeps the empty-with-error degradation."""
    page = _FakePage(evaluate_side_effect=ValueError("boom"))
    _install(monkeypatch, _FakeSession(page))
    out = asyncio.run(list_favorites())
    assert out["count"] == 0
    assert out["error"] == "boom"
