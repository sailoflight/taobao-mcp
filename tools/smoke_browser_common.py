"""Browser-host smoke for the browser_common adoption (facade-level, zero Taobao).

Run this ON THE BROWSER HOST (Production / Operator, after stopping the ordinary
MCP process) to verify the pinned shared-library integration against a REAL
headed Chrome before pointing the MCP at the production profile:

    python tools/smoke_browser_common.py

What it does and does not do:
- Builds the production BrowserSession facade with an ISOLATED profile at
  user_data/smoke_profile (never user_data/chrome_profile) and the real
  playwright driver — no injection, no mocks.
- Exercises: start/probe, stealth init script, temporary_pages scope
  (track→close on exit, working page untouched), adopt_page, close report.
- Traffic: data: URLs only. No Taobao navigation, no login, no cookies from
  the production profile. Headed only (business hard rule §7.1).

Exit code 0 = all checks passed; 1 = any failure. Each check prints PASS/FAIL.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from src.browser.session import (  # noqa: E402
    BrowserSession,
    _STEALTH_JS,
    track_temporary_page,
)

RESULTS: list[tuple[str, bool, str]] = []
SMOKE_PROFILE = _PROJECT_ROOT / "user_data" / "smoke_profile"


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))


def _smoke_config():
    """Production-shaped config with an isolated profile (boundary-checked)."""
    return SimpleNamespace(browser=SimpleNamespace(
        headless=False,            # hard rule §7.1 — the facade fail-closes otherwise
        user_data_dir=str(SMOKE_PROFILE),
        channel="chrome",
        executable_path="",        # empty → channel wins (matches config.toml defaults)
        locale="zh-CN",
        timezone="Asia/Shanghai",
    ))


async def run_smoke() -> None:
    sess = BrowserSession(config=_smoke_config())
    page = await sess.start()
    record("S1 start→started + probe", sess.status == "started"
           and await page.evaluate("1 + 1") == 2, f"status={sess.status}")

    scripts = list(sess._owner._context.init_scripts)
    record("S2 stealth init script applied", scripts == [_STEALTH_JS],
           f"n_scripts={len(scripts)}")

    async with sess.temporary_pages() as scope:
        extra = track_temporary_page(scope, await sess._owner.context.new_page())
        await extra.goto("data:text/html,<p>scope-temp</p>")
        body = await extra.evaluate("() => document.body.innerText")
    record("S3 scope tracks+uses native page", body == "scope-temp")
    record("S4 scope exit closes temp page, working page untouched",
           extra.is_closed() and not page.is_closed() and scope.report.complete)

    adopted = await sess._owner.context.new_page()
    old = page
    sess.adopt_page(adopted)
    record("S5 adopt_page switches working page", sess.page is adopted
           and sess.page is not old and not old.is_closed())

    report = await sess.close()
    record("S6 close→release complete+clean",
           report is not None and report.complete and report.clean,
           f"context={getattr(report, 'context_status', '?')} "
           f"driver={getattr(report, 'driver_status', '?')}")


def main() -> int:
    if SMOKE_PROFILE.exists():
        shutil.rmtree(SMOKE_PROFILE, ignore_errors=True)  # isolated: never chrome_profile
    try:
        asyncio.run(run_smoke())
    except Exception as exc:  # noqa: BLE001
        record("smoke aborted", False, repr(exc))
        traceback.print_exc()
    finally:
        shutil.rmtree(SMOKE_PROFILE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n==== smoke summary: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS ====")
    return 1 if fails else 0


if __name__ == "__main__":
    print("This smoke opens a REAL headed Chrome on an isolated profile "
          "(user_data/smoke_profile). Stop the ordinary MCP process first.")
    sys.exit(main())
