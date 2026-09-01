# Retired taobao-mcp WIN-WSL relay mapping

> **Historical reference only.** The project-owned relay, listener, launchers,
> and port-8765 deployment were intentionally removed. Paths and commands below
> explain the former design and are unsupported. Use `../operations/MCP_RUNBOOK.md`
> for the ordinary stdio MCP and independently installed bridge integration.

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
| Canonical runtime prompt | `src/runtime_prompt.py`, revisioned with `src/identity.py`; `server.py` returns it through MCP `initialize.instructions` |
| Client adapters | DSH MCP client + generated `tools/dsh/runtime-prompt-companion.js`; Codex/native clients consume `initialize.instructions` |
| WSL orchestration | `tools/mcp_bridge_entry.sh` checks health, starts Windows when needed, then execs the relay |
| Operator / runbook | `tools/wsl_bridge_ctl.sh` + `tools/windows/` (windowless start/restart/autostart) |
| Verification | offline: bridge/runtime-policy tests; live: `tools/mcp_probe.py` validates the deployed prompt revision |

## Transport and lifecycle

- Bind only `127.0.0.1`; never `0.0.0.0`; no public exposure.
- One live MCP client at a time (`_ACTIVE_LOCK` in `tools/bridge_server.py`);
  accepted MCP sockets enable keepalive, with bounded Windows keepalive timing
  where the runtime exposes `SIO_KEEPALIVE_VALS`.
- The bridge listener persists, while each client owns one FastMCP child and one
  browser process. Disconnect closes that child/browser cleanly; the Windows
  `user_data/chrome_profile` persists cookies and login state for reconnect.
- `status` uses a one-byte health preface handled before `_ACTIVE_LOCK`; it does
  not spawn a FastMCP child or contend with the live MCP session.
- The listener and scheduled task use `pythonw.exe`; connection children retain
  `python.exe` stdio with Windows `CREATE_NO_WINDOW`, so no console is shown.

## Prompt delivery

- WSL facade forwards `initialize` verbatim; no second hand-written prompt copy.
- Clients with native instruction support use `initialize.instructions`.
- DSH clients that only register tools must install the generated, namespaced
  companion from the same deployment generation; `tools/list` alone is not sufficient.

## Compatibility matrix

| Client | Transport | Prompt delivery | Verified |
|---|---|---|---|
| DSH MCP client | `tools/mcp_bridge_entry.sh` → relay | generated `tools/dsh/runtime-prompt-companion.js` | offline generation/relay tests; external-profile deployment remains Operator evidence |
| Codex/native client | stdio → relay | native `initialize.instructions` | protocol probe; client visibility is deployment evidence |

## Conformance checklist

- [x] WSL facade complete stdio MCP relay, stdlib-only.
- [x] Loopback-only internal channel (`127.0.0.1:8765`).
- [x] Engine lazy-loads Playwright (`server.py` top-level has no GUI deps).
- [x] Single-owner browser profile.
- [x] stdout protocol-pure; diagnostics to stderr/log.
- [x] Offline structural, runtime-policy, health-preface, and relay tests exist.
- [x] Revisioned runtime prompt is delivered through `initialize.instructions`.
- [x] Generated DSH companion has a staleness check and deployment wiring example.
- [x] Live probe rejects a missing or mismatched deployed prompt revision.
