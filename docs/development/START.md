# Development start

## Supported environments

| Runtime/platform | Supported range | Evidence |
|---|---|---|
| Python | >= 3.11 (dev 3.12) | `pyproject.toml` |
| MCP host | host able to run Python and the configured visible Chrome/Edge | ordinary stdio entry + browser config |
| Cross-host client | optional independently installed bridge | `../operations/MCP_RUNBOOK.md` |

## Bootstrap

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Run from the repository root. Network is not required for offline tests. Live
Taobao work needs a logged-in browser profile on the MCP host. Never commit
`config.local.toml`, `user_data/`, `output/`, credentials, cookies, or captures.

## Entrypoints

| Purpose | Command/path | Side effects |
|---|---|---|
| Ordinary stdio MCP | `.venv/bin/python run_mcp_stdio.py` | browser opens only when requested |
| Direct server | `.venv/bin/python server.py` | transport selected by environment |
| Offline stdio probe | `.venv/bin/python tools/mcp_probe.py` | status only; no login/crawl |
| Syntax | `.venv/bin/python -m py_compile server.py run_mcp_stdio.py tools/mcp_probe.py dsh/*.py` | none |
| Tests | `.venv/bin/python -m pytest -p no:cacheprovider -q` | offline |
| Runtime companion | `.venv/bin/python dsh/build_runtime_prompt_companion.py --check` | none in check mode |

## Task routing

| Change area | Contract | Matching verification |
|---|---|---|
| MCP tools / server | `server.py`, `src/` | tool/contract tests |
| Extraction parsers | `src/extract/*.py` | matching parser tests |
| Browser/session | `src/browser/` | browser/config tests |
| Stdio/client policy | `run_mcp_stdio.py`, `dsh/` | stdio architecture + runtime-prompt tests |
| Deployment adapter | external bridge project | do not add relay code here |

## Local data

| Item | Rule |
|---|---|
| `config.toml` | tracked defaults |
| `config.local.toml` | ignored machine overrides |
| `user_data/` | ignored browser/login state; single process owner |
| `output/` | ignored exports/logs/caches |

## Common failures

| Symptom | First check | Authority |
|---|---|---|
| initialize/tools missing | run `tools/mcp_probe.py` on MCP host | verification matrix |
| browser cannot launch | executable/profile config and profile owner | Operator runbook |
| external client cannot connect | external bridge registry/node health | bridge's own runbook |
| profile lock | stop duplicate MCP/browser process | Operator runbook |
