# Taobao Sourcing Assistant

A **local, human-paced MCP server** that removes the drudgery of sourcing products
on Taobao/Tmall. You keep all judgment (search intuition, buy decisions, sending
supplier messages); the tool drives a real Chrome window to extract — for every
product — **a price for every SKU variant**, specs, images, and **reviews linked to
the variant bought**, then tabulates it into a comparison spreadsheet. Ships with a
ChatGPT/Codex **Skill** (sourcing playbook) and **Chinese supplier-message templates**
(drafted by the assistant, sent via confirm-then-send — you approve each message).

> Built on the QR-login + persistent-session approach of `JeremyDong22/taobao_mcp`,
> rebuilt as **12 FastMCP tools** with embedded-data + DOM extraction (mtop interception
> kept as a fallback): search, per-SKU pricing, variant-linked reviews, xlsx export,
> **gated cart staging** (adds via the `mtop.trade.addBag` API — works on Taobao *and*
> Tmall), **confirm-then-send seller messaging**, and a **daily order-tracking + 取件码
> pickup-code digest** — plus a **vendor-joined full picture** and a **full-history
> landed-cost inventory export**, a captcha human-handoff, and anti-detection pacing.

## Local Codex and public ChatGPT modes

This repository keeps two independent entrypoints:

- **Local Codex:** `.codex-plugin/plugin.json` loads the skill and `.mcp.json`
  starts the existing stdio server. This remains the default and needs no public
  service.
- **Public ChatGPT plugin backend:** set `MCP_TRANSPORT=streamable-http` and run
  the authenticated HTTP server behind a stable HTTPS domain at `/mcp`.

An account without Developer Mode cannot register a private MCP connection merely
by typing `@taobao-mcp`. Its ChatGPT path is a reviewed, published MCP-backed plugin.
The repository includes `.app.json` only as an intentionally empty development-state
entry (`{"apps": {}}`). It is wired into the plugin manifest but connects to nothing.
Do not add a placeholder or real `plugin_asdk_app_...` ID unless the user explicitly
authorizes it after a real MCP connection has been registered.

See [`PUBLIC_DEPLOYMENT.md`](PUBLIC_DEPLOYMENT.md) and `.env.public.example` for
the public endpoint, OAuth, domain-verification, testing, and submission checklist.

### Set up another machine

Machine paths are never committed. After cloning, create the virtual environment,
install dependencies, put any browser-path override in the Git-ignored
`config.local.toml`, then generate that clone's local Codex wiring:

```bash
python configure_codex.py
```

This writes a Git-ignored `.mcp.json` containing absolute paths for the current
machine. Run `python run_mcp_stdio.py` for direct stdio or
`python run_mcp_http.py` for authenticated public HTTP.

Before committing or pushing a clone, verify the local-data boundary:

```bash
python verify_git_safety.py
git status --short --ignored
```

The repository is intentionally initialized without a commit or remote. After
reviewing the files yourself, publish manually:

