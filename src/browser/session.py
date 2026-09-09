"""Persistent headed real-Chrome session: launch, login, login-check, captcha pause.

Resource ownership is delegated to the shared library ``browser_common``
(``AsyncSession``, lijq-browser-common==0.1.0.dev1): it exclusively holds the
native Playwright driver/context and the single working page, while THIS module
keeps all Taobao business policy — active QR-polling login, captcha handoff,
stealth init script, project-local profile boundary (ADAPTATION_GUIDE §2/§10).

Launch hardening (§7) is unchanged: real Chrome via ``channel="chrome"``, the
AutomationControlled flag off, locale/timezone set, ``navigator.webdriver``
masked, headed mode fail-closed.

Captcha rule (§7.4): on a slider/punish/login wall, set ``human_action_required``,
leave the window visible, and poll until the human clears it. NEVER auto-solve.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from browser_common import (
    AsyncSession,
    PageCleanupError,
    ReleaseReport,
    ResourceUnavailableError,
    SessionConfig,
    SessionStateError,
)

from src.config import Config, load_config
from src.errors import BrowserLaunchError, CaptchaError
from src.log import get_logger

# Masks the one fingerprint that bundled automation leaks. Real Chrome +
# --disable-blink-features=AutomationControlled already keeps this false; this
# init script makes it deterministic across pages.
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => false});"

# URL fragments / DOM hints that mean "a human must act before we can continue".
_BLOCK_URL_HINTS = ("login.taobao.com", "login.tmall.com", "//login.", "punish", "_____tmd_____", "sec.taobao.com")
# Slider/security wall selectors — a human must solve these. `.baxia-dialog` is
# included only so that a frequency popup whose X could not be auto-clicked is
# still surfaced as `human_action_required` instead of silently ignored.
_SLIDER_SELECTORS = (
    "#nc_1_n1z",
    ".nc-container",
    ".nc_iconfont",
    "iframe[src*='punish']",
    "iframe[src*='baxia']",
    ".baxia-dialog",
)

# Session cookies used only as a CHEAP PRE-FILTER. They are not sufficient on
# their own: Taobao's guest-friendly homepage now issues anonymous
# `_tb_token_`/`cookie2` even while logged out (verified 2026-08-18). The
# authoritative check is a REAL PAGE navigation to a login-gated URL (below),
# not a background `context.request` call: the background request can be risk-
# controlled into a login redirect even while the session is still valid, which
# then produced spurious "扫码 / 快速进入" pages.
_AUTH_COOKIE_NAMES = ("_tb_token_", "cookie2")
_LOGIN_GATE_URL = "https://i.taobao.com/my_itaobao"
_CONFIRMED_TTL_S = 15 * 60  # trust a successful page-verified login for 15 min

# Taobao shows a "快速进入" soft-reauth button when cookies are still valid but
# the risk layer wants a human click. Clicking it is NOT captcha-solving and
# does not enter credentials; it simply confirms the existing session.
_QUICK_ENTRY_JS = r"""() => {
  const labels = ['快速进入', '快速登录', '一键登录'];
  const nodes = [...document.querySelectorAll('button, a, [role="button"], div, span')];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const t = (el.innerText || '').trim();
    if (t && t.length <= 30 && labels.some(l => t.includes(l))) {
      el.click();
      return t;
    }
  }
  return null;
}"""

# "访问太频繁" is a soft frequency notice, not a captcha: closing its X is
# safe and does not solve a slider or enter credentials. If it won't close,
# guard_captcha() hands it to the human.
_FREQUENCY_DIALOG_SELECTOR = ".baxia-dialog"
_FREQUENCY_DIALOG_HINTS = ("访问太频繁", "操作太频繁", "请求过于频繁", "稍后再试")
_CLOSE_FREQUENCY_DIALOG_JS = r"""() => {
  const dialog = document.querySelector('.baxia-dialog');
  if (!dialog) return null;
  const nodes = [...dialog.querySelectorAll('button, a, [role="button"], i, span, div')];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const t = (el.innerText || '').trim();
    const cls = typeof el.className === 'string' ? el.className : '';
    if (/close|关闭|×|^x$/i.test(`${t} ${cls}`)) {
      el.click();
      return t || cls;
    }
  }
  return null;
}"""

# Browser state is private to this checkout.  The installed Chrome/Edge binary
# may be shared with the OS, but its normal user profile must never be opened by
# this project or become part of a deployment artifact.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_USER_DATA_ROOT = (_PROJECT_ROOT / "user_data").resolve()


def _resolve_project_user_data_dir(configured_path: str) -> Path:
    """Resolve and enforce the checkout-local browser-profile boundary."""
    raw_path = Path(configured_path).expanduser()
    resolved = (
        raw_path.resolve()
        if raw_path.is_absolute()
        else (_PROJECT_ROOT / raw_path).resolve()
    )
    if not resolved.is_relative_to(_PROJECT_USER_DATA_ROOT):
        raise BrowserLaunchError(
            "browser.user_data_dir must stay inside this project's user_data directory "
            f"({_PROJECT_USER_DATA_ROOT}). Refusing to use the operating-system browser profile: "
            f"{resolved}"
        )
    return resolved


async def _after_context_created(context) -> None:
    """Shared-library init hook (ADAPTATION_GUIDE §9): runs once per NEW context,
    before the working page is selected. Re-applies the §7 stealth init script.
    Must never reenter start/release and must stay short — no login/captcha here.
    """
    await context.add_init_script(_STEALTH_JS)


def _report_failures(report: ReleaseReport) -> str:
    """Compact, type-only rendering of a release report for logs (no page data)."""
    return ",".join(f"{f.operation}:{f.error_type}" for f in report.failures) or "none"


def track_temporary_page(scope, page):
    """Register a popup/temp page with an active temporary_pages() scope
    (ADAPTATION_GUIDE §6). A page the site already closed needs no cleanup and is
    skipped; tracked pages are closed by the scope on exit — failures surface as
    PageCleanupError instead of the old silent swallow (fail-loud, guide 完成指标
    "临时页不误关"/"失败不误报成功"). Returns the page unchanged.
    """
    if page is not None and not page.is_closed():
        scope.track(page)
    return page


class BrowserSession:
    """Business facade over ONE exclusive shared-library AsyncSession.

    browser_common owns the native driver/context/working page (single owner,
    ADAPTATION_GUIDE §2.4); this class keeps the historical surface — page/
    context accessors, status strings, login/captcha policy — that the extract
    modules and tests rely on, and maps business hard rules onto SessionConfig.
    """

    def __init__(self, config: Config | None = None, *, playwright_factory=None) -> None:
        self.config = config or load_config()
        self.status = "uninitialized"
        self.human_action_required = False
        self.login_confirmed: bool | None = None
        self.login_confirmed_at: float = 0.0
        # Serializes the probe→(release→restart)→start DECISION; the inner
        # AsyncSession has its own launch lock. Together they keep the §7.3
        # invariant: only ONE context per process.
        self._start_lock = asyncio.Lock()
        # Exclusive resource owner (created lazily on first start so that import
        # and constructor stay browser-free; tests may inject a factory).
        self._owner: AsyncSession | None = None
        self._playwright_factory = playwright_factory  # offline-test assembly only (§9)

    # ---- shared-library ownership ------------------------------------------
    def _build_owner(self) -> AsyncSession:
        """Map business config onto the shared-library SessionConfig.

        Order per ADAPTATION_GUIDE §2.3: business hard rules (headless fail-closed,
        project-local profile boundary) run FIRST; the library never overrides
        them. ``cleanup_restored=True`` reproduces the old startup behavior of
        closing every restored tab except the selected working one (§7.3).
        """
        b = self.config.browser
        if b.headless:
            # Fail-closed (§7.1): never launch a headless browser. The human must
            # watch the window and solve captchas; headless would fly blind.
            raise BrowserLaunchError(
                "browser.headless must be false — this project runs HEADED only "
                "(the human watches the window and solves captchas). Refusing to "
                "launch a headless browser."
            )
        user_dir = _resolve_project_user_data_dir(b.user_data_dir)
        user_dir.mkdir(parents=True, exist_ok=True)
        launch: dict = {
            "headless": False,  # enforced above, fail-closed
            "locale": b.locale,
            "timezone_id": b.timezone,
            "viewport": {"width": 1280, "height": 800},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if b.executable_path:
            launch["executable_path"] = b.executable_path  # pin exact Google Chrome binary
        elif b.channel:
            launch["channel"] = b.channel  # real Chrome, not bundled Chromium
        return AsyncSession(
            SessionConfig(user_dir, launch_options=launch, cleanup_restored=True),
            playwright_factory=self._playwright_factory,
            after_context_created=_after_context_created,
        )

    # Native-resource accessors: delegate to the exclusive owner. Per the library
    # contract they RAISE when unavailable instead of returning None (guide §5).
    @property
    def page(self):
        if self._owner is None:
            raise ResourceUnavailableError("Browser not started; call start() first")
        return self._owner.page

    @property
    def context(self):
        if self._owner is None:
            raise ResourceUnavailableError("Browser not started; call start() first")
        return self._owner.context

    def _page_or_none(self):
        try:
            return self.page
        except ResourceUnavailableError:
            return None

    def _context_or_none(self):
        try:
            return self.context
        except ResourceUnavailableError:
            return None

    def adopt_page(self, page) -> None:
        """Explicitly adopt a same-context page as the working page (guide §6).

        Replaces the old raw ``session.page = <page>`` assignment; adoption never
        closes the previous working page — the caller coordinates that cleanup.
        """
        if self._owner is None:
            raise ResourceUnavailableError("Browser not started; call start() first")
        self._owner.adopt_page(page)

    def temporary_pages(self, *, budget_ms=None):
        """Delegate: temporary-page cleanup scope (guide §6).

        Pages tracked inside the scope are closed on scope exit; the working page
        and pages of other active scopes are protected. Business keeps its own
        pacing/sequencing — the scope only owns cleanup responsibility.
        """
        if self._owner is None:
            raise ResourceUnavailableError("Browser not started; call start() first")
        return self._owner.temporary_pages(budget_ms=budget_ms)

    async def _release_owner(self) -> ReleaseReport:
        assert self._owner is not None
        report = await self._owner.release()
        if report.complete:
            get_logger().info(
                "browser released (clean=%s, context=%s, driver=%s)",
                report.clean, report.context_status, report.driver_status,
            )
        else:
            get_logger().warning(
                "browser release INCOMPLETE (context=%s, driver=%s, failures=%s) — "
                "session retained for a controlled re-release; resources are NOT "
                "confirmed freed",
                report.context_status, report.driver_status, _report_failures(report),
            )
        return report

    async def _ensure_released(self) -> None:
        """Release (with one controlled retry); raise only if still retained."""
        await self._release_owner()
        report = self._owner.last_release_report
        if report is not None and not report.complete:
            get_logger().warning(
                "first release incomplete (%s) — one controlled retry", _report_failures(report)
            )
            await self._release_owner()
            report = self._owner.last_release_report
            if report is not None and not report.complete:
                raise BrowserLaunchError(
                    "could not release the previous browser session "
                    f"({_report_failures(report)}); resolve the release failure "
                    "(or restart the MCP process) before starting again."
                )

    # ---- lifecycle ---------------------------------------------------------
    async def start(self):
        """Start (or reuse) the persistent headed real-Chrome context; return the page.

        Concurrent start() calls are serialized: the second caller waits for the
        first and then REUSES its launched context instead of launching a second
        one. Headed mode is enforced fail-closed BEFORE any launch.

        Reuse probe: the shared library never probes or restarts a ready session
        (guide §5) — that policy is business, so it lives here: when the library
        reports ready, the working page is probed with a cheap evaluate; a wedged
        page releases the whole browser and relaunches (same as pre-library).
        """
        async with self._start_lock:
            if self._owner is None:
                self._owner = self._build_owner()
            else:
                state = self._owner.snapshot().state
                if state == "ready":
                    try:
                        page = self._owner.page
                        await page.evaluate("1 + 1")
                        self.status = "started"
                        return page
                    except Exception:
                        # 工作页被关/卡死/context 被原生关闭 → 整体重启(接入前行为)。
                        get_logger().warning(
                            "working page not responsive — releasing browser for relaunch"
                        )
                        await self._ensure_released()
                elif state == "release_failed":
                    # 上一次 release 未完成: 先受控重试, 仍失败则 fail-closed。
                    await self._ensure_released()
            try:
                page = await self._owner.start()
            except PageCleanupError:
                raise  # restored-tab cleanup failed: report attached, resources freed (§8)
            except Exception as exc:  # channel="chrome" needs Google Chrome installed
                raise BrowserLaunchError(
                    f"Could not launch Chrome (channel={self.config.browser.channel!r}): {exc}. "
                    "Install Google Chrome, or run `.venv/bin/python -m playwright install chrome`. "
                    "To fall back to bundled Chromium, set channel = \"\" in config.local.toml."
                ) from exc
            try:
                await page.bring_to_front()  # make the Chrome window unambiguous/front-most
            except Exception:
                pass
            self.status = "started"
            return page

    async def close(self) -> ReleaseReport | None:
        """Release the browser (lifespan exit / compare reset). Best-effort like
        before, but HONEST: completion is reported and failures are logged with
        the library's per-resource report; the session stays releasable. The
        pre-library behavior of swallowing every error while always claiming
        "closed" is gone (guide §8 flags this as an intentional improvement).
        """
        if self._owner is None:
            self.status = "closed"
            return None
        try:
            report = await self._release_owner()
        except SessionStateError as exc:
            get_logger().error("browser close refused by ownership rules: %s", exc)
            return None
        if report.complete:
            self.status = "closed"
        return report

    # ---- login -------------------------------------------------------------
    @staticmethod
    def _is_login_url(url: str | None) -> bool:
        u = (url or "").lower()
        return "login.taobao.com" in u or "login.tmall.com" in u or "//login." in u

    def _mark_logged_in(self) -> str:
        self.login_confirmed = True
        self.login_confirmed_at = time.monotonic()
        self.human_action_required = False
        self.status = "logged_in"
        return "logged_in"

    async def _has_auth_cookies(self) -> bool:
        """Cheap pre-filter only — guests also receive these cookies."""
        context = self._context_or_none()
        if context is None:
            return False
        try:
            cookies = await context.cookies()
        except Exception:
            return False
        names = {c.get("name") for c in cookies}
        return bool(names.intersection(_AUTH_COOKIE_NAMES))

    async def _quick_entry_if_available(self, page) -> bool:
        """Click Taobao's 快速进入 soft-reauth button when it is on screen.

        This is not captcha-solving and does not enter credentials: the button
        only confirms a session whose cookies are still valid. Returns True when
        the page leaves the login URL after the click.
        """
        for frame in [page, *page.frames]:
            try:
                label = await frame.evaluate(_QUICK_ENTRY_JS)
            except Exception:
                continue
            if label:
                get_logger().info("clicked quick-entry button (%s)", label)
                await asyncio.sleep(3)
                try:  # a second confirmation button occasionally appears
                    await frame.evaluate(_QUICK_ENTRY_JS)
                    await asyncio.sleep(3)
                except Exception:
                    pass
                return not self._is_login_url(page.url)
        return False

    async def _verify_via_gate_page(self, page) -> bool:
        """Authoritative check using a REAL page navigation.

        A background `context.request` was rejected here (2026-08-18): Taobao's
        risk layer can 302 even a valid session to login.taobao.com, which made
        the server navigate the visible window to a spurious QR page. Navigating
        the actual page behaves like the human browser and exposes the 快速进入
        button when cookies are still valid.
        """
        try:
            await page.goto(_LOGIN_GATE_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            get_logger().warning("login-gate navigation failed", exc_info=True)
            return self.login_confirmed or False
        await asyncio.sleep(2)
        if not self._is_login_url(page.url):
            return True
        return await self._quick_entry_if_available(page)

    async def is_logged_in(self) -> bool:
        """Return the cached page-verified login state.

        Before the first successful `ensure_logged_in` verification the answer is
        False (safe default) — never a guest-cookie guess.
        """
        if self._context_or_none() is None or self.login_confirmed is None:
            return False
        if time.monotonic() - self.login_confirmed_at > _CONFIRMED_TTL_S:
            self.login_confirmed = None
            return False
        if not await self._has_auth_cookies():
            self.login_confirmed = False
            self.login_confirmed_at = 0.0
            return False
        return self.login_confirmed

    async def ensure_logged_in(self, timeout_s: int | None = None, poll_s: float | None = None) -> str:
        """Ensure a logged-in session, actively polling for the human's QR scan.

        Fast path: a recent page-verified login is reused without any extra
        navigation. Otherwise the visible page is used for verification so the
        快速进入 button can be clicked automatically when cookies are still valid.
        Returns 'logged_in', or a 'login_required: ...' message if the human
        hasn't scanned within timeout_s.
        """
        ar = load_config().anti_risk
        timeout_s = ar.login_timeout_s if timeout_s is None else timeout_s
        poll_s = 3.0 if poll_s is None else poll_s
        page = await self.start()

        if (
            self.login_confirmed
            and time.monotonic() - self.login_confirmed_at < _CONFIRMED_TTL_S
            and await self._has_auth_cookies()
        ):
            return self._mark_logged_in()

        await page.goto("https://www.taobao.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # If we are already on a login wall, try the soft quick-entry first.
        if self._is_login_url(page.url) and await self._quick_entry_if_available(page):
            return self._mark_logged_in()

        # Authoritative real-page check: logged-out guests 302 to login.*.
        if await self._verify_via_gate_page(page):
            return self._mark_logged_in()

        self.login_confirmed = False
        self.login_confirmed_at = time.monotonic()

        # Surface the QR page and wait for the human to scan (warm sessions
        # auto-redirect off login and the poll catches it immediately).
        await page.goto("https://login.taobao.com", wait_until="domcontentloaded")
        self.human_action_required = True
        self.status = "login_required"
        get_logger().info("QR login required — waiting up to %ss for human scan", timeout_s)

        waited = 0.0
        while waited < timeout_s:
            await asyncio.sleep(poll_s)
            waited += poll_s

            if self._is_login_url(page.url):
                # Quick-entry may appear while the QR page is open (cookies valid).
                if await self._quick_entry_if_available(page):
                    if await self._verify_via_gate_page(page):
                        return self._mark_logged_in()
            elif await self._verify_via_gate_page(page):
                # Human scan succeeded and the page redirected away from login.
                return self._mark_logged_in()

        return (
            "login_required: scan the QR code in the Chrome window with the "
            "Taobao app, then retry."
        )

    # ---- captcha / punish handoff -----------------------------------------
    async def dismiss_frequency_dialog(self, page=None) -> bool:
        """Close Taobao's soft '访问太频繁' popup by clicking its X.

        Returns True when there is no such popup or it was closed, False when a
        frequency dialog is present but not closable (→ hand to human). A real
        slider/punish wall is never auto-clicked.
        """
        page = page or self._page_or_none()
        if page is None:
            return True
        for _ in range(3):
            try:
                info = await page.evaluate(
                    """() => {
                      const d = document.querySelector('.baxia-dialog');
                      if (!d) return null;
                      const r = d.getBoundingClientRect();
                      return { text: (d.innerText || '').slice(0, 200), visible: r.width > 0 && r.height > 0 };
                    }"""
                )
            except Exception:
                return True
            if not info or not info.get("visible"):
                return True
            text = info.get("text", "")
            if not any(hint in text for hint in _FREQUENCY_DIALOG_HINTS):
                # Not the dismissible frequency notice; leave it for guard_captcha.
                return False
            try:
                clicked = await page.evaluate(_CLOSE_FREQUENCY_DIALOG_JS)
            except Exception:
                clicked = None
            if not clicked:
                return False
            get_logger().info("closed frequency dialog (%s)", clicked)
            await asyncio.sleep(1.5)
        return False

    def _candidate_pages(self, page=None) -> list:
        """所有相关活动标签页: 传入的 page + 浏览器上下文里全部未关闭的标签页。

        搜索提交后风控常在**新标签页**弹出验证码(2026-08-20 实测), guard 若只盯
        单个 page 就"看不见"验证码, 也不会在人工通过后检测到。扫描全部标签页才能
        覆盖这个场景。
        """
        out: list = []
        seen: set[int] = set()
        context = self._context_or_none()
        for p in [page, *(context.pages if context is not None else [])]:
            try:
                if p is not None and not p.is_closed() and id(p) not in seen:
                    seen.add(id(p))
                    out.append(p)
            except Exception:
                continue
        return out

    async def _any_visible_selector(self, page=None) -> bool:
        """True when a *visible* captcha/punish widget is on screen.

        Two failure modes this covers:
        - ``query_selector`` alone matches hidden elements too: Taobao leaves solved
          captcha widgets in the DOM (``display:none`` / zero-size / off-screen) for a
          while, and a hidden leftover must NOT keep us "blocked" after the human
          already solved it — only a widget the human can still see counts.
        - captcha/slider lives INSIDE an iframe (baxia slider / image-select): the
          main-document ``page.query_selector`` never sees it. We must walk every
          frame of every candidate page, not just the top document.
        """
        for p in self._candidate_pages(page):
            try:
                frames = p.frames
            except Exception:
                frames = []
            for frame in [p.main_frame, *frames]:
                for sel in _SLIDER_SELECTORS:
                    try:
                        el = await frame.query_selector(sel)
                    except Exception:
                        continue
                    if el is None:
                        continue
                    try:
                        if await el.is_visible():
                            return True
                    except Exception:
                        pass
        return False

    async def _looks_blocked(self, page=None) -> bool:
        # A login wall is authoritative on its own (the QR page IS the block),
        # across every open tab.
        for p in self._candidate_pages(page):
            url = (p.url or "").lower()
            if any(h in url for h in ("login.taobao.com", "login.tmall.com", "//login.")):
                return True
        # Other block URL hints (punish / sec.taobao.com / _____tmd_____) only
        # count while a visible widget confirms the wall. After the human solves
        # an image-select captcha the tab can stay on sec.taobao.com for a beat
        # with the widget already gone — a bare URL match would keep us stuck.
        if await self._any_visible_selector(page):
            return True
        return False

    async def _alert_human(self, page=None) -> None:
        """Bring the browser window to front and (on Windows) flash its taskbar
        icon, so the human notices a captcha/punish handoff. Best-effort only.
        """
        # 前置所有活动标签页里仍在的页(验证码可能在新标签页), 保证人工看得到。
        for p in self._candidate_pages(page):
            try:
                await p.bring_to_front()
            except Exception:
                pass
        if os.name == "nt":  # Windows 部署 — 任务栏闪烁 msedge/chrome + 置前
            try:
                import subprocess

                ps = (
                    "Add-Type @'\\n"
                    "using System; using System.Runtime.InteropServices;\\n"
                    "public class FlashWin { [DllImport(\"user32.dll\")] public static extern bool "
                    "FlashWindow(IntPtr h, bool b); [DllImport(\"user32.dll\")] public static extern bool "
                    "SetForegroundWindow(IntPtr h); }\\n"
                    "'@\\n"
                    "$w = Get-Process msedge, chrome | Where-Object { $_.MainWindowHandle -ne 0 } | "
                    "Select-Object -First 1\\n"
                    "if ($w) { [FlashWin]::FlashWindow($w.MainWindowHandle, $true) | Out-Null; "
                    "[FlashWin]::SetForegroundWindow($w.MainWindowHandle) | Out-Null }"
                )
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], creationflags=flags)
            except Exception:
                pass

    async def _dismiss_block_x(self, page=None) -> bool:
        """先能X就X: 点击阻塞对话框(含滑块, 用户确认可点X关闭)的关闭钮; 不自动滑.

        遍历所有活动标签页(验证码可能在新标签页), 任一页点掉 X 且整体不再阻塞即成功。
        """
        for p in self._candidate_pages(page):
            try:
                clicked = await p.evaluate(_CLOSE_FREQUENCY_DIALOG_JS)
            except Exception:
                continue
            if clicked:
                await asyncio.sleep(1.5)
                if not await self._looks_blocked(page):
                    get_logger().info("dismissed block dialog via X (%s)", clicked)
                    return True
        return False

    async def guard_captcha(self, page=None, timeout_s: int | None = None, poll_s: float | None = None) -> None:
        """If a slider/punish/login wall is showing, pause and wait for the human.

        Soft '访问太频繁' popups are dismissed automatically first. Sets
        ``human_action_required`` and polls until the page clears. Raises
        CaptchaError on timeout. Never auto-solves a real slider (§7.4).
        Bounded by anti_risk.captcha_timeout_s (config.toml).
        """
        ar = load_config().anti_risk
        timeout_s = ar.captcha_timeout_s if timeout_s is None else timeout_s
        poll_s = ar.captcha_poll_s if poll_s is None else poll_s
        page = page or self._page_or_none()
        if page is None:
            # 主工作页可能已关, 但浏览器还在(验证码可能在新标签页) — 用任一活动页。
            context = self._context_or_none()
            for p in (context.pages if context is not None else []):
                if not p.is_closed():
                    page = p
                    break
        if page is None:
            return
        await self.dismiss_frequency_dialog(page)  # 软'访问太频繁'先自动点X
        if await self._looks_blocked(page):
            # 再试阻塞对话框的 X(滑块常可点X关闭, 用户明确可点X或滑动) — 不自动滑
            await self._dismiss_block_x(page)
        if not await self._looks_blocked(page):
            # 没有阻塞了: 若上一轮 guard 因工具调用被中止(ABORTED)而留下陈旧的
            # human_action_required=True, 这里要主动清零 — 否则会话一直显示需人工,
            # 且下一次调用不会重新进入轮询(用户已通过也无人检测)。
            if self.human_action_required or self.status == "human_action_required":
                get_logger().info("captcha already cleared since the last (possibly aborted) guard — resetting human_action_required")
            self.human_action_required = False
            if self.status == "human_action_required":
                self.status = "resumed"
            return
        self.human_action_required = True
        self.status = "human_action_required"
        get_logger().warning("captcha/punish detected at %s — handing off to human", (page.url or "").split("?")[0][:80])
        await self._alert_human(page)  # 提醒人工(前置+任务栏闪烁)
        waited = 0.0
        interval = poll_s
        while waited < timeout_s:
            await asyncio.sleep(interval)
            waited += interval
            interval = min(interval * 1.5, 15.0)  # exponential backoff, capped
            if not await self._looks_blocked(page):
                self.human_action_required = False
                self.status = "resumed"
                get_logger().info("captcha cleared after ~%.0fs — resuming", waited)
                return
            await self._alert_human(page)  # 持续提醒直到清除
        get_logger().error("captcha not cleared within %ss", timeout_s)
        raise CaptchaError()


# ---- module-level singleton + thin wrappers (match stub signatures) --------
_session: BrowserSession | None = None


def get_session() -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession()
    return _session


async def start_session():
    """Launch persistent headed real-Chrome per base + local TOML config; return the page."""
    return await get_session().start()


async def ensure_logged_in() -> str:
    """Ensure login, actively polling for the human's QR scan."""
    return await get_session().ensure_logged_in()


async def is_logged_in() -> bool:
    """Authoritative logged-in check (login-gated URL, not just cookies)."""
    return await get_session().is_logged_in()


async def guard_captcha(page=None) -> None:
    """Detect slider/punish/login-wall; pause for the human until cleared."""
    return await get_session().guard_captcha(page)
