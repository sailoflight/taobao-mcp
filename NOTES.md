# NOTES.md — Base Repo Recon (`JeremyDong22/taobao_mcp`)

> Phase 0 documentation of the base repo's **actual** behavior, as cloned to
> `_base_repo/ (repo root)` (git HEAD `4cdeb50`, "Fix critical bugs causing MCP tool to hang with certain URLs").
> Every claim is cited `file:line`. "Not present" means the code does not contain it.

Base repo layout (4 Python modules):
- `server.py` (389 lines) — MCP server + tool registration + handlers
- `taobao_scraper.py` (1380 lines) — browser lifecycle, login, DOM scraping
- `unified_fetcher.py` (326 lines) — image collection + pagination + markdown
- `image_utils.py` (273 lines) — async image download → base64, AVIF→WebP
- `pyproject.toml`, `README.md`, `USAGE.txt`, `CLAUDE.md`, `__init__.py`

---

## 1. MCP Surface

**Transport: stdio.** `server.py:367` — `async with stdio_server() as (read_stream, write_stream):` then `mcp_server.run(...)` at `:368`. Entry: `asyncio.run(main())` at `server.py:385`.

**SDK style: low-level `mcp.server.Server`, NOT FastMCP / `@mcp.tool`.** Imports at `server.py:36-42`:
```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (Tool, TextContent, ImageContent)
```
Server created at `server.py:125`: `mcp_server = Server("taobao-mcp")`. Tools are declared via a manual `@mcp_server.list_tools()` handler (`server.py:128`) returning hand-built `Tool(...)` objects with raw JSON-Schema `inputSchema`, and dispatched via a single `@mcp_server.call_tool()` router (`server.py:229`). There are **no Pydantic-typed tool signatures** at the MCP boundary — only one input model, `ProductInputBase` (`server.py:93-115`), validated manually inside the handler.

**Tools registered: exactly 2** (despite README/USAGE referencing a `taobao_fetch_product_info` name — the real registered name is `taobao_fetch_product`).

| Tool | Params (from `inputSchema`) | Returns | What it does |
|---|---|---|---|
| `taobao_initialize_login` | none (`{"type":"object","properties":{},"required":[]}`, `server.py:146-150`) | `list[TextContent]` with a status string (`success` / `login_required` / `already_initialized` / `error`) | Launches persistent browser, navigates to taobao.com, detects login, surfaces QR-login instruction. Handler `handle_initialize_login` `server.py:245-305`. |
| `taobao_fetch_product` | `product_url_or_id: str` (required), `offset: int = 0` (min 0), `limit: int = 10` (min 1, max 20) — `server.py:198-224` | `list[TextContent \| ImageContent]` — markdown blocks + base64 images, paginated | Scrapes the product (DOM), then returns **paginated images** + a basic-info markdown block. Handler `handle_fetch_product` `server.py:333-360`. |

Tool signatures are not Python functions — they are `Tool(name=..., description=..., inputSchema={...})` dicts. The dispatcher (`server.py:229-240`):
```python
@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    if name == "taobao_initialize_login":   return await handle_initialize_login()
    elif name == "taobao_fetch_product":     return await handle_fetch_product(arguments)
    else: raise ValueError(f"Unknown tool: {name}")
```

**Notable surface facts:**
- The `taobao_fetch_product` description (`server.py:156-197`) is image-centric: it instructs the agent to *auto-loop pagination until `has_more=False`*. The tool's product is really "fetch all images + a summary," not structured product data.
- There is a `ProductCache` class (`server.py:57-88`, TTL 30 min) but it is **effectively disabled** — `_get_or_scrape_product` (`server.py:308-330`) always re-scrapes (comment at `:318`: "cache disabled to ensure latest URL cleaning logic").
- **No search tool, no reviews-only tool, no export tool.** Not present.
- Errors are returned as `TextContent` strings (`server.py:355-360`), not raised MCP errors — there is no error taxonomy.

---

## 2. Browser & Login Flow

**Launch — persistent context, headed, but NOT real Chrome.** `taobao_scraper.py:502-509`:
```python
self.playwright = await async_playwright().start()
self.browser = await self.playwright.chromium.launch_persistent_context(
    user_data_dir=str(self.profile_dir),
    headless=False,
    viewport={'width': 1280, 'height': 720},
    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
)
self.page = await self.browser.new_page()
```
- `launch_persistent_context` ✅ used. `headless=False` ✅.
- **`channel="chrome"` — NOT present.** It uses bundled **Chromium** (`self.playwright.chromium`), not real Chrome. README/USAGE both say `playwright install chromium`.
- **`user_data_dir` value:** constructor default `profile_dir="user_data/chrome_profile"` (`taobao_scraper.py:456`), and `server.py:252` instantiates `TaobaoScraper(profile_dir="user_data/chrome_profile")`. Path is **relative to CWD** (wrapped in `Path(...)`, `taobao_scraper.py:463`; `mkdir(parents=True, exist_ok=True)` at `:500`). USAGE.txt:122 says "../user_data/chrome_profile" but the code uses `user_data/chrome_profile`. No `locale`, no `timezone_id` set.

