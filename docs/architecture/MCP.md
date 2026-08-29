# taobao-mcp architecture

Status: verified
Scope: repository
Evidence: `server.py` (13 tools), `src/` parsers, `tools/` bridge, `tests/`

## System context

Implemented capability: a single-tenant MCP server that finds Taobao/Tmall
products, reads per-SKU prices and variant-linked reviews, stages cart lines,
tracks orders, and drafts seller messages — all human-paced, no auto-buy/auto-send.
Actors: WSL agents (DSH/Codex) via stdio; Windows host runs the real Chrome
engine. Exclusions: payment, checkout, address selection, forwarding/logistics.

## Runtime topology

| Unit/process/package | Responsibility | Lifecycle owner | Communicates through |
|---|---|---|---|
| WSL MCP facade | stdio↔TCP relay, complete MCP forwarding | WSL agent spawns it | stdio (client) → loopback TCP |
| Windows bridge | spawn `run_mcp_stdio.py`, single-client lock | Windows (persistent) | loopback TCP |
| Windows MCP engine | 13 tools, dispatch, browser session | Windows persistent | stdio child of bridge |
| Chrome/Edge engine | real browser + persistent profile | Windows engine | Playwright |

Detailed WIN-WSL mapping: see `bridge/ARCHITECTURE.md`.

## Module boundaries

| Module | Owns | Does not own | Entrypoint | Contract |
|---|---|---|---|---|
| `server.py` | tool registration/schemas/handlers | parsing/browser details | `server.py` | tool schemas |
| `src/extract/` | search/product/reviews/orders/… parsers | protocol | `src/extract/*.py` | `src/models.py` |
| `src/browser/` | Chrome session, pacing, scroll | business rules | `src/browser/session.py` | config |
| `tools/` | WSL relay + Windows launcher/self-heal | tool logic | `tools/*` | `tools/wsl_bridge_ctl.sh` |

## Dependency direction

```text
MCP client -> mcp_tcp_bridge.py -> bridge_server.py -> server.py -> src/* -> Playwright/Chrome
```

- Allowed: WSL facade → Windows engine; server → src extraction; src → browser.
- Forbidden: Windows engine importing WSL-only deps; src modules owning the MCP protocol.

## Trust and side-effect boundaries

| Boundary/action | Input identity/trust | Effect | Gate/failure rule |
|---|---|---|---|
| MCP client → WSL facade | local stdio | read/forward | protocol-clean stdout |
| WSL → Windows TCP | loopback only | read/forward | bind 127.0.0.1 only |
| Taobao crawl | logged-in profile | read/write favorites/cart | pacing + daily caps + confirm |
| Cart add / message send | confirmed by human | write | preview then `confirm=true` |

## Data and configuration ownership

| Item | Authority/owner | Lifecycle | Consistency/retention rule |
|---|---|---|---|
| `config.toml` | repo | tracked | defaults |
| `config.local.toml` | machine | untracked | machine overrides |
| Chrome profile `user_data/` | Windows engine | persistent | never commit |
| `output/` exports/logs | local | untracked | never commit |

## Failure and recovery model

Bridge restart/self-heal is windowless (`tools/wsl_bridge_ctl.sh`, `tools/windows/`).
Client reconnect does not reset the persistent browser profile. See
`../operations/MCP_RUNBOOK.md` and `DSH_WSL_BRIDGE.md`.

## Invariants

- WSL facade is stdlib-only and never holds Windows state.
- Windows engine lazy-loads Playwright (no GUI dep at import).
- One persistent Chrome profile has a single owner at a time.
- MCP stdout carries only JSON-RPC; diagnostics go to stderr/logs.
- Mutations require explicit human confirmation.

## Unknowns and decisions

- Unknown: none blocking current scope.
- Decision: see `DSH_WSL_BRIDGE.md` (bridge rationale) and `bridge/ARCHITECTURE.md`.
