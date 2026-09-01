# taobao-mcp ordinary stdio runbook

Audience: Production / Operator
Scope: MCP process, browser/profile ownership, DSH policy adapter, and optional external bridge registration.

## Preconditions

- A deployment copy on the browser host with Python 3.11+ and dependencies installed.
- A visible Chrome/Edge configured by `config.toml` plus ignored `config.local.toml`.
- Preserved ignored `user_data/chrome_profile`, `output/`, and local configuration.
- Exactly one MCP process owner for that profile.

## Ordinary entry

Run on the host that owns the profile:

```powershell
cd C:\MCP\taobao-mcp
.\.venv\Scripts\python.exe -B run_mcp_stdio.py
```

The process speaks MCP JSON-RPC on stdin/stdout. It does not open a TCP port or
install a task/service. Client EOF terminates the process and releases browser
resources while leaving the profile on disk.

## Optional cross-host adapter

For WSL clients, register the ordinary command above with an independently
installed `win-wsl-mcp-bridge` using id `taobao`, `cwd` set to the Windows
checkout, and `multiProcessAllowed=false`. Configure DSH from
`dsh/cordis.patch.yml.example`. The external bridge owns loopback listeners,
registry, supervision, redacted Registry MCP, reconnect, and rollback. Never
copy those mechanisms into this repository.

## Change procedure

1. Record environment, identity, current process/profile owner, user/data impact,
   recovery point, stop conditions, and explicit approval.
2. Verify the new source offline and run `tools/mcp_probe.py` on its target host.
3. Stop the client/adapter so the ordinary MCP and browser exit cleanly.
4. Preserve `config.local.toml`, `user_data/`, `output/`, and any local secrets.
5. Replace source/dependencies without creating a second profile owner.
6. Start the client/adapter and verify initialize, exact tool list, runtime-policy
   companion, and `taobao_session(action=status)` only.
7. Stop on captcha, profile lock, ambiguous writes, unexpected browser pages, or
   any request to pay/checkout/change address.

## Health

Healthy means:

- one ordinary MCP process generation owns the profile;
- initialize returns the expected identity and runtime-policy revision;
- tools/list exposes the 13 parameterized tools;
- session status is sane without opening a second browser;
- stdout is protocol-clean;
- any external bridge reports its own nodes/registry healthy.

## Recovery

Restore the deployment copy and ignored local state from the established
recovery point, then restart only one client/adapter. Do not delete a profile as
cleanup. If the external bridge fails, follow its runbook; do not reactivate the
retired project relay preserved under `docs/history/`.