**Login flow** — `initialize()` (`taobao_scraper.py:469-583`):
1. Liveness check: if already initialized, run `await self.page.evaluate("1 + 1")`; if it throws, reset and relaunch (`:478-497`).
2. Navigate to `https://www.taobao.com` (`:516-517`), sleep 2s.
3. If URL contains `login.taobao.com`/`login.tmall.com` (`:523`), try `_handle_quick_entry_button()` (the "快速进入"/Quick-Entry button shown when cookies are still valid). If still on login page → return `status="login_required"` with **manual QR-scan instructions** (`:543-554`).
4. Else verify with `_check_login_status()`; return `success` or `login_required`.

**QR login mechanism: manual + passive.** There is **no QR polling loop**. The server simply returns a `login_required` message telling the human to scan; `server.py:265` even states "The browser will remain open for 3 minutes." The human must re-call `taobao_initialize_login` after scanning. No automated wait-for-login.

**`is_logged_in` logic — `_check_login_status()`** (`taobao_scraper.py:626-677`): multi-factor JS check requiring **all three**: DOM element `.site-nav-login-info-nick` present AND cookie `dnk` AND cookie `_tb_token_` (`:655` `isLoggedIn = !!nickElement && !!dnk && !!tbToken`). Returns `{isLoggedIn, username, dnk, ...}`.

**Quick-Entry handler — `_handle_quick_entry_button()`** (`taobao_scraper.py:593-624`): tries 4 selectors, clicks if button text contains "快速进入".

**Session persistence:** purely via Playwright's `user_data_dir` (cookies/localStorage on disk). Re-running reuses it. ✅ This is the one piece we keep wholesale.

**Captcha / punish / slider handling — NOT present.** Grep for `captcha|punish|slider|_____tmd_____` returns nothing. The code has **no detection and no human-handoff pause**. The only "guard" is: if redirected to a `login.*` URL during a fetch it raises `RuntimeError("Login required! ...")` (`taobao_scraper.py:737-748`). A verification slider would simply make selectors time out.

**Browser-liveness re-init** is duplicated in `scrape_product()` (`taobao_scraper.py:701-713`).

---

## 3. Fetch Flow (URL/ID → data)

**Interception vs DOM: 100% DOM scraping. No mtop XHR interception anywhere.** Confirmed by grep: no `page.on(`, no `.on("response"`, no `response.json`, no `mtop`, no `__INITIAL_DATA__`, no `TShop`, no `sku2info`/`skuBase`/`propPath` in any `.py`. All data comes from CSS selectors + `query_selector(_all)`.

**Trace** (`scrape_product`, `taobao_scraper.py:679-823`):
1. **ID extraction** — `TaobaoLinkExtractor.extract_product_id` (`:182-243`): regex priority = direct `item.htm?id=` link → short link (`e.tb.cn`/`s.click.taobao.com`, resolved via **browser nav** `resolve_short_link_with_browser` `:245-263` then **HTTP redirect** `resolve_short_link` `:265-295`) → bare 12–13-digit ID. Patterns at `:178-180`.
2. **Build URL** — always Tmall: `build_product_url(id, platform='tmall')` → `https://detail.tmall.com/item.htm?id={id}` (`:297-303`). (Taobao-vs-Tmall is not auto-detected from the source link for the final nav.)
3. **Navigate** `domcontentloaded`, sleep 3s (`:729-731`); if bounced to login, try quick-entry else raise (`:737-748`).
4. Wait for title selector `.mainTitle--R75fTcZL` (`:751`); if share-link params remain, rebuild clean URL and reload (`:754-763`).
5. Scrape sections sequentially (`:773-817`), each its own DOM method.

**Per-data-type handling (all DOM, all hashed CSS classes — fragile):**

