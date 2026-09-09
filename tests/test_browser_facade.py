"""Facade contract tests: BrowserSession composes the shared-library AsyncSession.

Covers the lifecycle mappings introduced by the browser_common adoption
(ADAPTATION_GUIDE §5/§8/§9) with an offline fake playwright manager — no real
browser, no network. Business rules stay in the facade; the library owns
resources exclusively.

Scenarios:
- fresh start → ready page, status "started", stealth init script applied
- close() → release report complete + status "closed"; idempotent second close
- wedged working page → probe fails → controlled release → relaunch (new driver)
- adopt_page() → working page switches without closing the old page (guide §6)
- headless / launch failure mapping is covered in test_session_guard.py
"""

from __future__ import annotations

import asyncio

import pytest

from src.browser import session as sess_mod
from src.browser.session import BrowserSession


class _FakePage:
    """Fake native page: context backref (library ownership checks) + wedge flag."""

    def __init__(self, context: "_FakeContext"):
        self.context = context
        self._closed = False
        self.fail_evaluate_once = False

    def is_closed(self) -> bool:
        return self._closed

    async def evaluate(self, expr) -> int:
        if self.fail_evaluate_once:
            self.fail_evaluate_once = False
            raise RuntimeError("page wedged")
        return 2

    async def bring_to_front(self) -> None:
        pass

    async def close(self) -> None:
        self._closed = True


class _FakeContext:
    def __init__(self):
        self.pages: list[_FakePage] = []
        self.init_scripts: list[str] = []
        self._close_cb = None

    # library _attach_context/_forget_context need the native event API
    def on(self, event: str, cb) -> None:
        if event == "close":
            self._close_cb = cb

    def remove_listener(self, event: str, cb) -> None:
        if event == "close" and self._close_cb is cb:
            self._close_cb = None

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def new_page(self) -> _FakePage:
        page = _FakePage(self)
        self.pages.append(page)
        return page

    async def close(self) -> None:
        pass


class _FakeManager:
    """Satisfies the library's native manager protocol (ADAPTATION_GUIDE §9)."""

    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.chromium = _FakeChromium(self)

    async def start(self) -> "_FakeManager":
        self.started += 1
        return self

    async def stop(self) -> None:
        self.stopped += 1


class _FakeChromium:
    def __init__(self, manager: _FakeManager):
        self._manager = manager
        self.launch_calls = 0
        self.context = _FakeContext()

    async def launch_persistent_context(self, **kwargs) -> _FakeContext:
        self.launch_calls += 1
        # launch_persistent_context restores previous-session tabs: model one.
        self.context.pages = [_FakePage(self.context)]
        return self.context


@pytest.fixture()
def managers(monkeypatch, tmp_path):
    """Factory returning one fresh fake manager per launch cycle + path patch."""
    created: list[_FakeManager] = []

    def factory() -> _FakeManager:
        mgr = _FakeManager()
        created.append(mgr)
        return mgr

    monkeypatch.setattr(sess_mod, "_resolve_project_user_data_dir", lambda p: tmp_path / "profile")
    return created, factory


def _session(factory) -> BrowserSession:
    return BrowserSession(playwright_factory=factory)


def test_fresh_start_and_stealth_script(managers):
    created, factory = managers
    sess = _session(factory)

    async def go():
        page = await sess.start()
        # 断言取值必须在 owning loop 内(库的 ExecutionContextError 契约)。
        obs = {
            "page_is_page": page is sess.page,
            "status": sess.status,
            "state": sess._owner.snapshot().state,
            "scripts": list(sess._owner._context.init_scripts),
        }
        report = await sess.close()
        obs["report"] = (report.complete, report.clean)
        obs["status_closed"] = sess.status
        report2 = await sess.close()
        obs["report2"] = (report2.complete, report2.context_status)
        return obs

    obs = asyncio.run(go())
    assert obs["page_is_page"]
    assert obs["status"] == "started"
    assert obs["state"] == "ready"
    # after_context_created: stealth init script re-applied on every NEW context.
    assert obs["scripts"] == [sess_mod._STEALTH_JS]
    assert obs["report"] == (True, True)
    assert obs["status_closed"] == "closed"
    # idempotent second close: nothing left to release, still honestly closed.
    assert obs["report2"] == (True, "absent")
    assert len(created) == 1


def test_wedged_page_releases_and_relaunches(managers):
    created, factory = managers
    assert len(created) == 0
    sess = _session(factory)

    async def go():
        first = await sess.start()
        first.fail_evaluate_once = True  # next reuse probe hits a wedged page
        second = await sess.start()      # probe fails → release → fresh launch
        obs = {
            "different": first is not second,
            "status": sess.status,
            "last_release_complete": sess._owner.last_release_report.complete,
        }
        await sess.close()
        obs["stopped_first"] = created[0].stopped
        return obs

    obs = asyncio.run(go())
    assert obs["different"]                       # fresh launch, fresh page
    assert len(created) == 2                      # two manager lifecycles
    assert obs["stopped_first"] == 1              # wedged browser released cleanly
    assert created[1].chromium.launch_calls == 1
    assert obs["status"] == "started"
    assert obs["last_release_complete"]


def test_adopt_page_switches_working_page_without_closing_old(managers):
    created, factory = managers
    sess = _session(factory)

    async def go():
        old = await sess.start()
        new = await sess._owner.context.new_page()  # native creation, business adopt
        sess.adopt_page(new)
        obs = {
            "page_is_new": sess.page is new,
            "old_still_open": not old.is_closed(),
        }
        report = await sess.close()
        obs["status"] = sess.status
        obs["report_complete"] = report.complete
        return obs

    obs = asyncio.run(go())
    assert obs["page_is_new"]
    assert obs["old_still_open"]  # adoption never closes the previous working page
    assert obs["status"] == "closed"
    assert obs["report_complete"]
