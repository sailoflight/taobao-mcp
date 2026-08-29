# Documentation index

## Current authorities

| Concern | Authority | Evidence/source |
|---|---|---|
| Current behavior | implementation and tests | `server.py`, `src/`, `tests/` |
| Public contract (13 tools) | `README.md` tool table + live `tools/list` schemas | `server.py` registrations |
| WIN-WSL bridge mapping | `bridge/ARCHITECTURE.md` | `tools/`, `tests/test_bridge_architecture.py` |
| Verification selection | `verification/MATRIX.md` | `pyproject.toml`, `tests/` |
| Deployment/runtime | `DSH_WSL_BRIDGE.md`, `operations/MCP_RUNBOOK.md` | `tools/windows/` |
| Historical rationale | `NOTES.md`, `REFACTOR_PLAN.md` | git history |

## Read by role

| Role | Start here | Do not preload |
|---|---|---|
| Developer | `development/START.md` | usage, operations, evidence unless needed |
| Maintainer | `development/START.md` + `architecture/MCP.md` | production usage/operations |
| Reviewer | `verification/MATRIX.md` + target diff | package adaptation, production docs |
| User (MCP consumer) | `usage/MCP_CONSUMER.md` | dev/architecture/operations |
| Operator | `operations/MCP_RUNBOOK.md` | development, roadmap |

## Read by task

| Need | First read | Next exact detail |
|---|---|---|
| Change source | `development/START.md` | one `src/*` module + matching tests |
| Understand a boundary | `architecture/MCP.md` or `bridge/ARCHITECTURE.md` | module contract/ADR |
| Select validation | `verification/MATRIX.md` | exact `pytest` target |
| Use a public capability | `usage/MCP_CONSUMER.md` | README tool table / live schema |
| Operate/recover bridge | `operations/MCP_RUNBOOK.md` | `tools/wsl_bridge_ctl.sh` |

## Non-authoritative collections

| Area | Contains | Must link back to |
|---|---|---|
| `output/` | exports, logs, caches | contract or verification |
| `NOTES.md` | working history | current authority/docs |
| `REFACTOR_PLAN.md` | plans | no claim of current behavior |
