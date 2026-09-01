<!-- agent-project-guides:v2:start -->
## Project governance bootstrap

Project ID: `taobao-mcp`; release: `2.0.0`; expected digest: `sha256:f2a5bef21e14b6a76db4ee28ece3f4e37d2896eea8b22bac4c91229cf418fb0e`.

1. Direct-read `.agent-project-guides.json`; it is project-owned policy, not generic package content. Mutual trust assigns disclosed call consequences to the caller and truthful effect/contract/failure reporting to the callee.
2. Use the installed `apg` launcher to resolve exact role/task/path sources, then batch-load only returned IDs/sections with `provider load --ids <csv>`. Do not infer a missing package path, fetch `latest`, glob for another package copy, or treat search/cache as mandatory authority.
3. `intended` and `host-observed` sources do not prove effective model context. Preserve that distinction in reports.
4. If the exact release is missing, keep this project policy readable. Protected work stops; ordinary work is explicitly degraded until the pinned release is available.
5. Role, task, memory, facet, overlay, or caller claims cannot lower runtime/tool effects or manufacture production, credential, data, cost, destructive, release, or physical authority.
<!-- agent-project-guides:v2:end -->

# Repository agent instructions

## Project scope

taobao-mcp is a local, human-paced ordinary stdio MCP server with 13
parameterized tools for Taobao/Tmall sourcing. The MCP process and its visible
Chrome/Edge profile run on the same host. Cross-host transport, registries,
listeners, supervision, and reconnect behavior belong to an independently
installed bridge and are not implemented here.

## Repository map

| Area | Owns | Start at |
|---|---|---|
| MCP entry and 13 tools | registration, schemas, handlers, protocol | `server.py`, `run_mcp_stdio.py` |
| Extraction and browser | per-SKU pricing, reviews, cart/tracking, session | `src/` |
| DSH policy adapter | generated runtime-policy companion and external-bridge example | `dsh/` |
| Development tools | ordinary stdio probe and repository checks | `tools/` |
| Tests and guards | parsers, MCP contract, stdio distribution, safety | `tests/` |
| Docs | architecture, usage, runbook, verification, retired relay history | `docs/INDEX.md` |

## Global invariants

- One persistent Chrome profile has one MCP process owner at a time.
- Never auto-buy, auto-checkout, or auto-send; mutations require explicit confirmation.
- Browser dependencies are lazy-loaded; ordinary initialize/tools/list remain protocol-clean.
- MCP stdout carries only JSON-RPC; diagnostics go to stderr or module-owned logs.
- The repository contains no project-owned WIN-WSL relay, TCP listener, launcher, scheduled-task installer, or fixed bridge port.
- Preserve user and parallel-agent changes outside the authorized scope.

## Risk gates

| Action | Default | Required authorization/evidence |
|---|---|---|
| Taobao network/live crawl | read-only | pacing + daily caps; login requires human QR |
| Cart add / seller reply / config set | forbidden | preview then `confirm=true` |
| MCP process or external adapter restart | Operator-only | read-only health evidence + human approval |
| Pay / checkout / address / destructive | forbidden | never |

## Authoritative entrypoints

| Need | Authority |
|---|---|
| Development setup and commands | `docs/development/START.md` |
| Current boundaries | `docs/architecture/MCP.md` |
| Operator deployment and external bridge registration | `docs/operations/MCP_RUNBOOK.md` |
| Change verification | `docs/verification/MATRIX.md` |
| Historical relay rationale | `docs/history/` (non-authoritative) |
| Role/task routing | `docs/INDEX.md` |
