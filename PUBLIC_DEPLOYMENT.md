# Public deployment

This project supports two independent MCP transports from the same `server.py`:

- Local Codex: stdio through `.mcp.json` (the default).
- Public ChatGPT plugin backend: authenticated Streamable HTTP at `/mcp`.

The server is deliberately single-tenant because one persistent Taobao browser
profile backs all tools. Restrict OAuth to exactly the account that owns that
profile. Do not scale the process beyond one replica.

## Non-negotiable local-data boundary

Never upload, publish, copy, mount into a hosted image, or commit `user_data/`.
It contains the browser profile, cookies, and Taobao login state. `.gitignore`
and `.dockerignore` both exclude it. `output/`, `.env*`, local logs, the private
sourcing profile, and raw page captures are excluded too.

The server enforces this boundary at startup: `browser.user_data_dir` must resolve
inside this checkout's `user_data/` directory. A system-installed Chrome or Edge
executable is allowed, but its normal operating-system profile is never reused.

For this browser-driven implementation, run the MCP process interactively on
the machine that owns the browser profile. Put a stable HTTPS reverse proxy in
front of `127.0.0.1:8000`; the proxy may be hosted separately only if it securely
forwards to this machine. The human must be able to see the browser opened by
`taobao_initialize_login` and handle QR login or captchas.

## 1. Configure OAuth and the public URL

Use an OAuth 2.1 provider that issues signed JWT access tokens and publishes an
OAuth/OIDC discovery document plus JWKS. Copy `.env.public.example` to a secret
configuration outside this repository and set:

- `MCP_PUBLIC_URL`: exact stable HTTPS endpoint, ending in `/mcp`.
- `OAUTH_ISSUER_URL`: token issuer from the provider's discovery document.
- `OAUTH_JWKS_URL`: provider JWKS URL.
- `OAUTH_AUDIENCE`: expected access-token audience, normally `MCP_PUBLIC_URL`.
- `OAUTH_REQUIRED_SCOPES`: normally `taobao:mcp`.
- `OAUTH_ALLOWED_SUBJECTS`: the one OAuth `sub` allowed to use this Taobao profile.

Public mode refuses to start if the URL, issuer, JWKS URL, or subject allowlist
is missing. Invalid, expired, wrong-audience, wrong-scope, or non-allowlisted
tokens are rejected without logging the token.

## 2. Start Streamable HTTP

Load the environment into the current shell, then run with the project Python:

```bash
python run_mcp_http.py
```

The local listener is:

- MCP: `http://127.0.0.1:8000/mcp`
- Liveness: `http://127.0.0.1:8000/healthz`
- OAuth protected-resource metadata: derived by FastMCP from `MCP_PUBLIC_URL`
- Domain verification: `/.well-known/openai-apps-challenge`

Terminate the process and run `python run_mcp_stdio.py` to return to stdio.
Each cloned machine generates its own Git-ignored `.mcp.json` by running
`python configure_codex.py` once after installing dependencies.

## 3. Put stable HTTPS in front

Configure your domain/reverse proxy so that:

- `https://your-domain.example/mcp` streams to `http://127.0.0.1:8000/mcp`
  without response buffering.
- `/.well-known/oauth-protected-resource*`, `/healthz`, and
  `/.well-known/openai-apps-challenge` reach the same process.
- TLS certificates renew automatically.
- The proxy has request timeouts long enough for MCP streaming.

Do not use a temporary forwarding URL for public submission.

## 4. Verify before submission

Use MCP Inspector in Streamable HTTP mode against the public `/mcp` URL. Confirm:

1. An unauthenticated request receives an OAuth challenge.
2. OAuth discovery and authorization-code + PKCE complete successfully.
3. Initialization lists exactly 12 `taobao_*` tools.
4. Read/write/open-world annotations match actual behavior.
5. Preview calls do not write; `confirm=true` performs only the documented cart
   add or seller reply.
6. Health and application logs contain no tokens, cookies, tool results, order
   details, messages, or browser-profile paths.

## 5. Submit through OpenAI Platform

Developer Mode is not required for the final public submission path, but the
publisher still needs Platform plugin-submission access and verified identity.
Submit as **With MCP**, use a Universal URL, provide the production `/mcp` URL,
OAuth details and reviewer credentials, then complete domain verification and
Scan Tools.

Prepare public website, support, privacy-policy, and terms URLs, production logo,
five positive test cases, three negative test cases, country availability, and
release notes. Do not add invented URLs or IDs to `.codex-plugin/plugin.json`;
add the real values only when those public resources exist.

The tracked `.app.json` is deliberately left as `{"apps": {}}` during development.
It must stay empty until the user explicitly authorizes adding the real registered
connection ID. Never commit a made-up `plugin_asdk_app_...` placeholder.
