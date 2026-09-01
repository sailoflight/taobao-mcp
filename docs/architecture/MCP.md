# taobao-mcp architecture

Status: verified
Scope: repository
Evidence: `server.py`, `run_mcp_stdio.py`, `src/`, `tests/`

## System context

The repository implements a single-tenant ordinary stdio MCP that finds
Taobao/Tmall products, reads per-SKU prices and reviews, stages confirmed cart
lines, tracks orders, and handles confirmed seller messages. Payment, checkout,
address selection, and autonomous sending are excluded.

The MCP process and its visible Chrome/Edge profile run on the same host.
Cross-host transport is optional deployment infrastructure supplied by an
independently installed bridge. This repository owns no cross-host relay,
listener, registry, service launcher, scheduled task, or fixed port.

## Runtime topology

```text
MCP client or external adapter
  -> stdin/stdout
  -> run_mcp_stdio.py
  -> server.py (13 tools + canonical runtime policy)
  -> src/*
  -> Playwright -> visible Chrome/Edge + persistent user_data profile
```

| Unit | Responsibility | Lifecycle owner |
|---|---|---|
| `run_mcp_stdio.py` | force stdio mode and exec the server | MCP client/adapter |
| `server.py` | identity, policy, 13 schemas/handlers, process-wide serialization | MCP process |
| `src/extract/` | search/product/review/order parsers | business module |
| `src/browser/` | browser session, pacing, scroll, persistent profile | MCP process |
| `dsh/` | generated runtime-policy companion and external-bridge client example | client compatibility |

## Boundaries

- `server.py` may call `src/*`; extraction/browser modules do not own MCP protocol.
- Browser/Playwright imports remain lazy so initialize, tools/list, and local
  validation do not require a launched browser.
- stdout contains JSON-RPC only; diagnostics use stderr or module-owned logs.
- One profile has one MCP process owner. A deployment must not launch two
  ordinary MCP processes against the same `user_data` directory.
- An external bridge may supervise this command, but command/registry/link
  semantics belong to that bridge and are not duplicated here.

## Trust and effects

| Action | Effect | Gate |
|---|---|---|
| initialize/tools/list/status | local/protocol or read-only state | no business mutation |
| Taobao crawl | account/network reads; some flows may touch favorites | pacing, caps, human verification |
| Cart add / seller reply | account write | preview plus explicit confirmation |
| Payment / checkout / address | prohibited | never implemented |

## Data ownership

| Item | Owner | Retention |
|---|---|---|
| `config.toml` | repository defaults | tracked |
| `config.local.toml` | MCP host | ignored, preserve across deploys |
| `user_data/` | browser profile on MCP host | ignored, single owner, preserve |
| `output/` | exports/logs/caches | ignored, preserve as required |
| runtime policy | `src/runtime_prompt.py` + generated `dsh/` companion | one revisioned generation |

## History

The retired project-owned WIN-WSL relay rationale is preserved under
`docs/history/`. Those pages are non-authoritative and their commands are
unsupported. Current deployment and recovery authority is
`docs/operations/MCP_RUNBOOK.md`.
