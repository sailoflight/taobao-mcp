# Verification matrix

## Defaults and boundaries

- Default network: offline / live-approved-only
- Default production write: forbidden
- Default data: synthetic / fixture / sanitized
- Supported verification platforms: Python 3.11+ on WSL (offline), Windows host (live bridge)

## Command authorities

| Check family | Command/config source | Working directory | Expected evidence |
|---|---|---|---|
| Syntax | `.venv/bin/python -m py_compile server.py tools/*.py` | repo root | exit 0 |
| Offline tests | `.venv/bin/python -m pytest -q` | repo root | pass count, 0 failures |
| Bridge structure guards | `pytest tests/test_bridge_architecture.py tests/test_windows_bridge_scripts.py -q` | repo root | pass |
| Git safety | `.venv/bin/python verify_git_safety.py` | repo root | "Git safety checks passed" |
| Live bridge probe | `python3 tools/mcp_probe.py` | repo root | initialize/tools/list/tools/call ok, idle-ok |
| Bridge health | `tools/wsl_bridge_ctl.sh status` | repo root | "bridge reachable" |

## Change matrix

| Change type/scope | Fast check | Required checks | Broader trigger | External risk/cost |
|---|---|---|---|---|
| Documentation only | markdown review | none | generated reference changed | 0 |
| Internal logic (parsers) | py_compile | matching `tests/test_*.py` | shared boundary (models) | 0 |
| MCP tool/API change | py_compile + schema | `tests/test_tools.py`, contract tests | release/compat | low |
| Bridge/transport change | py_compile + structure guards | `tools/mcp_probe.py` live | client compat matrix | network/live |
| Config/anti-risk change | `taobao_config` unit | `tests/test_config.py` | production pacing caps | low |

## Live, destructive, and costly checks

| Check | Environment/data | Approval | Budget/stop condition | Cleanup/rollback |
|---|---|---|---|---|
| `taobao_session(action=login)` | Windows real Chrome | human QR scan | 180s login window | none (profile persists) |
| `taobao_cart(action=add, confirm=true)` | real Taobao cart | explicit `confirm=true` | single SKU proof | exact-sku rollback |
| `taobao_message(action=reply, confirm=true)` | real seller chat | per-message human OK | one message | none |
| Bridge restart | Windows engine | human OK | `tools/wsl_bridge_ctl.sh restart` | force-restart runbook |

## Evidence and incomplete verification

Report commands/checks actually run, scope, environment, result, and artifact. For
skipped/failed checks, record reason and remaining risk; never turn an unrun check
into a pass.
