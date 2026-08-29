# Development start

## Supported environments

| Runtime/platform | Supported range | Evidence |
|---|---|---|
| Python | >= 3.11 (dev 3.12) | `pyproject.toml` requires-python |
| Windows host (Engine) | Windows 11 22H2+, WSL2 mirrored networking | `DSH_WSL_BRIDGE.md` |
| Browser | Google Chrome or Edge via `config.local.toml` | `config.toml [browser]` |

## Bootstrap

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

State: run from repository root. Network not required for offline tests; live
Taobao crawl needs a logged-in Chrome profile on Windows. Never put credentials
in the repo (`config.local.toml`, `user_data/`, `output/` are gitignored).

## Entrypoints

| Purpose | Command/path | Scope/output | Side effects |
|---|---|---|---|
| Run (stdio MCP) | `.venv/bin/python server.py` | MCP server, 13 tools | opens Chrome only on login tool |
| Fast check | `.venv/bin/python -m py_compile server.py tools/*.py` | syntax | none |
| Test | `.venv/bin/python -m pytest -q` | offline unit/contract/guard tests | none |
| WSL→Windows bridge probe | `python3 tools/mcp_probe.py` | full MCP handshake + `taobao_session(status)` | live Windows bridge must be up |
| Windows bridge start/restart | `tools/wsl_bridge_ctl.sh start|restart|status` | windowless bridge control | starts/restarts Windows process |

## Task routing

| Change area | Contract/entry | Matching verification |
|---|---|---|
| MCP tools / server.py | `server.py`, `src/` | `../verification/MATRIX.md#mcp-contract` |
| Extraction parsers | `src/extract/*.py` | `../verification/MATRIX.md#offline-parsers` |
| WIN-WSL bridge | `tools/mcp_tcp_bridge.py`, `tools/bridge_server.py` | `../verification/MATRIX.md#bridge-structure` |

## Configuration, state, and data

| Item | Owner/source | Committed? | Local/test rule |
|---|---|---|---|
| `config.toml` | repo | yes | defaults |
| `config.local.toml` | machine | no (gitignored) | executable_path / mi_id |
| `user_data/` Chrome profile | Windows engine | no | never commit |
| `output/` exports/logs/caches | local | no | never commit |

## Generated artifacts

| Output | Source | Regenerate | Check drift |
|---|---|---|---|
| `agent-project-guides/` state | `install.sh` | `./agent-project-guides/scripts/install.sh check` | `check-update` |

## Common failures

| Symptom | Cheapest check | Exact detail/runbook |
|---|---|---|
| bridge NOT reachable | `tools/wsl_bridge_ctl.sh status` | `../operations/MCP_RUNBOOK.md` |
| 0 tools / no response | `python3 tools/mcp_probe.py` | `DSH_WSL_BRIDGE.md` 故障排查 |
| profile lock "正在使用" | check stray Chrome/python | `../operations/MCP_RUNBOOK.md` |
