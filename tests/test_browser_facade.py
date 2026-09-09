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


def test_temporary_pages_scope_delegates_and_closes_tracked(managers):
    """Facade delegation (guide §6): temporary_pages() returns the library scope;
    a tracked native page closes on scope exit while the working page is untouched.
    Before start() it fails closed with ResourceUnavailableError."""
    created, factory = managers
    sess = _session(factory)

    from browser_common import ResourceUnavailableError

    with pytest.raises(ResourceUnavailableError):
        sess.temporary_pages()

    async def go():
        page = await sess.start()
        extra = await sess._owner.context.new_page()
        async with sess.temporary_pages() as scope:
            sess_mod.track_temporary_page(scope, extra)
            obs = {
                "extra_open_inside": not extra.is_closed(),
                "work_open_inside": not page.is_closed(),
            }
        obs["extra_closed_after"] = extra.is_closed()
        obs["work_open_after"] = not page.is_closed()
        report = await sess.close()
        obs["report"] = (report.complete, report.clean)
        return obs

    obs = asyncio.run(go())
    assert obs["extra_open_inside"] and obs["work_open_inside"]
    assert obs["extra_closed_after"], "scope exit must close the tracked temporary page"
    assert obs["work_open_after"], "the working page is never a scope victim"
    assert obs["report"] == (True, True)


def test_scope_exit_never_masks_body_exception(managers):
    """库语义锁定(browser_common async_session.__aexit__): 业务异常传播途中清理
    不完整 → 原异常上浮 + add_note(不抛 PageCleanupError);正常退出才 fail-loud。
    desc/qa/reviews 的 CaptchaError/SelectorDriftError 上浮依赖此语义 — 若库回退为
    一律抛 PageCleanupError, 风控墙会被清理失败遮蔽。"""
    created, factory = managers
    sess = _session(factory)

    async def go():
        await sess.start()

        async def bad_close():
            raise RuntimeError("stuck tab")

        stuck = await sess._owner.context.new_page()
        stuck.close = bad_close
        raised = None
        scope = None
        try:
            async with sess.temporary_pages() as tp_scope:
                scope = tp_scope
                sess_mod.track_temporary_page(scope, stuck)
                raise RuntimeError("boom")  # 业务异常(如 CaptchaError 的占位)
        except RuntimeError as exc:
            raised = exc
        obs1 = {
            "msg": str(raised),
            "notes": [n for n in (getattr(raised, "__notes__", None) or [])],
            "incomplete": scope is not None and not scope.report.complete,
        }

        stuck2 = await sess._owner.context.new_page()
        stuck2.close = bad_close
        err2 = None
        try:
            async with sess.temporary_pages() as tp_scope2:
                sess_mod.track_temporary_page(tp_scope2, stuck2)
        except Exception as exc:  # noqa: BLE001
            err2 = exc
        obs2 = {
            "type": type(err2).__name__ if err2 else None,
            "report_complete": err2.report.complete if err2 is not None else None,
        }
        await sess.close()
        return obs1, obs2

    obs1, obs2 = asyncio.run(go())
    assert obs1["msg"] == "boom", "the body exception must surface, not the cleanup failure"
    assert obs1["notes"] and "cleanup incomplete" in obs1["notes"][0]
    assert obs1["incomplete"]
    assert obs2["type"] == "PageCleanupError", "clean exit + failed close must fail loud"
    assert obs2["report_complete"] is False
