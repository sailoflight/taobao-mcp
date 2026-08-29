# taobao-mcp WIN-WSL bridge instance mapping

> Conforms to `agent-project-guides/profiles/mcp/WINDOWS_WSL_BRIDGE.md`
> (subtype `windows-wsl-bridge`). This file only maps the generic spec roles to
> taobao-mcp entities; the generic invariants and acceptance checklist live in
> the subtype spec, not here.

## Instance mapping

| Spec role | taobao-mcp entity |
|---|---|
| WSL Facade | `tools/mcp_tcp_bridge.py` (stdlib-only stdio↔TCP relay, raw fd) |
| Internal transport | loopback TCP `127.0.0.1:8765` (WSL2 mirrored networking) |
| Windows Engine | `tools/bridge_server.py` (persistent listener) → `run_mcp_stdio.py` → `server.py` |
| Tools / native resources | `server.py` TOOLS/HANDLERS + `src/*`; browser profile `user_data/chrome_profile` (Windows) |
| Canonical runtime prompt | `server.py` `FastMCP(instructions=…)` delivered via MCP `initialize.instructions`; single authored source in `server.py`, revision = committed repo state (git) |
| Client adapters | DSH `@deepseek-ai/dsh-mcp-client` (stdio) and Codex; WSL DSH config in `DSH_WSL_BRIDGE.md` |
| Operator / runbook | `tools/wsl_bridge_ctl.sh` + `tools/windows/` (windowless start/restart/autostart) |
| Verification | offline: `tests/test_bridge_architecture.py`, `tests/test_windows_bridge_scripts.py`; live: `tools/mcp_probe.py` |

## Transport and lifecycle

- Bind only `127.0.0.1`; never `0.0.0.0`; no public exposure.
- One live MCP client at a time (`_ACTIVE_LOCK` in `tools/bridge_server.py`).
- Client disconnect/reconnect does not close the browser; only bridge restart
  (or Windows reboot) closes it → re-login via `taobao_session(action=login)`.

## Prompt delivery

- WSL facade forwards `initialize` verbatim; no second hand-written prompt copy.
- Each supported client must make `initialize.instructions` model-visible
  before the first tool decision; `tools/list` alone is not sufficient.

## Compatibility matrix

| Client | Transport | Prompt delivery | Verified |
|---|---|---|---|
| DSH `dsh-mcp-client` | stdio → `tools/mcp_tcp_bridge.py` | native `initialize.instructions` | `tools/mcp_probe.py` + DSH manual |
| Codex | stdio → relay | native `initialize.instructions` | manual |

## Conformance checklist

- [x] WSL facade complete stdio MCP relay, stdlib-only.
- [x] Loopback-only internal channel (`127.0.0.1:8765`).
- [x] Engine lazy-loads Playwright (`server.py` top-level has no GUI deps).
- [x] Single-owner browser profile.
- [x] stdout protocol-pure; diagnostics to stderr/log.
- [x] Offline structural guards + live probe exist.
- [x] Runtime prompt delivered via `initialize.instructions` (probe-verified: `tools/mcp_probe.py`); DSH/Codex manual visibility recorded in Operator runbook.