```bash
git add .
python verify_git_safety.py
git commit -m "Initial taobao-mcp import"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

`user_data/` is a strict local-data boundary. It contains browser profiles,
cookies, and login state; it is ignored by version control and must never be
uploaded, published, attached, or copied into a distributable plugin package.
The code also rejects any `browser.user_data_dir` outside this checkout's
`user_data/` directory. Chrome or Edge itself may be installed system-wide, but
this MCP never opens that browser's normal operating-system user profile.
Generated `output/` files are local-only as well unless the user explicitly asks
to share a specific export.

The public endpoint carries MCP requests and tool results needed for the current
ChatGPT conversation, but deployment artifacts must never contain `user_data/`.
The browser profile, cookies, and login state remain on the machine running the
interactive browser process.

## Scope — it does four things
**Find** legitimate products · **add to cart** · **communicate with sellers** (you confirm
each message) · **track orders** (+ 取件码 pickup codes). You + your buying agent handle
**payment, the delivery address, checkout, and all logistics** — the tool hands off at the
cart and the tracking digest.

## What it does NOT do
No headless scraping, no proxy rotation, no captcha-solving service, no cloud. It **never
pays, checks out, or picks a shipping address**, and it **never blind-sends** a seller
message (confirm-then-send — you approve each one). **Not getting your account flagged is
the priority, not speed.**

---

## Install (one time)

```bash
# from the project root
uv venv --python 3.12
uv pip install -e ".[dev]"
```

You need **Google Chrome** installed (the real app, not Chromium, not Comet).
`config.toml` uses the portable `chrome` channel. If the browser lives somewhere
non-standard, put its absolute path in the Git-ignored `config.local.toml`.

## Configure

Edit tracked defaults in `config.toml`; put machine-specific values in
`config.local.toml`:
- `[browser] executable_path` — pinned Google Chrome binary (avoids launching Comet/other Chromium).
- `[browser] user_data_dir` — the persistent profile under this project's
  `user_data/` boundary (your login lives here; gitignored). Paths outside that
  directory are rejected.
- `[pacing]` — random delays + `max_products_per_minute` (keep it low).
- `[limits]` — `max_reviews`, `review_pages`.
- `[output] dir` — where xlsx + `run.log` land.

## Run

```bash
.venv/bin/python server.py                                   # stdio MCP server
npx @modelcontextprotocol/inspector .venv/bin/python server.py   # interactive inspect
```

For Claude Desktop, register it as an MCP server pointing at the **full venv python
path** and `server.py` (use absolute paths — `/Volumes/...`).

## First-run login (once per session)
You log in with **your own** Taobao account — no account, cookie, or profile ships in this
repo (`user_data/` is gitignored and lives only on your machine).
1. Call `taobao_initialize_login` (or just `taobao_fetch_product` — it auto-ensures login).
2. A **visible Chrome window** opens to the Taobao QR page.
3. **Scan the QR with your Taobao app.** The server polls and continues automatically.
4. The session persists in `user_data_dir` — restarts reuse it, no re-scan.

## Tools
| Tool | Purpose |
|---|---|
| `taobao_initialize_login` | Open Chrome, QR login (you scan). |
| `taobao_session_status` | Login/health (read-only). |
| `taobao_search` | Keyword → result list for you to pick from. |
| `taobao_fetch_product` | One product: **every SKU variant + price/stock**, specs, images. (`deep_price=True` clicks each variant for the live after-subsidy price.) |
| `taobao_fetch_reviews` | Recent reviews, each tagged with the variant bought. |
| `taobao_add_to_cart` | **Gated** cart staging — preview, then `confirm=True`; selects + validates the variant, adds via the cart API (Taobao **and** Tmall). Never buys/checks out. |
| `taobao_read_messages` | Read seller IM conversations + a thread's messages (read-only). |
| `taobao_send_reply` | Send a seller message — **confirm-then-send** (preview, then `confirm=True`). |
| `taobao_track_orders` | Daily digest: per order — status, carrier + tracking#, **取件码 pickup code** + station. Caps to one live run/day. |
| `taobao_export_xlsx` | 3-sheet comparison workbook (Summary / Variants / Reviews). |
| `taobao_full_picture` | Joins cart + orders (+ tracking/取件码) + seller chats **by vendor** — per-seller, per-order, or an overview. |
| `taobao_export_inventory` | Pages the full purchase history → a **visual inventory** workbook: embedded thumbnail (or `=IMAGE` for Google Sheets) + variant per line, with **landed cost** (product + shipping allocated by qty) and a By-Category sheet. |

## The Skill
`skills/taobao-sourcing/SKILL.md` is the sourcing playbook (search → you pick → fetch → translate →
summarize reviews → normalize price-per-unit → compare → export → flag risks).
`skills/taobao-sourcing/supplier_templates.md` has Chinese message templates — **the assistant drafts; sent via
`taobao_send_reply` only after you confirm that exact message** (never blind auto-send).

The ChatGPT/Codex plugin loads this skill directly. For a separate Claude Code
installation, copy it to `~/.claude/skills/` after each edit:

```bash
mkdir -p ~/.claude/skills/taobao-sourcing
cp skills/taobao-sourcing/SKILL.md skills/taobao-sourcing/supplier_templates.md ~/.claude/skills/taobao-sourcing/
# optional, local-only buyer profile (gitignored):
cp skills/taobao-sourcing/sourcing_profile.md ~/.claude/skills/taobao-sourcing/ 2>/dev/null || true
```

---

## Troubleshooting
- **It launched Comet / the wrong browser** — set `[browser] executable_path` to your
  Google Chrome binary (default: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`).
- **"login_required" / NotLoggedInError** — run `taobao_initialize_login` and scan the QR; keep the window open.
- **A slider/verification appeared** — solve it yourself in the Chrome window; the tool pauses (`human_action_required`) and resumes. It logs to `output/run.log`.
- **Screenshots/automation "page still loading"** — the new detail page holds a connection open; this server uses embedded-data + DOM extraction (not screenshot-waits), so this only affects ad-hoc scripts.
- **`SelectorDriftError`** — Taobao changed its layout; patch the one file `src/extract/selectors.py`.
- **Wrong price on a multi-model listing** — the headline price is the cheapest model; always read the **per-SKU price** for the exact variant. `补贴后` prices may include a 国补 subsidy that needs a mainland ID — verify the real checkout price.
- **Only a few reviews returned** — deep review pagination is shallow (known limit); increase scrolling in `src/extract/reviews.py` if needed.
- **Reset everything** — delete `user_data/chrome_profile/` and re-scan the QR.

## Risks (don't hide these)
- Scraping Taobao violates its ToS; using your own logged-in account carries
  account-limitation risk. Keep volume low and human-paced.
- mtop endpoints / selectors drift — budget periodic maintenance (selectors are centralized).

## Tests
```bash
.venv/bin/python -m pytest -q     # parsers, output, MCP contract, drift, evals
```
