# Historical WIN-WSL MCP architecture comparison

> **Retired comparison.** It records the former project-owned relays. Their
> executables were intentionally removed; this page is non-authoritative and its
> commands must not be used for deployment. See `../operations/MCP_RUNBOOK.md`.

Status: retired historical review
Scope: `taobao-mcp` and sibling `onshapescript`

## Shared baseline

Both projects use a stdlib-only WSL stdio facade, WSL2 mirrored loopback TCP,
a Windows-owned visible browser/profile, protocol-clean stdout, windowless bridge
launchers, one browser-profile owner, offline guards, and a live MCP probe.

## Material differences

| Concern | taobao-mcp | onshapescript | Decision |
|---|---|---|---|
| Windows MCP lifecycle | Persistent listener; one FastMCP child per client | Persistent in-process MCP dispatch | Keep different: Taobao login cookies survive browser restart; Onshape login must survive client reconnect |
| Browser lifecycle | Connection-owned async Playwright browser, cleanly closed by FastMCP lifespan | Process-owned sync Playwright browser survives reconnect | Preserve domain-specific ownership |
| Protocol body | MCP SDK/FastMCP, fixed 13-tool surface | Custom newline JSON-RPC dispatcher, connection-scoped dynamic tool views | Do not replace either body solely for uniformity |
| Client state | Fresh SDK session per connection | Fresh `McpConnection` view state over shared Engine/browser | Both isolate connection state |
| Runtime policy | Canonical revisioned Python source plus generated DSH companion | Same pattern, with a broader role/tool-discovery policy | Adopted Onshape's generation model in Taobao |
| DSH startup | Health → start Windows → poll → exec relay | Same orchestration pattern | Adopted Onshape's entry pattern in Taobao |
| Health check | One-byte preface answered before the MCP lock; no child spawn | Bare TCP reachability check | Taobao's non-spawning check is safer for a child-per-client Engine |
| Window behavior | Listener uses `pythonw`; stdio child uses `python.exe + CREATE_NO_WINDOW` | Single listener/Engine uses `pythonw` | Both are windowless without sacrificing Taobao stdio pipes |
| Probe generation check | Exact runtime-policy revision required | Exact revision check added to the live probe | Both now fail on Engine/client generation drift |
| Tool exposure | Small fixed public surface | Static/profile/semantic/dynamic views plus catalog | Onshape complexity is domain-driven; not needed for 13 Taobao tools |

## Improvements applied

### Onshape patterns brought to Taobao

- Canonical `src/runtime_prompt.py` with a bounded Production/User and
  Production/Operator policy, tied to `src/identity.py`.
- Generated, namespaced DSH companion with a `--check` drift gate and two-row
  deployment example.
- `tools/mcp_bridge_entry.sh` to start the Windows listener when health is down,
  poll readiness, and then exec the WSL facade.
- Live probe validation of the exact deployed prompt revision.
- Explicit Engine, browser, profile, and connection lifecycle documentation.

### Taobao patterns brought to Onshape

- The Onshape live probe now checks `initialize.instructions` against the current
  canonical revision before listing or calling tools.
- An offline test rejects missing and stale runtime-policy generations.

### Additional Taobao bridge hardening

- `status` no longer opens a normal MCP connection or spawns a FastMCP/browser
  child; it uses a one-byte health preface before `_ACTIVE_LOCK`.
- Windows child processes keep reliable stdio under `python.exe` while
  `CREATE_NO_WINDOW` suppresses console windows.
- FastMCP lifespan closes a started browser cleanly on client disconnect.
- Relay early-close is non-zero and diagnostic, so a rejected second client is
  not mistaken for normal shutdown.
- Relay pending client data is capped at 8 MiB.
- Accepted MCP sockets enable keepalive; Windows uses bounded keepalive timing
  when supported.

## Intentional non-merges

Moving Taobao to Onshape's persistent in-process dispatcher would discard the
MCP SDK body and couple async FastMCP state to browser persistence that Taobao
does not require. Moving Onshape to Taobao's per-client child would close the
browser and lose the Onshape web login on each reconnect. These differences are
requirements, not missing symmetry.

## Remaining verification and risk

- Offline tests cover policy generation, facade byte forwarding, health preface,
  windowless launch structure, schemas, and domain logic.
- The updated Windows Taobao listener, DSH companion/profile, and Onshape live
  probe still require deployment sync and read-only Windows smoke verification.
- Loopback TCP is local-only but unauthenticated in both projects; confirmation
  gates remain the primary mutation boundary.
- Onshape's Windows `_serve_client` framing and single-client rejection still
  lack focused offline socket tests.
