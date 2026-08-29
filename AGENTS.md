<!-- agent-project-guides:routing:start -->
## Agent routing

Package adaptation: status=adapted; package_revision=1.4.3; verified_at=2026-08-29T00:59:36Z; scope=repo; reason=none

1. Trigger is active only if this injected root has both managed marker names `agent-project-guides:adapter-trigger:start` and `agent-project-guides:adapter-trigger:end`. Routing/state and `pending/stale` are not triggers; bootstrap is template-only. If absent, never re-read/search; route now.
2. Before pwd/list/glob/read, an assigned compatible role/mode wins: content-grep its exact quoted `id` or literal label across `agent-project-guides/routing/*.roles.jsonl`; use one record. No fuzzy regex, discovery, planes/full registries, re-asking or rediscovery. Unmatched labels are unresolved: ask, never infer.
3. Only when unassigned read two-line `agent-project-guides/routing/planes.jsonl`; if unclear use the structured question tool (DSH: `ask_user_question`) and wait.
4. In that registry grep one exact role. If unclear, use the same tool and wait before its guide.
5. Blocking questions use stable IDs, 2–4 exclusive choices and impacts, not prose lists; free text only if choices mislead; ask directly only without a tool.
6. Resolve record `guide`/`procedure_by_mode` under `agent-project-guides/`, never relative to registry/cwd. Read only those paths; a failure is package integrity, not permission to glob.
7. Without a trigger, ask adapt-now vs continue only if intent is unclear and state is not `adapted`; explicit adaptation needs no question. Installer owns `pending/stale`; initialize/readapt records `partial/adapted/blocked`.
8. Roles never grant production credentials, real data, cost or destructive actions.

Subagents receive explicit role/mode, scope, writable paths, environment/data permissions and deliverable; missing/conflicting authority goes to parent/captain, never end user or self-expansion.
<!-- agent-project-guides:routing:end -->

# Repository agent instructions

## Project scope

taobao-mcp is a **local, human-paced MCP server** (13 parameterized tools) for
Taobao/Tmall sourcing. Runtime form: WIN-WSL bridge — a pure-stdlib **WSL
facade** relays MCP over loopback TCP to a **Windows engine** that drives a
real Chrome/Edge window with a persistent login profile.

## Repository map

| Area | Owns | Start at |
|---|---|---|
| MCP entry & 13 tools | tool registration, schemas, handlers, protocol | `server.py` |
| Extraction & browser | per-SKU pricing, reviews, cart/tracking, session | `src/` |
| WIN-WSL bridge | WSL relay + Windows launcher/self-heal scripts | `tools/` |
| Tests & guards | offline parsers, MCP contract, bridge structure | `tests/` |
| Docs | architecture, usage, runbook, verification | `docs/INDEX.md` |

## Instruction scope

- This root `AGENTS.md` applies repository-wide.
- A nearer directory `AGENTS.md` adds only local differences for its subtree.
- Project details route through `docs/INDEX.md`; do not preload the doc tree.

## Global invariants

- Single persistent Chrome profile → **single owner** at a time (one MCP client).
- Never auto-buy, auto-checkout, or auto-send; mutations require explicit confirm.
- WSL facade stays **stdlib-only**; Windows engine lazy-loads Playwright.
- MCP protocol stdout carries only JSON-RPC; diagnostics go to stderr/logs.
- Preserve user and parallel-agent changes outside the authorized scope.

## Risk gates

| Action | Default | Required authorization/evidence |
|---|---|---|
| Taobao network/live crawl | read-only | pacing + daily caps; login requires human QR |
| Cart add / seller reply / config set | forbidden | preview then `confirm=true` |
| Windows bridge restart | read-only | `tools/wsl_bridge_ctl.sh` + human OK |
| Pay / checkout / address / destructive | forbidden | never |

## Authoritative entrypoints

| Need | Authority |
|---|---|
| Development setup and commands | `docs/development/START.md` |
| Current boundaries | `docs/architecture/MCP.md` + `bridge/ARCHITECTURE.md` |
| Change verification | `docs/verification/MATRIX.md` |
| Role/task routing | `docs/INDEX.md` |
