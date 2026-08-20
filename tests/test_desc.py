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


class _FakeSession:
    def __init__(self):
        self.page = _FakePage()

    async def start(self):
        return self.page

    async def guard_captcha(self, page=None):
        return None


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
    residue: the favorite WE added this round is un-favorited and the popup tab is
    closed in the finally before the error propagates (audit cleanup-on-error)."""
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

        async def start(self):
            return self.page

        async def guard_captcha(self, page=None):
            return None

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
