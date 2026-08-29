# taobao-mcp Windows bridge runbook

Audience: operator
Scope: Windows-hosted bridge (`tools/bridge_server.py`) serving WSL MCP clients.
Authority/version: `DSH_WSL_BRIDGE.md` + `tools/windows/` scripts; port `127.0.0.1:8765`.

## Preconditions and access

| Requirement | Source | Minimum permission | Validation |
|---|---|---|---|
| Windows deployment copy `C:\MCP\taobao-mcp` | synced from repo | local user | `tools/wsl_bridge_ctl.sh status` |
| `.venv\Scripts\pythonw.exe` | Windows install | local user | file exists |
| Chrome profile `user_data\chrome_profile` | Windows engine | local user | exists after first login |

## Runtime topology and health baseline

WSL agent → `tools/mcp_tcp_bridge.py` → loopback TCP `127.0.0.1:8765` →
`tools/bridge_server.py` → `run_mcp_stdio.py` → `server.py` → Chrome/Edge.
Healthy: bridge port reachable; one client at a time; no `exited rc=0` while a
client is connected.

## Deploy, start, stop, or change

1. Precheck: `tools/wsl_bridge_ctl.sh status`.
2. Start (windowless): `tools/wsl_bridge_ctl.sh start` (or
   `wscript.exe tools\windows\start-bridge-hidden.vbs 8765` on Windows).
3. Restart after code change: `tools/wsl_bridge_ctl.sh restart` (force-kill
   browser+bridge, then fresh windowless start).
4. Verify: `python3 tools/mcp_probe.py` → initialize/tools/list/tools/call ok.

## Configuration and secrets

| Setting class | Authority/precedence | Reload/restart | Secret rule |
|---|---|---|---|
| `config.toml` | repo defaults | restart | tracked |
| `config.local.toml` | machine overrides | restart | never commit |
| Chrome profile | Windows engine | survives bridge restart | never commit |
| Port | `tools/*` + DSH config | restart both ends | loopback only |

## Observe and diagnose

| Signal/symptom | Query/check | Healthy threshold | Next section |
|---|---|---|---|
| bridge not reachable | `tools/wsl_bridge_ctl.sh status` | reachable | restart |
| 0 tools / no response | `python3 tools/mcp_probe.py` | ok | restart / check log |
| profile lock "正在使用" | stray Chrome/python | none | force restart |
| `rejected client ... active` | two MCP clients | one client | close extra session |

## Incident and stop conditions

Triage: status → probe → `output/bridge-server.log`. Stop crawling on captcha /
`human_action_required`; never bypass pacing or captcha. Hard stop before any
destructive/production action not explicitly approved.

## Backup, recovery, and data validation

Login state lives in `user_data\chrome_profile`; do not delete it as "cleanup".
After force-restart, verify `taobao_session(action=status)` returns a sane
session state; re-login via QR if Onshape/Taobao logged out.

## Change and rollback plan

| Trigger | Rollback action | Verify restored state | Irreversible boundary |
|---|---|---|---|
| bridge fails after deploy | `tools/wsl_bridge_ctl.sh restart` (old synced copy) | probe ok | none (profile persists) |
| wrong config | restore `config.local.toml` backup | status ok | none |

## Operation record and cleanup

Record time, actor, commands, versions, output/effects, and remaining risk.
Remove temporary permissions after the operation.