- **SKU variants / per-SKU price: NOT extracted as variant rows. This is the critical gap.** `_scrape_specifications` (`:1304-1380`) only reads the **option labels** — it collects color/size *names* into `specifications['colors']` / `['sizes']` (`:1340-1346`) and one global `stock_status` string (`:1373-1375`). **There is NO per-SKU price, NO per-SKU stock, NO `pid:vid` join, NO cartesian enumeration.** It never reads a `skuBase`/`sku2info` map (that map isn't fetched at all). Selectors: `SKU_ITEM .skuItem--Z2AJB9Ew`, `SKU_LABEL .ItemLabel--psS1SOyC`, `SKU_VALUE_ITEM .valueItem--smR4pNt4` (`:118-123`).

- **Price (headline only):** `_scrape_basic_info` (`:825-956`) reads `.text--LP7Wf49z` nodes (`PRICE_NUMBER`, `:91`), takes `prices[0]` → `current_price`, `prices[1]` → `original_price` (`:841-854`). Single headline/original pair, **not per variant**.

- **Reviews:** `_scrape_reviews` (`:1098-1167`) clicks the reviews tab, scrolls 5× (`:1116-1119`), reads `.Comment--H5QmJwe9` cards. Per review: `username` (`.userName--KpyzGX2s`), `review_text` (`.content--uonoOhaz`), and a `meta` string split on `·` into `review_date` (part 0) and **`product_variant`** (part 1) (`:1133-1140`), plus `photos` (URL list). **No pagination beyond one tab's lazy-scroll, no `max`/page cap, no dedupe, no rating, no XHR.** `product_variant` is captured but **not parsed/normalized and not grouped** into any per-variant rollup — there is no `reviews_by_variant`.

- **Q&A:** `_scrape_qa` (`:1169-1204`) scrolls to `.askAnswerWrap--SOQkB8id`, reads `.askAnswerItem--RJKHFPmt` → `{question, answer}`. ✅ present (DOM, single pass).

- **Specs/参数:** `_scrape_parameters` (`:958-1009`) clicks params tab, reads "emphasis" + "general" param items → `list[{param_name, param_value, param_category}]`. ✅ present (DOM). Returns a **list of dicts**, not a `dict[str,str]`.

- **Images:** gallery from `#picGalleryEle` + SKU thumbs (`_scrape_basic_info` `:856-951`), detail images from `.desc-root` with lazy-scroll (`_scrape_detail_images` `:1011-1096`), review photos inline. Heavy URL-cleaning regex to strip CDN webp/size suffixes (`:881-898`, repeated several places).

- **Also scraped:** shipping (`:1206-1236`), shop details+ratings (`:1238-1279`), guarantees (`:1281-1302`).

**Output shape:** `scrape_product` returns a flat **dict** (not a Pydantic model). `unified_fetcher.fetch_product_with_images` (`:85-171`) then flattens all image lists, paginates (offset/limit, max 20), downloads each via `image_utils.fetch_images_batch`, and emits markdown + `ImageContent`. `generate_markdown` exists (`taobao_scraper.py:343-443`) but the server path uses the unified fetcher's own `_generate_basic_info` (`unified_fetcher.py:231-292`) instead.

---

## 4. Anti-Detection

Almost nothing. Against our `CLAUDE.md §7` non-negotiables:

| Measure | Status | Evidence |
|---|---|---|
| Persistent context | ✅ | `taobao_scraper.py:504` |
| Headed (`headless=False`) | ✅ | `:506` |
| Real Chrome `channel="chrome"` | ❌ not present | uses `playwright.chromium` (bundled), README says `playwright install chromium` |
| `--disable-blink-features=AutomationControlled` / launch `args` | ❌ not present | no `args=` anywhere |
| `navigator.webdriver` masking | ❌ not present | no override; bundled Chromium leaves it `true` |
| `locale` / `timezone_id` | ❌ not present | not passed to `launch_persistent_context` |
| Custom UA | ⚠️ partial | sets a truncated Mac UA `...AppleWebKit/537.36` (`:508`) — no Chrome version token, arguably *more* suspicious |
| Human-paced delays / `max_products_per_minute` | ❌ not present | only fixed `asyncio.sleep(2/3)` + fixed scroll loops; no randomization, no rate cap |
| Mouse jitter / random movement | ❌ not present | grep `move_mouse`/`jitter`/`random` → none |
| Captcha/slider/punish pause + handoff | ❌ not present | see §2 |

So: the warm-session reuse is good, but **stealth flags, real-Chrome channel, locale/timezone, pacing, mouse jitter, and captcha handling must all be built fresh** in our `src/browser/`.

---

## 5. Dependencies

From `pyproject.toml`:
- **Python:** `requires-python = ">=3.10"` (our spec wants 3.11+ — fine, superset).
- **Deps (exact):**
  - `mcp>=0.9.0`  ← our spec needs `mcp>=1.2` for FastMCP; **bump required**
  - `playwright>=1.40.0`
  - `aiohttp>=3.9.0`
  - `pydantic>=2.0.0`
  - `Pillow>=10.0.0`
- **Build:** `hatchling`; `[tool.hatch.build.targets.wheel] packages = ["."]`.
- **Console-script entry point: NONE.** No `[project.scripts]`. The base `CLAUDE.md` shows `uv run taobao-mcp`, but that target **does not exist** — README/USAGE correctly invoke `python3 .../server.py` directly. We must add `[project.scripts]` if we want `taobao-sourcing` runnable.
- **Missing for our build:** `openpyxl` (xlsx), `pytest` (tests). Not in deps.

---

## 6. Key Reusable Functions / Classes (exact names + `file:line`)

- **Browser launch:** `TaobaoScraper.initialize()` — `taobao_scraper.py:469`; the actual launch block `:502-511`. Class `TaobaoScraper` defined `:448`; `__init__` `:456`; `close()` `:585`.
- **Login (passive QR) + quick-entry:** `TaobaoScraper._handle_quick_entry_button()` — `taobao_scraper.py:593`. Login orchestration inside `initialize()` `:514-577`.
- **Login-check:** `TaobaoScraper._check_login_status()` — `taobao_scraper.py:626` (cookie `dnk` + `_tb_token_` + `.site-nav-login-info-nick`).
- **Captcha handling:** none — must build (`guard_captcha` is ours to write).
- **mtop interception:** none — must build (`interceptor.py` is ours to write).
- **Product fetch (DOM):** `TaobaoScraper.scrape_product()` — `taobao_scraper.py:679`. Section methods: `_scrape_basic_info` `:825`, `_scrape_parameters` `:958`, `_scrape_detail_images` `:1011`, `_scrape_reviews` `:1098`, `_scrape_qa` `:1169`, `_scrape_shipping_info` `:1206`, `_scrape_shop_details` `:1238`, `_scrape_guarantees` `:1281`, `_scrape_specifications` `:1304`.
- **ID / link extraction (high reuse):** `TaobaoLinkExtractor.extract_product_id` `:182`, `resolve_short_link_with_browser` `:245`, `resolve_short_link` `:265`, `build_product_url` `:297`; helpers `is_share_link` `:308`, `clean_share_url` `:329`.
- **Selectors (central registry):** `TaobaoSelectors` `:86`, `TaobaoNavigationHelpers` `:154` (tab index map).
- **Image download → base64 (high reuse):** `image_utils.fetch_image_as_base64` `:49`, `fetch_images_batch` `:114`, MIME magic-byte detection `_detect_mime_type_from_bytes` `:147`, AVIF→WebP `_convert_to_webp` `:235`. Includes Alibaba anti-hotlink headers (`Referer: detail.tmall.com`) `:64-74`.
- **Markdown:** `generate_markdown` `taobao_scraper.py:343`; unified path `unified_fetcher._generate_basic_info` `:231`.

---

## 7. Reuse vs. Extend vs. Replace (for OUR goals)

Our deliverables: (i) a price for **every** SKU via `skuBase`/`sku2info` join; (ii) reviews **linked to the variant bought** + `reviews_by_variant`; (iii) Q&A; (iv) xlsx export; (v) sourcing Skill.

**REUSE AS-IS (drop into our layers):**
- `image_utils.py` — full module (download, base64, AVIF→WebP, anti-hotlink headers). → our Output/fetch helpers.
- `TaobaoLinkExtractor` (ID + short-link + share-text resolution) and `is_share_link`/`clean_share_url` — robust, no live-site coupling. → reuse in `extract/` or a util.
- Persistent-context **session persistence pattern** (`launch_persistent_context(user_data_dir=...)`) and `_check_login_status()` cookie/DOM heuristic — reuse the approach in `src/browser/session.py`.
- `_handle_quick_entry_button()` "快速进入" logic — reuse inside our login flow.
- `_scrape_qa()` — works as a DOM Q&A fallback (light reshape into `QAPair`).

**EXTEND / RESHAPE:**
- **Browser launch** — start from `initialize()` but add `channel="chrome"`, `args=["--disable-blink-features=AutomationControlled"]`, `locale="zh-CN"`, `timezone_id="Asia/Shanghai"`, webdriver masking. (Our `config.toml` drives these.)
- **Login** — convert the passive "return login_required" into an **active polling** `ensure_logged_in()` that waits for the QR scan (base only instructs, never waits).
- **Parameters/specs** — reuse `_scrape_parameters` selectors but emit `dict[str,str]` to fit `Product.specs`.
- **Reviews** — keep the DOM card parser as a *fallback*, but our primary path is the **rate-list XHR** (Appendix A.2). Must add: pagination cap (`review_pages`/`max_reviews`), dedupe, `sku_bought` normalization, and `reviews_by_variant` grouping — none exist today (base captures a raw `product_variant` string but never groups it).
- **Output dict → Pydantic** — base returns a flat dict; we must map into `Product`/`SkuVariant`/`Review`/`QAPair` (our `models.py`). The unified-fetcher's image-pagination flow can inform Output but isn't our structured path.
- **Selectors registry** — extend `TaobaoSelectors`, but **centralize + wrap in try/except** with `SelectorDriftError` (Phase 6).

**MISSING ENTIRELY — BUILD FRESH:**
- **mtop XHR interception** (`interceptor.py`, `page.on("response")` before nav) — nothing exists. **This is the core new capability.**
- **Per-SKU price/stock extraction** — the `skuBase` (props/skus/propPath) ↔ `skuCore.sku2info` join, `pid:vid → 名称` mapping, cartesian completeness check, `SkuIncompleteError`. **Highest-value, entirely absent.** Click-through fallback also absent.
- **`reviews_by_variant` rollup** and review↔variant linkage — absent.
- **Search** (`s.taobao.com` results → `SearchResult`) — no search tool at all.
- **xlsx export** (`openpyxl`: summary/variants/reviews sheets) — absent (`openpyxl` not even a dep).
- **Anti-detection layer** — `pacing.py` (human_delay/human_scroll/move_mouse), `max_products_per_minute`, `guard_captcha` (slider/punish detection + human pause/resume). Absent.
- **Error taxonomy** (`errors.py`: NotLoggedInError/CaptchaError/ProductNotFoundError/SkuIncompleteError/SelectorDriftError) — base returns plain strings; absent.
- **FastMCP migration** — base uses low-level `Server`; our spec mandates FastMCP `@mcp.tool` (and `mcp>=1.2`). The 2-tool surface must be rebuilt as 6 FastMCP tools.
- **Config file** (`config.toml`) — base hard-codes everything; absent.
- **Sourcing Skill + supplier templates, tests, evals** — absent.

**One trap to flag:** the base navigates **everything to Tmall** (`build_product_url(..., 'tmall')`, `:300-302`) regardless of the source platform, and waits on the Tmall title selector `.mainTitle--R75fTcZL`. Taobao-vs-Tmall detail pages differ (and mtop endpoints differ per Appendix A). Our fetcher must branch by platform, not force Tmall.

---

## 详情图长图 (全量详情) — 现场 recon 记录 (2026-08-18)

> 需求:抓商品页底部"详情图长图"(如 3D 打印机那种一长条图片)。以下是完整侦查过程与最终机制,供维护时参考。

### 侦查过程(都试过,记录教训)
1. **桌面 SSR 页直接访问 `item.taobao.com/item.htm?id=X`** → 页面正确加载(重定向到 `detail.tmall.com`),**但详情容器 `#description` / `.desc-root` 都不存在**;首屏+滚动 12 秒内**没有任何详情相关网络请求**(只有 recommend/querybagcount 之类)。→ 结论:新版桌面页首屏不渲染详情、不主动拉详情。
2. **H5 壳 `h5.m.taobao.com/awp/core/detail.htm?id=X`** → 302 到 `detail.tmall.com?x-ssr=true&h5_spm=a-tb-item`(移动端 SSR 变体),**选型号后出现"扫码去移动端购买"墙** → 对桌面抓取是死路。
3. **H5 页调的接口**是 `mtop.taobao.detail.data.get/1.0`(带签名 GET);直接复制签名 URL 重放可拿到响应,但响应里详情字段未定(payload 已变)。
4. **滚动 SKU 面板 `#tbpcDetail_SkuPanelBody`** → 面板确实可滚,但滚完 `.desc-root` 仍不存在。
5. **点击"图文详情"标签** → 该标签(`.tabTitleItem--z4AoobEz`)在直接访问时**根本不存在**。
6. **油猴/搞图宝插件源码**(greasyfork 460143, 2026-01 仍维护;github.com/CJMF-i/gao-tu) → 它的选择器也是 `.desc-root` / `.content-detail` / `[class*="desc-"]` / `[class*="detail-"]`,靠滚动 `#tbpcDetail_SkuPanelBody` 触发懒加载——**前提同样是"页面有详情"**。
7. **人工监听(taobao_debug_watch)**:用户从详情页去搜索"拓竹a1"→ 点商品 → 那个**带跟踪参数的点击 URL** 进入的页面**详情加载了**。
8. **二分参数(taobao_debug_entry)**:完整跟踪 URL 触发;逐个剥离后发现 **`mi_id=<值>` 单独即可触发**,而且:
   - `mi_id` 单独(`?id=X&mi_id=...`)就够,不需要 skuId;
   - 编造的 `mi_id` 值**不触发**(值有语义);
   - **同一个 `mi_id` 在多个商品上有效**(拓竹A1 50张 / 纵维立方46张 / Storelite 14张 / 天鼠 23张)→ 账号/渠道级稳定值,非每次点击生成。

### 最终机制
- **入口**: `https://item.taobao.com/item.htm?id={pid}&mi_id={mi_id}`
  `mi_id` 让 SSR 渲染出完整图文详情;裸 URL(无 mi_id)页面**根本没有 `.desc-root`**。
- **容器**: 详情图在 **`.desc-root`** 里(老页面才是 `#description .content`)。
- **懒加载**: 需先把 SKU 面板滚入视野,再滚 `#tbpcDetail_SkuPanelBody` 内部触发图片;图片真实地址在 `data-ks-lazyload` / `data-src` / `src`。
- **过滤**: 取 `width>=700` 的图(排除 logo/缩略图),无结果时回退到全部宽度的图。
- **无需 mtop/签名**: 全部 SSR 渲染进 DOM(`mtop_apis: 0`)。

### 落地
- `config.toml [detail] mi_id`(默认 `0000aArBeG_hsA1mg1B99KlUQg3VeA5Nf2vZ-C6aSFzldN4`)。
- `src/extract/desc.py::fetch_detail()` → 返回 `{product_id, url, scope, panel_found, count, detail_images[], caveat}`。
- MCP 工具 **`taobao_fetch_detail(product_url_or_id)`**。
- 保留 3 个只读调试工具:`taobao_debug_detail`(机制侦查)、`taobao_debug_entry`(入口 URL 测试)、`taobao_debug_watch`(人工操作+网络/DOM 记录)。

### 风险/注意
- `mi_id` 是账号/渠道级营销 id,若淘宝轮换/吊销会失效 → 届时重新走 `taobao_debug_entry` 从一次人工点击的 URL 里提取新 `mi_id` 更新 config。
- 详情容器可能再变(现在是 `.desc-root`,老版本是 `#description .content`)——选择器集中在 `src/extract/selectors.py`。
- 大长图(50 张)URL 都是 https alicdn 直链,可直接下载;图片本身不带文字,识别需 OCR。

---

## mi_id 续期工具 + 风控策略 (2026-08-18 补充)

### 为什么要续期(风控)
- 淘宝持续升级反爬/风控(2025–2026 大量实战记录),真实账号自动化有封号案例。
- 硬编码/反复复用同一 `mi_id` = 每次请求带同一个营销 token,留下**固定足迹**,且淘宝可能轮换它。
- **低风险方案**:mi_id 由"真人自然点击"生成(真人浏览行为),单独一个工具续期,不长期复用。

### `taobao_get_miid` 工具(新增)
- 打开搜索结果页(可见 Chrome 窗口)→ **客户像正常购物一样随便点一个商品** → 点击生成的跟踪 URL 带全新 mi_id → 工具捕获并持久化到 `output/.miid.json`(gitignored)。
- 默认观察 90 秒(可配);要求点击的是"导航到商品页"的左键点击(item.htm 且带 mi_id)。
- **联动**:`taobao_fetch_detail` 返回 `miid_stale=true`(无 `.desc-root`)时 → 调 `taobao_get_miid` 续期 → 重试。
- 优先级:`output/.miid.json`(运行时,最新) > `config.toml [detail] mi_id`(静态兜底)。
- 缓存:`load_config()` 的缓存 key 已含 `.miid.json` mtime,写入新 mi_id 后立即生效。

### 推荐工作流(无 miid 先搜大框 → 确认目标 → 详情)
1. `taobao_search` 无 miid 大范围搜索(公开搜索,零风险),圈定几个明确目标;
2. `taobao_fetch_product` 拿型号/规格/价格(无需 miid,正常 SSR);
3. `taobao_fetch_detail` 拿详情长图 + **定向优惠/个性化促销页**(miid 页附带加载)——
   若 `miid_stale=true`,先 `taobao_get_miid`(客户点一个商品)再重试。

### 风险边界
- `taobao_get_miid` 只读 URL(不点购物车/不下单/不写),人类点击是唯一"操作"。
- 频率仍是关键:详情抓取保持 human-pacing;mi_id 没必要每次刷新,失效/定期再取即可。

---

## mi_id 行为实测 + 轮换策略 (2026-08-18 二补)

### 实测(多商品多点击 + 跨商品/时效验证)
- **mi_id 不是每次点击随机**:同一商品+同一渠道 → mi_id 固定(广告素材点击 3 次 mi_id 相同,ali_trackid 才每次不同);不同商品 / 不同渠道(搜索 vs 广告)→ mi_id 不同。
- **任意有效 mi_id 跨商品通用**:某商品点击来的 mi_id 用在别的商品上一样触发详情(.desc-root)。
- **mi_id 失效很慢**:几小时前抓的 mi_id 仍有效(天鼠 23 张、用户点击过的商品 16 张)。
- 格式:`0000` + 21~26 位 base62-ish;`spm=a21n57.1.hoverItem.N` 的 N 是页面位置,不是 token。

### 轮换策略
- 建议 **每 5~10 次详情抓取轮换一次**(`taobao_get_miid` 让客户自然点一个商品即可),或换商品/换渠道时顺手换。
- 风控足迹风险主要来自"一个 token 反复驱动大量不同商品",轮换即为此。
- `taobao_fetch_detail` 的 `miid_stale=true` 是失效兜底信号 → 调 `taobao_get_miid` 续期。
- 监听工具 `taobao_debug_miid_watch`(taobao_debug_miid_watch):打开搜索页逐秒记录 URL+mi_id,用于以后复核 token 行为是否变化。

---

## 自动取 mi_id(mode="auto")+ 风控三条 (2026-08-18 三补)

### 风控推断(结合实测)
1. **mi_id ≈ 个人+渠道(营销素材级)追踪 id,决定定向优惠** —— 用 mi_id 进的详情页会加载政府补贴/促销横幅等个性化内容。
2. **警惕同商品会话内"可变量"**:ali_trackid(每次点击)、spm 位置号、priceTId、utparam 都在变 → 正是风控指纹原料。抓详情 URL 应保持干净稳定(`id+mi_id`),不制造变化 token。
3. **自动获取可行**:首页"猜你喜欢"推荐位(`.tb-pick-feeds-container` 内 `a.item-link`,xxc=home_recommend)**href 直接带 mi_id**,固定位置、可程序化读取/点击。

### taobao_get_miid 的 mode
- `mode="auto"`(默认):进首页 → 读固定推荐位链接的 mi_id(零点击足迹),失败则模拟点击该固定位,再不行搜索首结果点击。无需人工。实测 2.7s 取到并持久化。
- `mode="human"`:打开搜索页等真人点一个商品(最低风险兜底)。
- 持久化到 `output/.miid.json`(Windows 部署为 `C:\MCP\taobao-mcp\output\.miid.json`),优先级 > config.toml,缓存按 mtime 失效。
- 闭环实测:auto 取新 mi_id → fetch_detail 自动使用 → 拓竹 A1 50 张详情图,miid_stale=False。

### 轮换建议(修订)
- mi_id 失效慢 + 任意有效 token 跨商品通用 → **每 5~10 次详情抓取轮换一次**即可;`taobao_get_miid(mode="auto")` 一键续。
- 保持抓取 URL 干净稳定;不要在同一会话里人为制造一堆变化参数。
- 调试工具 `taobao_debug_home` 可随时复核首页推荐位结构是否变化。

---

## 收藏链路取 mi_id(每次详情查询都重新生成)(2026-08-18 四补)

用户设计(手动验证通过):确认要访问的商品后,通过收藏/购物车把它放到近似固定位置 → 模拟点击直接生成完整参数链接 → 每次查询详情都重新生成,完美避开"一个 token 反复驱动大量商品"的足迹。低效率可接受。

### 手动录制验证(g2_pages_watch,多页面监听)
- 加购物车 → 从购物车点击 → `?from=cart&id=X&mi_id=<全新>`。
- 进收藏夹(`i.taobao.com/my_itaobao/itao-tool/collect`)→ 点收藏的商品 → `?id=X&mi_id=<全新>&spm=tb...`。
- 3 个不同 mi_id,每次点击都生成新值 → 设计成立。

### 收藏夹页面的真实结构(深度 recon 结论)
- 商品列表是 JS 加载(等 ~12s + 反复滚动才渲染),不在 iframe 里(iframe 只是 search-suggest/push 基建)。
- 卡片 = `div.goodsItem--hCuMGp0I card--Je2jHg4e`,**无 <a> 链接、DOM 里无 pid**;点击由 JS 事件绑定。
- **点击卡片 → 新标签页打开**商品:`detail.tmall.com/item.htm?id=X&mi_id=<全新>&spm=tbpc.mytb_itemcollect.item.goods&upStreamPrice=…`。新收藏在列表**最前**(位置≈固定)。
- 收藏判定信号:**`#collectBtn` 按钮颜色/icon**(favorited = `icon-taobaoyishoucang`+文字"已收藏"+橙 `rgb(255,80,0)`;not = `icon-taobaoshoucang`+"收藏"+深色)。比一次性 toast 持久可靠。**别点外层 div `.RightButtonList`(无 handler),要点 `#collectBtn`**。

### fetch_detail(miid_source="favorite") 四条规则(用户定,均已实现并实机闭环)
1. **按钮颜色判态**:已收藏 → 不碰(绝不 remove+re-favorite,不破坏收藏夹分层位置);未收藏 → 点 `#collectBtn` 添加,验证按钮翻转。`added_by_us` 标记本轮是否我们加的。
2. **找位置点击**:进收藏夹 → 等渲染 → 点第一个 `goodsItem` 卡片(新收藏在最前)→ 新标签页带全新 mi_id;用 `expect_popup` 接住 → 校验打开的商品 `id==pid`(防点错/已收藏沉底的兜底:不匹配就回退 config mi_id,绝不乱猜)。
3. **查完清理**:`added_by_us=true` → `ensure_unfavorited`(点 `#collectBtn` 取消,验证颜色复原)→ 零残留;非本轮收藏不清理。
4. **单标签卫生**:抓完关掉弹窗标签页(CLAUDE.md §7.3 不批量开 tab)。
- 实测:已收藏商品(拓竹 A1)→ 不动、收藏夹点击新 mi_id、50 张图;未收藏商品(欧丝轩阁 PETG 干燥箱)→ 添加→点击新 mi_id→12 张图→查完取消收藏(cleanup=removed)。
- 调试工具:`taobao_debug_pages_watch`(多页面 URL+mi_id 录制)、`taobao_debug_favorite`(按钮/弹窗/收藏夹)、`taobao_debug_collect(_deep)/goodsitem/collect_click`(收藏夹结构/点击行为)。

---

## 工具整合 + 查询分离 + 模拟点击确认 (2026-08-18 五补)

### 工具清理(25 → 19)
- 删除 6 个冗余调试工具:`taobao_debug_watch`/`taobao_debug_entry`(被 `pages_watch`+`detail` 取代)、`taobao_debug_miid_watch`(被多页面 `pages_watch` 取代)、`taobao_debug_collect_click`/`taobao_debug_goodsitem`/`taobao_debug_collect_deep`(整合进 `taobao_debug_collect`)。
- 整合:收藏夹结构/卡片/点击探测 → **一个 `taobao_debug_collect(target_pid)`**(等 JS 渲染 → 卡片样例 → 点第一张卡 → 新标签页取全新 mi_id + opened_id 校验)。
- 删除模块 `collect_deep.py`/`collect_goodsitem.py`(逻辑并入 favorite.py 的 `recon_collect`);删除死代码 `probe_entry`/`watch_detail`(desc.py)、`watch_miid_clicks`(miid.py)、`_json_scan_for`(favorite.py)。
- 保留 5 个调试工具:`taobao_debug_detail`/`taobao_debug_home`/`taobao_debug_pages_watch`/`taobao_debug_collect`/`taobao_debug_favorite`。

### 查询分离(粗查定位 → 细查对比,用户要求)
- **粗查定位**:`taobao_search` + `taobao_fetch_product`——绝不碰收藏、绝不重新生成 mi_id,纯筛选对比候选。
- **细查对比**:`taobao_fetch_detail(miid_source="favorite")` **仅对短名单商品用**——每次真实模拟点击生成全新 mi_id。
- **`fetch_detail` 默认改为 `miid_source="config"`**(静态 mi_id、零收藏操作)→ 粗查阶段误调也不会触发收藏。要细查必须显式 `miid_source="favorite"`。

### 模拟点击 + 其他追踪参数(风控确认)
- **是的,已实现真实模拟点击**:收藏夹卡片用 Playwright 真鼠标点击 → 新标签页带**该渠道的自然追踪参数**(`mi_id` 全新 + `spm=tbpc.mytb_itemcollect.item.goods` + `upStreamPrice` + `sku_properties`),抓详情**直接在那个被点击页面上进行**(所有参数原样保留,不做二次干净跳转)。
- `ali_trackid`/`priceTId`/`utparam` 是**搜索/广告渠道**的每次点击变量,收藏夹渠道点击天然不带——我们**不伪造**(制造假追踪比缺失更可疑,与"不人为制造变化 token"原则一致)。每次查询 = 一次真实渠道点击 = 全新 mi_id + 该渠道参数,足迹自然。

### 模拟点击抖动(2026-08-18 六补)
- 新增 `pacing.human_click(page, locator)`:随机落点(偏中心 ±)、动画移动路径(steps=10~24)、微抖动(±2.5px)、可变 hover 停顿与按住时长、down+up。替代 Playwright 原生 `click()`(瞬移中心+零抖动)。
- **注意**:整个卡片 box 内随机落点会点到悬浮按钮(进入店铺/按图找相似)或删除按钮 → 不导航。**改为点卡片内 `.title` 标题元素**(商品链接的自然目标),抖动围绕标题中心。实机验证:human_click(标题)→ 新标签页 → 全新 mi_id,opened_id 匹配。
- 应用到收藏链路全部模拟点击:ensure_favorited/ensure_unfavorited 的 `#collectBtn`、click_from_favorites 与 recon_collect 的收藏卡片标题。
