<!-- agent-project-guides:v3:start -->
## Project governance routing

Project ID: `taobao-mcp`; variant: `shared-runtime.pinned`; pinned release: `3.0.3` / `sha256:f3dc0ca9cd50d27deac2b4e9c063d243dd3ce20127edc88d9f8b4c3aac4bd603`; manifest: `sha256:50a5769025fcb44d6ca35d367fbfd2e2259d8e57a449a7137c8ae2ed4a02dd17`.

Before work, run `apg context --target . --task <current-task> --format context` and use only the returned governance content. Resolve any ambiguity before protected work. The shared CLI and exact packed digest are runtime dependencies; missing content fails explicitly and never falls back to `latest`. Returned sources are intended context and do not prove model-effective context.
<!-- agent-project-guides:v3:end -->

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
