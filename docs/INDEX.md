# Documentation index

## Current authorities

| Concern | Authority | Evidence/source |
|---|---|---|
| Current behavior | implementation and tests | `server.py`, `run_mcp_stdio.py`, `src/`, `tests/` |
| Public contract (13 tools) | `README.md` tool table + live schemas | `server.py` registrations |
| Ordinary stdio architecture | `architecture/MCP.md` | `run_mcp_stdio.py`, `tests/test_stdio_architecture.py` |
| Verification selection | `verification/MATRIX.md` | `pyproject.toml`, `tests/` |
| Deployment and external bridge registration | `operations/MCP_RUNBOOK.md` | ordinary stdio entry + `dsh/` example |
| User calling boundary | `usage/MCP_CONSUMER.md` | tool schemas and runtime policy |

## Read by role

| Role | Start here | Do not preload |
|---|---|---|
| Developer | `development/START.md` | usage, operations, history unless needed |
| Maintainer | `development/START.md` + `architecture/MCP.md` | production usage/operations |
| Reviewer | `verification/MATRIX.md` + target diff | production state |
| User (MCP consumer) | `usage/MCP_CONSUMER.md` | dev/architecture/operations |
| Operator | `operations/MCP_RUNBOOK.md` | development/history |

## Read by task

| Need | First read | Next exact detail |
|---|---|---|
| Change source | `development/START.md` | one `src/*` module + matching tests |
| Understand a boundary | `architecture/MCP.md` | ordinary entry or module source |
| Select validation | `verification/MATRIX.md` | exact `pytest` target |
| Use a public capability | `usage/MCP_CONSUMER.md` | README tool table / live schema |
| Install/recover MCP | `operations/MCP_RUNBOOK.md` | external bridge documentation when cross-host |
| Trace retired project relay | `history/` | historical pages only; commands unsupported |

## Non-authoritative collections

| Area | Contains | Must link back to |
|---|---|---|
| `docs/history/` | retired relay architecture/runbook/comparison | current architecture/runbook |
| `output/` | exports, logs, caches | contract or verification |
| `NOTES.md` | working history | current authority/docs |
| `REFACTOR_PLAN.md` | plans | no claim of current behavior |
