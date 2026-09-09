# Verification matrix

## Defaults

- Default network: offline.
- Default production/account mutation: forbidden.
- Default data: synthetic, fixture, or sanitized.
- Browser login/crawl is never part of repository regression tests.

## Core commands

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile \
  server.py run_mcp_stdio.py configure_codex.py tools/mcp_probe.py \
  dsh/build_runtime_prompt_companion.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python dsh/build_runtime_prompt_companion.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python verify_git_safety.py
```

## Change matrix

| Change | Required offline evidence | Broader condition |
|---|---|---|
| MCP schema/handler | syntax + tool/contract tests | external client check when compatibility changes |
| Parser/extraction | matching parser fixtures/tests | live selector work only when separately approved |
| Browser/session | browser/config tests | human-visible target-host check only when approved |
| Shared-library adoption (`lijq-browser-common`) | facade/library contract tests (`tests/test_browser_facade.py`, `test_session_guard.py`), full suite green, wheel checksum vs `LIBRARIES.md` | `tools/smoke_browser_common.py` on the browser host (isolated profile, zero Taobao traffic) before production-profile use |
| Stdio entry | `test_stdio_architecture`, runtime-prompt tests, `tools/mcp_probe.py` | target-host probe |
| DSH companion | builder `--check`, runtime-prompt tests | external-cwd model visibility |
| External bridge config | this repo's DSH example/static guard | bridge project's own doctor/registry/lifecycle suite |
| Retired relay history | link/banner review | no executable path may return |

## Negative relay guard

Current source and documentation, excluding `docs/history/`, must not reference or
restore `mcp_tcp_bridge.py`, `bridge_server.py`, `mcp_bridge_entry.sh`,
`wsl_bridge_ctl.sh`, `tools/windows`, port 8765, or `TaobaoMCPBridge`.

## Live gates

- `taobao_session(action=login)` requires a human QR window and is not an offline test.
- Cart additions, seller replies, config writes, and any other account mutation
  require their schema-defined confirmation.
- Payment, checkout, address selection, captcha bypass, and destructive cleanup
  are never regression checks.

Record commands actually run, scope, environment, results, and skipped evidence.
Never describe an unexecuted or external deployment check as passed.

## Record (browser_common adoption, 2026-09-10)

- Environment: Linux dev checkout without any browser binary; the library smoke
  therefore could not run here. The user approved offline adoption with the
  real-browser smoke deferred to the browser host (stage 3).
- Run: full pytest suite 431 passed + 1 skipped (run twice, before and after the
  stage-2 cleanup-ownership change); shared-library offline suite 102 OK
  (stage 0, wheel checksum verified against `LIBRARIES.md`); py_compile on every
  touched module.
- Pending target-host evidence (not run, not claimed):
  `tools/smoke_browser_common.py` on the browser host, then human-visible
  session checks only when separately approved.

## Record (shared-library upgrade to 0.1.0.dev2, 2026-09-10)

- Wheel `lijq_browser_common-0.1.0.dev2-py3-none-any.whl` (23582 bytes), sha256
  `c1763265aa5c968032e7d18116d07e18ddbdbd873a4d6c7db033686a58913cc8` verified
  against the library's own release note (`FEEDBACK_DEV2.md`). Offline local
  install with `--force-reinstall --no-deps`; recovery point: reinstall the
  retained dev1 wheel (sha256 `0a32e9f0…`, recorded in `/tmp/backup_pip_freeze_dev1.txt`
  snapshot of the pre-upgrade freeze).
- Business adoption (user-approved): facade `track_temporary_page` now calls the
  library's `try_track` (identical skip/raise semantics, returns the page
  unchanged); `_report_failures` delegates to `report.failures_summary`;
  dependency pinned to `==0.1.0.dev2`; runbook checksum updated; feedback doc
  marked resolved.
- Run: full pytest suite green after adoption (see commit); library-side release
  note reports 111 offline unittests. Browser-host smoke still pending as above.
