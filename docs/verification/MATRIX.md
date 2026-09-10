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

## Record (deployment upgrade + bridge smoke, 2026-09-10)

- Candidate: main @ `cad398c` (adoption chain `0ba55af`…`ac5def6` + status-handler
  fix) with `lijq-browser-common==0.1.0.dev2`, deployed to the Windows copy at
  `C:\MCP\taobao-mcp` (file-copy deployment, not a git clone; pre-sync fingerprint
  matched `2547c56` with local governance evolution — governance-coupled files
  `AGENTS.md`/`.agent-guides*`/`dsh/`/`src/runtime_prompt.py` and deployment-only
  files `bridge/`, `DSH_WSL_BRIDGE.md`, `STDIO_DEPLOYMENT.json`, `.mcp.json`,
  `config.local.toml`, `user_data/`, `output/` were deliberately NOT synced).
- Recovery point: `/mnt/c/MCP/taobao-mcp_backup_20260910_pre_dev2.tar.gz`
  (source tree, excludes runtime dirs) + pre-upgrade pip freeze snapshot; dev1
  wheel retained.
- Executed (WSL): full suite 433 passed + 1 skipped (includes the new
  status-before-start regression test); sync scoped to `src/browser/`,
  `src/extract/{desc,qa,reviews,orders,favorite,search}.py`, `pyproject.toml`,
  docs; `py_compile` of all synced files via the deployment interpreter
  (Python 3.14.6); wheel installed offline (`--force-reinstall --no-deps`);
  import + new-API check on the deployment venv.
- Executed (bridge): backend restart gen 1→2 and gen 2→3, both with clean
  phases (drain → protocol-close → wait, no force-kill); taobao connection
  expanded and catalogued — exactly the authoritative 13 tools, `verified`;
  read-only smokes: `taobao_session(action=status)` → `not_started: call
  taobao_session(action=login) first …` (correct pre-start guidance);
  `taobao_config(action=get)` → full config, confirms the deployment runs the
  Edge pinned-binary path (`browser.executable_path=…msedge.exe`), which the
  facade's `executable_path` branch covers.
- Finding (fixed during the smoke): the status tool used the retired
  `session.context is None` semantics; under the shared-library §5 contract
  (properties raise when unavailable) it errored at the protocol layer instead
  of returning `not_started`. Fixed in `server.py` with a lazy-imported catch +
  protocol regression test (`cad398c`); redeployed and re-verified in the same
  session. Offline fake-based tests had not covered this handler path.
- Not executed / not claimed: QR login, any Taobao navigation, popup-chain and
  orders logistics flows on the real account, and `tools/smoke_browser_common.py`
  on the host (isolated-profile facade smoke still available for the next
  host-side session). These require the human at the window and separate
  approval.

## Record (real-account live smoke through the bridge, 2026-09-10, user present)

- Scope: user said "继续测试" with the window visible; staged read-only ladder —
  login → status → favorites list → fine-mode product (popup chain) →
  tracking (orders logistics). All via the bridge against deployment gen 3+
  (dev2, Edge pinned-binary path). Pacing/quotas respected (fav/search quota
  0/30 untouched; footmark channel only; no writes beyond the tool-designed
  reversible favorite flow, which was not triggered).
- Passed: `taobao_session(action=login)` → `logged_in` (warm profile, no QR);
  status telemetry (pacing slots + dual quotas) intact; `taobao_favorites
  list` rendered 10 items; **popup chain end-to-end** on product 862892097837 —
  footmark channel (`miid_from=footmark_click`, opened_id matched, 0 favorite
  quota), fresh mi_id, 平台加补后 price observed, 23 detail images, 9 QA pairs,
  46→5 recommendations, `_cleanup_fetch` ran, scope exit silent (fail-loud
  contract ⇒ no PageCleanupError ⇒ clean close); tracking live run enumerated
  30 order ids within the new 90s budget and stamped today's cache (09:28:18);
  restart-time release log `browser released (clean=True, driver=stopped)`
  confirms the library releases cleanly even on process teardown.
- Finding 1 (fixed, `cad398c`): status tool vs the §5 raise contract — see the
  previous record.
- Finding 2 (fixed, `baa7251`): tracking wedge — the first live run hung 8+
  minutes on the order-list stage with zero logs (unbounded evaluate after
  goto; the user watching the window reported the page parked on 全部订单).
  The enumeration stage is now bounded (`_ENUM_BUDGET_S=90`) and fails loud as
  SelectorDriftError without stamping a cache; a success log
  `track: enumerated N order ids` was added. Regression test replays the hang.
  The wedge did not reproduce after the fix (page rendered; 30 ids in ~47s).
- Finding 3 (OPEN — selector drift, NOT a shared-library issue): today's
  tracking digest is degraded — 2 active orders, status 未知, empty titles/
  tracking#/取件码. The orders parsers are byte-identical to the 8/19-era code;
  today's 已买到的宝贝 (全部订单 tab) and/or logistics page DOM has drifted.
  Per the change matrix, live selector work requires separate approval. The
  adoption paths themselves are fully verified.
