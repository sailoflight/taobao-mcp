"""Fine-mode (desc.py fetch_detail) captcha/drift propagation (audit HIGH-3 extension).

The review/QA/recommend broad catches around fetch_detail's on-page extraction must
re-raise CaptchaError (and SelectorDriftError for the REQUIRED review/QA extraction)
instead of embedding an error dict or continuing. Optional recommendation extraction
still degrades on ordinary errors.

The harness drives the real fetch_detail() through the footmark path with a fake
session/page, so the actual except-clauses in desc.py are exercised (not a re-test
of the underlying parsers).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.errors import CaptchaError, SelectorDriftError
from src.extract.desc import fetch_detail
from src.extract.selectors import DESC_PANEL_JS, PRICE_LINES_JS, SUBSIDY_PRICE_JS

PAGE_URL = "https://item.taobao.com/item.htm?id=12345678901&mi_id=abc"


class _FakePage:
    url = PAGE_URL

    def locator(self, sel):
        return SimpleNamespace()

    async def evaluate(self, js):
        if js == DESC_PANEL_JS:
            return {"scope": "x", "panelFound": False, "imgs": [], "imgsAnyWidth": []}
        if js == SUBSIDY_PRICE_JS:
            return {"after": "39", "before": "42", "raw": "平台加补后39"}
        if js == PRICE_LINES_JS:
            return {"priceLines": [], "hasKeywords": []}
        return None


class _FakeScope:
    """Minimal temporary_pages() scope double: track() registers pages, exit closes
    the still-open ones — mirroring the shared-library contract the facade delegates
    to (ADAPTATION_GUIDE §6)."""

    def __init__(self):
        self.tracked: list = []
        self.closed: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        for p in self.tracked:
            if not p.is_closed():
                try:
                    await p.close()
                    self.closed.append(p)
                except Exception:
                    pass
        return False

    def try_track(self, page):
        """Mirror of the library's try_track (dev2): None/closed pages are
        skipped; live pages register; ownership errors stay strict (fakes here
        never violate ownership, so no raise paths are modelled)."""
        if page is None or page.is_closed():
            return False
        self.tracked.append(page)
        return True


class _FakeSession:
    def __init__(self):
        self.page = _FakePage()
        self.last_scope: _FakeScope | None = None

    async def start(self):
        return self.page

    async def guard_captcha(self, page=None):
        return None

    def temporary_pages(self):
        self.last_scope = _FakeScope()
        return self.last_scope


async def _noop(*a, **k):
    return None


async def _footmark_ok(page, pid):
    return {"url": PAGE_URL, "mi_id": "abc", "matches_target": True, "popup": None}


def _install_harness(monkeypatch):
    import src.browser.pacing as pacing_mod
    import src.browser.scroll as scroll_mod
    import src.browser.session as session_mod
    import src.extract.favorite as fav_mod

    monkeypatch.setattr(session_mod, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(pacing_mod, "human_delay", _noop)
    monkeypatch.setattr(scroll_mod, "scroll_into_view", _noop)
    monkeypatch.setattr(fav_mod, "open_via_footmark", _footmark_ok)


def test_qa_captcha_propagates(monkeypatch):
    """Fine-mode QA extraction hitting a captcha wall → CaptchaError propagates (not embedded)."""
    import src.extract.qa as qa_mod

    async def boom(*a, **k):
        raise CaptchaError()

    _install_harness(monkeypatch)
    monkeypatch.setattr(qa_mod, "parse_qa", boom)
    with pytest.raises(CaptchaError):
        asyncio.run(fetch_detail("12345678901", miid_source="footmark"))


def test_qa_selector_drift_propagates(monkeypatch):
    import src.extract.qa as qa_mod

    async def boom(*a, **k):
        raise SelectorDriftError(step="qa")

    _install_harness(monkeypatch)
    monkeypatch.setattr(qa_mod, "parse_qa", boom)
    with pytest.raises(SelectorDriftError):
        asyncio.run(fetch_detail("12345678901", miid_source="footmark"))


def test_reviews_captcha_propagates(monkeypatch):
    """with_reviews=True review extraction hitting a wall → CaptchaError propagates."""
    import src.extract.reviews as reviews_mod

    async def boom(*a, **k):
        raise CaptchaError()

    _install_harness(monkeypatch)
    monkeypatch.setattr(reviews_mod, "parse_reviews_stratified", boom)
    with pytest.raises(CaptchaError):
        asyncio.run(fetch_detail("12345678901", miid_source="footmark", with_reviews=True))


def test_reviews_selector_drift_propagates(monkeypatch):
    import src.extract.reviews as reviews_mod

    async def boom(*a, **k):
        raise SelectorDriftError(step="reviews")

    _install_harness(monkeypatch)
    monkeypatch.setattr(reviews_mod, "parse_reviews_stratified", boom)
    with pytest.raises(SelectorDriftError):
        asyncio.run(fetch_detail("12345678901", miid_source="footmark", with_reviews=True))


def test_recommend_captcha_propagates(monkeypatch):
    """Recommendation is optional, but a captcha wall must still propagate, not be swallowed."""
    import src.extract.qa as qa_mod
    import src.extract.recommend as recomm_mod

    async def no_qa(*a, **k):
        return []

    def boom_rank(raw):
        raise CaptchaError()

    _install_harness(monkeypatch)
    monkeypatch.setattr(qa_mod, "parse_qa", no_qa)
    monkeypatch.setattr(recomm_mod, "rank_recommendations", boom_rank)
    with pytest.raises(CaptchaError):
        asyncio.run(fetch_detail("12345678901", miid_source="footmark"))


def test_recommend_degrades_on_ordinary_error(monkeypatch):
    """Optional recommend extraction still degrades on ordinary errors (no raise)."""
    import src.extract.qa as qa_mod
    import src.extract.recommend as recomm_mod

    async def no_qa(*a, **k):
        return []

    def boom_rank(raw):
        raise ValueError("boom")

    _install_harness(monkeypatch)
    monkeypatch.setattr(qa_mod, "parse_qa", no_qa)
    monkeypatch.setattr(recomm_mod, "rank_recommendations", boom_rank)
    out = asyncio.run(fetch_detail("12345678901", miid_source="footmark"))
    assert "recommendations" in out
    assert out["recommendations"]["items"] == []


def test_qa_ordinary_error_still_embedded(monkeypatch):
    """Ordinary (non-captcha/drift) QA errors keep the embedded-error behavior."""
    import src.extract.qa as qa_mod

    async def boom(*a, **k):
        raise ValueError("boom")

    _install_harness(monkeypatch)
    monkeypatch.setattr(qa_mod, "parse_qa", boom)
    out = asyncio.run(fetch_detail("12345678901", miid_source="footmark"))
    assert out["qa"] == [{"error": "boom"}]


def test_cleanup_runs_when_captcha_escapes(monkeypatch):
    """A CaptchaError escaping from fine-mode extraction must NOT leave account-state
    residue: the favorite WE added this round is un-favorited in the finally, and the
    popup tab is closed by the temporary_pages scope on exit (shared-library cleanup
    ownership, ADAPTATION_GUIDE §6) before the error propagates (audit cleanup-on-error)."""
    import src.browser.pacing as pacing_mod
    import src.browser.scroll as scroll_mod
    import src.browser.session as session_mod
    import src.config as cfg_mod
    import src.extract.fav_quota as quota_mod
    import src.extract.favorite as fav_mod
    import src.extract.qa as qa_mod

    unfavorited = {"called": False}
    popup_closed = {"called": False}

    class _PopupPage(_FakePage):
        def is_closed(self):
            return False

        async def close(self):
            popup_closed["called"] = True

    popup = _PopupPage()

    class _FavSession:
        def __init__(self):
            self.page = _FakePage()
            self.last_scope: _FakeScope | None = None

        async def start(self):
            return self.page

        async def guard_captcha(self, page=None):
            return None

        def temporary_pages(self):
            self.last_scope = _FakeScope()
            return self.last_scope

    async def noop(*a, **k):
        return None

    async def fav_ok(page, pid):
        return {"added_by_us": True}

    async def click_ok(page, pid, added_by_us):
        return {"mi_id": "abc", "matches_target": True, "url": PAGE_URL, "popup": popup}

    async def un_fav(page, pid):
        unfavorited["called"] = True
        return {"state": "removed"}

    async def qa_boom(*a, **k):
        raise CaptchaError()

    monkeypatch.setattr(session_mod, "get_session", lambda: _FavSession())
    monkeypatch.setattr(pacing_mod, "human_delay", noop)
    monkeypatch.setattr(scroll_mod, "scroll_into_view", noop)
    monkeypatch.setattr(cfg_mod, "load_config", lambda: SimpleNamespace(
        anti_risk=SimpleNamespace(fav_flow=True, miid_channel="favorite"),
        detail=SimpleNamespace(mi_id=""),
    ))
    monkeypatch.setattr(quota_mod, "check_and_record", lambda: {"allowed": True})
    monkeypatch.setattr(fav_mod, "ensure_favorited", fav_ok)
    monkeypatch.setattr(fav_mod, "click_from_favorites", click_ok)
    monkeypatch.setattr(fav_mod, "ensure_unfavorited", un_fav)
    monkeypatch.setattr(qa_mod, "parse_qa", qa_boom)

    with pytest.raises(CaptchaError):
        asyncio.run(fetch_detail("12345678901", miid_source="favorite"))
    assert unfavorited["called"] is True, "ensure_unfavorited must run before the error escapes"
    assert popup_closed["called"] is True, "popup close must run before the error escapes"


def test_popup_closed_when_preharvest_step_raises(monkeypatch):
    """泄漏路径回归(共享库接入, ADAPTATION_GUIDE §6): popup 经足迹/收藏通道获取后、
    内层 try/finally 清理(原 _cleanup_fetch)之前 — URL 兜底 goto(638-645)、滚动(663-671)、
    harvest evaluate(673) — 抛出普通异常时, 旧实现 popup 直接泄漏(finally 根本不在场上);
    现在由 temporary_pages scope 退出关闭, 异常照常上浮。"""
    import src.browser.pacing as pacing_mod
    import src.browser.scroll as scroll_mod
    import src.browser.session as session_mod
    import src.extract.favorite as fav_mod

    class _PopupPage(_FakePage):
        def __init__(self):
            self._closed = False

        def is_closed(self):
            return self._closed

        async def close(self):
            self._closed = True

        async def evaluate(self, js):
            if js == DESC_PANEL_JS:
                raise RuntimeError("harvest boom (pre-try window)")
            return await super().evaluate(js)

    pop = _PopupPage()

    async def _footmark_pop(page, pid):
        return {"url": PAGE_URL, "mi_id": "abc", "matches_target": True, "popup": pop}

    sess = _FakeSession()

    _install_harness(monkeypatch)
    monkeypatch.setattr(session_mod, "get_session", lambda: sess)
    monkeypatch.setattr(fav_mod, "open_via_footmark", _footmark_pop)

    with pytest.raises(RuntimeError, match="harvest boom"):
        asyncio.run(fetch_detail("12345678901", miid_source="footmark"))
    assert pop._closed is True, "scope exit must close the popup even when harvest raises"
    assert sess.last_scope is not None and pop in sess.last_scope.closed


def test_scope_skips_already_closed_popup(monkeypatch):
    """track_temporary_page 的容错面: 通道返回已被站点/生产者关闭的 popup 时登记跳过,
    scope 退出不再触碰(closed 页面 close 会抛)。"""
    from src.browser.session import track_temporary_page

    class _Dead:
        def is_closed(self):
            return True

        async def close(self):
            raise AssertionError("closed pages must never be tracked/closed again")

    class _Scope:
        def __init__(self):
            self.tracked = []

        def try_track(self, p):
            if p is None or p.is_closed():
                return False
            self.tracked.append(p)
            return True

    scope = _Scope()
    dead = _Dead()
    assert track_temporary_page(scope, dead) is dead
    assert track_temporary_page(scope, None) is None
    assert scope.tracked == [], "closed/None pages are skipped, not tracked"
