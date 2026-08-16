"""Persistent headed real-Chrome session: launch, login, login-check, captcha pause.

Owns the singleton Playwright persistent context (CLAUDE.md §7 + Phase 1). The
session-persistence approach is adapted from the base repo (NOTES.md §6), but
launch is hardened per §7: real Chrome via ``channel="chrome"``, the
AutomationControlled flag off, locale/timezone set, ``navigator.webdriver``
masked, and login is converted from the base's *passive* "return login_required"
into an *active* QR-polling ``ensure_logged_in()``.

Captcha rule (§7.4): on a slider/punish/login wall, set ``human_action_required``,
leave the window visible, and poll until the human clears it. NEVER auto-solve.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from src.config import Config, load_config
from src.errors import BrowserLaunchError, CaptchaError
from src.log import get_logger

# Masks the one fingerprint that bundled automation leaks. Real Chrome +
# --disable-blink-features=AutomationControlled already keeps this false; this
# init script makes it deterministic across pages.
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => false});"

# URL fragments / DOM hints that mean "a human must act before we can continue".
_BLOCK_URL_HINTS = ("login.taobao.com", "login.tmall.com", "//login.", "punish", "_____tmd_____", "sec.taobao.com")
_SLIDER_SELECTORS = ("#nc_1_n1z", ".nc-container", ".nc_iconfont", "iframe[src*='punish']", "iframe[src*='baxia']")

# Real session-token cookies — present only while logged in and CLEARED on logout
# (unlike the remembered-nick cookies tracknick/lgc, which persist and falsely read as logged in).
_AUTH_COOKIE_NAMES = ("_tb_token_", "cookie2", "unb", "sgcookie")

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


class BrowserSession:
    """Singleton-style holder for the persistent context + working page."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self.playwright = None
        self.context = None
        self.page = None
        self.status = "uninitialized"
        self.human_action_required = False

    # ---- lifecycle ---------------------------------------------------------
    async def start(self):
        """Launch (or reuse) the persistent headed real-Chrome context; return the page."""
        # Reuse a live page if the browser is still responsive.
        if self.page is not None and not self.page.is_closed():
            try:
                await self.page.evaluate("1 + 1")
                return self.page
            except Exception:
                await self.close()

        b = self.config.browser
        user_dir = _resolve_project_user_data_dir(b.user_data_dir)
        user_dir.mkdir(parents=True, exist_ok=True)

        self.playwright = await async_playwright().start()
        launch_kwargs = dict(
            user_data_dir=str(user_dir),
            headless=b.headless,
            locale=b.locale,
            timezone_id=b.timezone,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        if b.executable_path:
            launch_kwargs["executable_path"] = b.executable_path  # pin exact Google Chrome binary
        elif b.channel:
            launch_kwargs["channel"] = b.channel  # real Chrome, not bundled Chromium
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:  # channel="chrome" needs Google Chrome installed
            await self._stop_playwright()
            raise BrowserLaunchError(
                f"Could not launch Chrome (channel={b.channel!r}): {exc}. "
                "Install Google Chrome, or run `.venv/bin/python -m playwright install chrome`. "
                "To fall back to bundled Chromium, set channel = \"\" in config.local.toml."
            ) from exc

        await self.context.add_init_script(_STEALTH_JS)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        try:
            await self.page.bring_to_front()  # make the Chrome window unambiguous/front-most
        except Exception:
            pass
        self.status = "started"
        return self.page

    async def close(self) -> None:
        try:
            if self.context is not None:
                await self.context.close()
        except Exception:
            pass
        await self._stop_playwright()
        self.context = None
        self.page = None
        self.status = "closed"

    async def _stop_playwright(self) -> None:
        try:
            if self.playwright is not None:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None

    # ---- login -------------------------------------------------------------
    async def is_logged_in(self) -> bool:
        """Cheap cookie check: a nick cookie is present only when logged in."""
        if self.context is None:
            return False
        try:
            cookies = await self.context.cookies()
        except Exception:
            return False
        names = {c.get("name") for c in cookies}
        return any(name in names for name in _AUTH_COOKIE_NAMES)

    async def ensure_logged_in(self, timeout_s: int = 180, poll_s: float = 3.0) -> str:
        """Ensure a logged-in session, actively polling for the human's QR scan.

        Returns 'logged_in', or a 'login_required: ...' message if the human
        hasn't scanned within timeout_s.
        """
        page = await self.start()
        await page.goto("https://www.taobao.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        if await self.is_logged_in():
            self.human_action_required = False
            self.status = "logged_in"
            return "logged_in"

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
            if await self.is_logged_in():
                self.human_action_required = False
                self.status = "logged_in"
                return "logged_in"

        return (
            "login_required: scan the QR code in the Chrome window with the "
            "Taobao app, then retry."
        )

    # ---- captcha / punish handoff -----------------------------------------
    async def _looks_blocked(self, page) -> bool:
        url = (page.url or "").lower()
        if any(hint in url for hint in _BLOCK_URL_HINTS):
            return True
        for sel in _SLIDER_SELECTORS:
            try:
                if await page.query_selector(sel):
                    return True
            except Exception:
                pass
        return False

    async def guard_captcha(self, page=None, timeout_s: int = 300, poll_s: float = 3.0) -> None:
        """If a slider/punish/login wall is showing, pause and wait for the human.

        Sets ``human_action_required`` and polls until the page clears. Raises
        CaptchaError on timeout. Never auto-solves (§7.4).
        """
        page = page or self.page
        if page is None or not await self._looks_blocked(page):
            return
        self.human_action_required = True
        self.status = "human_action_required"
        get_logger().warning("captcha/punish detected at %s — handing off to human", (page.url or "").split("?")[0][:80])
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
    """Cheap cookie/DOM logged-in check."""
    return await get_session().is_logged_in()


async def guard_captcha(page=None) -> None:
    """Detect slider/punish/login-wall; pause for the human until cleared."""
    return await get_session().guard_captcha(page)
