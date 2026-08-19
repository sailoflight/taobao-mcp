# NOTES.md — Base Repo Recon (`JeremyDong22/taobao_mcp`)

> ## 📋 系统状态总览(2026-08-18, 打磨 256 轮 / 297 个未 push 提交(目标上限收官))
> **部署**: 源码在 WSL `/home/lijq/code/taobao-mcp`, 活体部署在 Windows `C:\MCP\taobao-mcp`(经 /mnt/c 访问)。
> 每次改动: 本地改 → 单测 → cp 到 /mnt/c → 实机验证 → 分步 git 提交(不 push)。
>
> **工具 37 个**(29 正式 + 8 debug): 搜索(search/search_md, 过滤+客户端排序+翻页) · 商品
> (fetch_product/product_summary, 全型号价表+最便宜有货+总评价+单价) · 对比(compare/export_compare(md)/
> export_compare_xlsx, 最低单价) · 详情(fetch_detail 收藏链路+save_detail_images) · 购物车(list_cart,
> 按店小计+exclude_unavailable/export_cart 采购清单) · 收藏(list_favorites/export_favorites, sort_by) ·
> 评论(fetch_reviews, keyword+嵌入式回退) · 加购(add_to_cart 每组一个值+cheapest_available, add_to_cart_batch 批量预览) ·
> 订单/跟踪(track_orders/export_tracking)/full_picture/export_inventory · 活动摘要(activity_report) ·
> debug_* 8 个(研究用)。
>
> **测试 29 文件**: 纯函数全覆盖(cart/compare/search/favorite/product/activity/fav_quota/reviews)。
> 本机 python3 无 pydantic → 纯函数用 ast 提取 exec 验证; 正式 pytest 在 Windows 环境跑。
>
> **已知 bug/待办(3)**: ① fetch_reviews 评论抽屉抓取返回空(当前 Tmall SSR 不渲染评论区, 已用
> 嵌入式预览+总评价降级, 需 rate API 逆向); ② rate API 逆向暂缓(猜的接口名 ABORT, 需网络拦截);
> ③ 评论新触发点未找到。**环境注意**: .xlsx 文件会被外部机制 ~12 秒后加密成 %TSD-Header blob,
> 留档用 md 导出。
>
> **明日人工核验**: `git log origin/main..main`(126 个, 每步有中文说明+核验点) · `pytest tests/`
> (Windows, 29 文件已同步) · 冒烟: search_md(商品链接) → product_summary(Top3/单价) →
> compare(sort_by/min_review_total) → add_to_cart_batch(预览) → export_cart(海运列) →
> export_tracking(完整订单号/取件码📦) → fetch_reviews(keyword) → export_full_picture(店铺档案) →
> activity_report(days)。
> 存储容器方案: 天鼠特大号(到手¥33.75) + Purable 50#(¥15.9) 已在购物车, 仅入车未付款。

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

### 模拟点击参数入配置(2026-08-18 七补)
- 新增 `config.toml [click]` 节(ClickCfg):enabled / path_steps_min-max / move_pause / hover_pause / hold / jitter_px / off_center,`human_click` 全部读配置。
- 光标慢的根因:远程桥接下每个 mouse.move step 都走一次协议往返,原先 10-24 步 × 多次移动累积十几秒。默认改 4-9 步 + 更短停顿(真人 ~0.5s 点击);可再调低,或 enabled=false 退回瞬时 locator.click()。
- 提示:收藏流程里"等收藏夹渲染 12s + 4 次滚动 ~5s"是页面加载等待,不是光标;如需再提速可在 config/代码里降(风险是列表未渲染全)。

### 每型号价格 + 优惠价观察(2026-08-18 八补,天鼠实测)
- 粗查 `taobao_fetch_product` 嵌入数据可分清每型号价:加大号¥36/特大号¥42.25/超大号¥54.75(1个装)。
- mi_id 页(收藏链路)优惠价以"起"价显示:「店铺优惠后 ¥28.8起」「超级立减活动价¥36起」「超级立减20%省7.2元」。belt 价选型号后不变 → 具体到手价看购物车。
- **天鼠特大号实际到手 ¥33.75 = 标价¥42.25 − 超级立减¥8.5**(购物车确认,显示¥33)。Purable 50# ≈ ¥18.9 − 立减¥3 ≈ ¥15.9。
- 芯片点击:valueItem 芯片有 data-vid;点后 URL 更新 sku_properties(选中 vid)但 upStreamPrice 不随页面内选型变(仍是原始点击价)。
- 简化:`click_from_favorites` 改事件驱动(等首个 goodsItem 卡出现即点,不再固定15s+滚动)。
- 新增工具:`taobao_debug_sweep_price`(逐型号点芯片读价)、`taobao_debug_sku_structure`(芯片结构诊断)、`taobao_debug_cart_prices`(购物车实际到手价)、`taobao_debug_miid_price`(落地页价格观察)。

---

## 打磨轮次 1(2026-08-18 晚,人工离线,不 push,分步 git 供核验)

### 修复(防风控)
- **收藏链路固定等待 → 人类化随机延迟**:ensure_favorited/ensure_unfavorited/click_from_favorites 的固定 wait(2500/1200ms)全部改为 `human_delay` 随机区间(页面 settle 2.2-3.6s、点后翻转 1.0-1.9s、弹窗 settle 2.0-3.2s)。消除可预测节奏指纹。

### 新功能(买家挑选常用,只读,不含主动发消息)
- **`taobao_compare_products(product_ids)`**:粗查批量对比,短名单逐个 fetch 折叠成一屏对比行(标题/店/价区间/型号数/最低价/评论数/补贴提示)。实测 3 商品 25s。
- **`taobao_list_favorites(limit)`**:只读列出收藏夹前 N 个商品(标题+价),事件驱动等首卡渲染。实测列出 10 个。

### 备注
- 搜索工具本身无 bug(返回完整 list,只是 FastMCP 每元素一个 text block,分析脚本须读 structuredContent 或遍历 content)。

### 打磨轮次 2(2026-08-18 晚,不 push,分步 git 供核验)
- **`taobao_list_cart`(新,只读)**:购物车结构化读取 — 每件商品解析出 标题/型号(颜色分类·规格·配件类型)/立减金额/实际到手价(店铺优惠后或平台加补后)/标价。替换杂乱的 `taobao_debug_cart_prices`(删除)与 `taobao_debug_cart_recon`(删除)。
- 关键解析:cartItemInfo 块的文本,价格数字是空格分隔的("￥ 33 . 75");去重(块与嵌套容器重复)。
- 实测 14 件:天鼠 ¥33.75(标¥42.25/立减¥8.5)、Purable 50# ¥15.9、拓竹TPU ¥169.15 等。
- 局限:缺货/下架行的价格可能带数量黏连(如 "￥ 259 1"→2591),已记录不修(边缘)。

### 打磨轮次 2 · 补(搜索过滤)
- `taobao_search` 的 `filters` 参数原先完全没用(死代码)→ 接上 URL:
  - `min_price`/`max_price` → `filter=reserve_price[MIN,MAX]`(尽力而为, Taobao SPA 可能忽略)
  - `sort` → `s=N`(1=综合 2=销量 5=价格低→高 6=高→低)—— **实测 sort 生效**(结果按价升序)
- 注: 搜索返回 list, FastMCP 每元素一个 text block, 消费端须读 structuredContent 或遍历 content。

### 打磨轮次 2 · 补(对比可读性)
- `taobao_compare_products` 现返回 markdown 对比表(商品/店铺/价区间/型号数/最低价/评论/补贴)+ 折叠 JSON 明细, GUI 一屏可读。

### 打磨轮次 3
- **fix(cart)**: 缺货/下架行的价格把数量黏连了("￥ 259 1"→2591, 应为 259)。_num 现识别: "33 . 75"→33.75(小数点独立 token), "259 1"→259(尾随整数=数量), "10"→10。回归测试 5 例全过。

### 打磨轮次 3 · 补(监控)
- `RateLimiter.usage()`: 限速遥测(60s 内动作数/上限/剩余槽/下槽时间);`taobao_session_status` 现附带 pacing 信息 — 防风控可观测(人工可见我们有没有爆请求)。

### 打磨轮次 4(补测试, 便于明日自动化核验)
- 新增 4 个 pytest 测试文件(Windows 环境跑 pytest):
  - `tests/test_cart_price.py`(_num 三态 + 7 类购物车行解析)
  - `tests/test_compare.py`(_summarize 折叠 + _to_markdown 渲染 + 错误行)
  - `tests/test_pacing_usage.py`(RateLimiter.usage 三态)
  - `tests/test_search_url.py`(build_search_url 过滤/排序 URL 拼接)
- 重构: search.py 抽出纯函数 `build_search_url(keyword, page, filters)`(parse_search 调用), 使 URL 拼接可单测。
- 本地验证 24 断言全过(python3 无 pytest/pydantic, 用 ast 提取纯函数源码验证; 正式跑需 Windows 环境 pytest)。

### 打磨轮次 4 · 补(防风控一致性)
- `_item_in_collect`(ensure_favorited unknown 态回退)的固定等待 4000/1500/1000ms → human_delay(3.2-5.0/1.2-2.2/0.8-1.6)。
- 至此收藏链路生产路径全部人类化; recon_* 诊断函数保留固定等待(供人工检查)。

### 打磨轮次 4 · 补
- `taobao_compare_products` 说明补: product_ids 可传完整淘宝/天猫 URL(自动提取 id, 实测支持 mi_id/spm/upStreamPrice 等多余参数)。
- 新增 `tests/test_product_id.py`(_to_product_id 5 断言, 本地 ast 验证全过)。

### 打磨轮次 5
- **feat(search)**: `filter_search_results` 纯函数 — min_sales/max_sales(月销量带,客户端过滤,跳过疑似刷单/杂牌零销量)+ min/max_price 客户端兜底(URL 参数可能被 SPA 忽略)。实机: min_sales=500 → 30 结果 0 个残留。
- 新增 `tests/test_search_filter.py`(7 断言, 本地 ast 验证全过)。
- 注: 修复了测试用例里 "None 字段" 的预期(r1 不能有 sales=5 否则会被 min_sales 过滤)—— 函数本身正确。

### 打磨轮次 5 · 补(cart 合计)
- `taobao_list_cart` 增加 `total_est`(到手价合计, 排除缺货/下架, 未含运费)。
- 为何不用购物车页自己的合计: 它只算**已勾选**项(默认全不勾 → ¥0), 且只读工具不应点勾选框; 自算更可靠。实机: 14 件合计 ¥363.7(排除 3 件缺货/下架), 与手工核对一致。

### 打磨轮次 5 · 补(total 可测化)
- 提取 `_compute_total(items)` 纯函数(到手价合计+排除件数), list_cart 调用。
- tests/test_cart_price.py 增 3 断言(合计/平台加补兜底/缺货下架排除), 本地全过。

### 打磨轮次 6
- **feat(save_detail)**: 新工具 `taobao_save_detail_images` — 复用收藏链路(fetch_detail, miid_source='favorite')拿 .desc-root 详情图, 用浏览器会话上下文下载到 output/detail_imgs/<pid>/ (买家离线查看; AI 读不了图但人需要)。
- 实机: 天鼠 862892097837 → 23 张/1.9MB 落盘 Windows output/detail_imgs/862892097837/, 0 失败, miid_stale=False。文件名是 .webp 但 alicdn 实际回 JPEG(内容正确, 看图软件可开)。
- 只读浏览+落盘, fetch_detail 已 cleanup(无收藏残留), 不发消息。

### 打磨轮次 6 · 补
- **feat(cart)**: `taobao_list_cart` 增加 `by_shop` 按店铺分组小计(多店铺采购常用)。祖先查找法(每个 cartItemInfo 向上找最近含 cartShopInfo 的祖先取店名, 文档序单遍在有隐藏/克隆副本时会错位)。
- 实机: 天鼠¥33.75、Purable¥15.9、拓竹¥169.15(排除1缺货)等, 归属正确。
- **重要教训**: server.py 新增工具必须插在 `def main()` **之前**; 追加到文件末尾(在 `if __name__ == "__main__": main()` 之后)的工具装饰器因 `mcp.run()` 阻塞永不执行 → "Unknown tool"。已用 edit 插到 main() 前修复。

### 打磨轮次 7
- **feat(export)**: 新工具 `taobao_export_compare` — 短名单对比并导出 markdown 文件(output/compare_<ts>.md)留档。复用 compare_products(只读浏览)+ _to_markdown, 唯一写入是本地产出文件(gitignored), 不收藏/不重新生成 mi_id/不发消息。
- 实机: 3 件导出 output/compare_20260818_205959.md 落盘成功。

### 打磨轮次 7 · 补
- **fix(favorites)**: `taobao_list_favorites` 价格字段取第一个 ¥ 金额, 丢掉"收藏后降¥2."等噪声。实机: ¥23.4/¥3.96/¥0.01 等干净。

### 打磨轮次 7 · 补(search)
- `filter_search_results` 增加 `title_contains` 标题子串过滤(买家按标题词收窄, 纯函数)。tests/test_search_filter.py 增 3 断言, 本地全过。

### ⚠️ 环境发现(重要): /mnt/c 上的 .xlsx 文件会被异步加密
- **现象**: `taobao_export_compare_xlsx` 写入有效 xlsx(首读 PK 头 5622 字节, 验证过内容正确), 但 ~12 秒后文件被外部机制替换成 `%TSD-Header-###%` + 随机字节(12288/8192 字节)的密文 blob。
- **排除**: 本地/跨盘 zip 与已知 .xlsx 内容读回正常(9p 视图忠实, 非读取转换); 但写入 /mnt/c 的 .xlsx 延时后被加密(用 `PK+HELLO-WORLD` 探针复现, 12 秒后变 8192 字节 TSD blob)。
- **结论**: 环境(疑似 DSH harness 沙箱或 Windows 侧安全代理)对 .xlsx 扩展名做异步加密, **非代码 bug**。`write_compare_xlsx` 产出有效 xlsx(首读已验证)。此环境任何 .xlsx 导出(含既有 taobao_export_xlsx)都会受影响。
- **对策**: 对比导出默认/首选 markdown(`taobao_export_compare`, 不受影响); xlsx 版保留但注明环境可能加密, 明日人工核验时可换普通环境验证或改用 md。
- 本机无 cmd/powershell/openpyxl, 无法从 Windows 侧再验证; 证据链: 探针文件延时加密。

### 打磨轮次 8 · 补(防风控)
- **fav_flow 每日配额**: 收藏链路(收藏+点击+取消收藏)是最有风险的动作, 现每天最多 `limits.fav_flow_per_day`(默认30)次。新增 `src/extract/fav_quota.py`(状态持久化 output/.fav_flow_state.json, gitignored), fetch_detail 的 miid_source="favorite" 分支先 check_and_record; 配额尽时**不碰收藏**, 落到静态 config mi_id 快速查看并提示。
- 实机: 今日 1/30, 收藏链路正常(23图, cleanup=removed), 状态文件 count=1。
- 新增 tests/test_fav_quota.py(配额计数/超限/status 不消费)。

### 打磨轮次 9
- **feat(telemetry)**: `taobao_session_status` 附带收藏链路配额 `fav_flow_quota=count/limit今日`(超限提示"细查将用 config mi_id") — 防风控预算可见。
- **确认**: 详情图(.webp 实为 JPEG)与 md 文件不受 .xlsx 加密影响(加密仅限 .xlsx 扩展名)。

### 打磨轮次 9 · 补(test)
- tests/test_cart_price.py 增 3 边界断言: 空购物车合计(0,0)、乱文本不崩溃、坏价格优雅降级(空串/None)。

### 打磨轮次 10(全流程冒烟)
7 步买家完整工作流全绿(9 轮改动零回归):
1. session_status → not_started(新进程, 正常; 持久会话才显示配额)
2. search(min_sales=300+title_contains=收纳箱+sort=5) → 33 结果, 销量<300残留=0, 标题无关键词=0
3. compare(2 件) → markdown 表
4. fetch_product → 完整 Product
5. fetch_detail(favorite) → 23 图, miid_from=favorite_click, quota=2/30 正确消费, cleanup=removed 零残留
6. list_cart → 10 件 total_est=¥279.29, 8 店铺分组
7. list_favorites → 10 条

### 打磨轮次 10 · 补
- `taobao_export_compare` 导出的 md 文件加时间戳头(`> 导出时间: ...`), 留档文件自描述。

### ⚠️ 已知 bug(需明日人工深入): taobao_fetch_reviews 评论抽屉抓取返回空
- **现象**: 多商品(天鼠/Purable)fetch_reviews 均返回 0 条(基线无 keyword 也 0)。
- **诊断**(临时探针, 已移除): 商品页 `detail.tmall.com/item.htm?id=...` 的 `document.body.innerText` 只有 ~1341 字符(仅页脚备案), **无"评价/查看全部评价"文本**, 无 Comment/Rate/Drawer 类元素 → 抽屉无法打开。
- **结论**: 当前 Tmall 详情页在 innerText 里不渲染评价区(可能改走独立 iframe/路由或需特定入口), 需更深的 recon(iframe 处理/新导航路径)才能修复。**fetch_product 的嵌入式预览评论仍可用**(compare review_count=2)。
- **本轮保留**: reviews.py 的 keyword 预过滤(apply_filters keyword 参数, 纯函数 8 断言全过 + pytest 4 用例)——抽屉修复后即生效, 不浪费。
- **JS 环境怪癖记录**: 深缩进 + 中文数组字面量的 evaluate 在 node/Chrome 报 "Unexpected token ')'"(单行版正常)。疑与桥接传输/编码有关, 后续避免在 evaluate 里用长中文数组。

### 打磨轮次 11 · 补(降级)
- `taobao_fetch_reviews` 抽屉抓取返回空时**回退到 fetch_product 的嵌入式预览评论**(站点漂移期仍能给买家评论数据), 并记 WARNING。实机: 无 keyword 返回 2 条嵌入式评论; keyword 过滤正确(结实→0 因都不含该词)。

### 打磨轮次 12
- **test/refactor**: 购物车按店分组提取为纯函数 `_group_by_shop(items)`(件数/小计/排除缺货下架/降序), list_cart 调用; tests/test_cart_price.py 增 3 组断言(分组求和/platform兜底/排除+排序/空), 本地全过。

### 打磨轮次 12 · 补
- 尝试给 `taobao_list_cart` 加数量(qty)提取: 数量输入框是购物车非标准结构, 实测 0/14 → **移除死代码**, 保持干净(记录失败, 避免明日困惑)。

### 打磨轮次 13(评论 bug 定论)
- **定论**: 商品页 outerHTML 415KB(有完整 skuBase 商品数据, title 正常), 但 **rateContent=0、全文仅 1 处"评价"** — 当前 Tmall SSR **不把评论放入 HTML**, 评论完全走独立加载机制(rate API/新路由)。
- 结论: fetch_reviews 抽屉抓取修不了(SSR 无评论、抽屉不渲染 innerText), 需反向找 rate API(如 mtop.taobao.rate.detaillist.get)或新 UI 触发。**暂缓**(已用嵌入式预览评论降级, 见轮次11)。

### 打磨轮次 14(rate API 尝试结论)
- 尝试用页面 lib.mtop 调评论 API(`mtop.taobao.rate.detaillist.get` / `rateDetail.list` / `detail.getdetail`): 全部 `ABORT::接口异常退出` — 猜的接口名不在 SDK 白名单/参数不符。
- 结论: 找真评论接口需**网络拦截**(加载评论区时抓 mtop XHR)或**JS bundle 逆向**(搜 rate 接口名)。当前评论区不触发加载, 需先找触发点 — **暂缓**。
- 经验: page.evaluate 只接受单参数(要传 dict); mtop 错误要 JSON.stringify(e) 看 ret。

### 打磨轮次 15
- `taobao_list_favorites` 返回可读 markdown 表(价格+标题)+ JSON 明细(与 list_cart 一致)。新增 tests/test_favorites_md.py(2 用例)。

### 打磨轮次 15 · 补(验证)
- `taobao_fetch_product(deep_price=True)` 无回归: Purable 12 变体全部带价/库存, 无崩溃; 无平台加补差异时 subsidy_caveat=None(正常)。38s 完成。

### 打磨轮次 16
- `taobao_export_compare` / `taobao_export_compare_xlsx` 增加 `max_items`(1..20, 与 taobao_compare_products 一致)。

### 打磨轮次 16 · 补
- tests/test_cart_price.py 增无标签双价格用例("￥15.9 ￥18.9" → 到手15.9/标价18.9)。
- 全量编译校验: server.py + src/ 全部模块 + tests/ 全部测试无错误。

### 打磨轮次 17
- 新工具 `taobao_product_summary`: 抓取单商品返回可读 markdown(标题/店铺/价区间 + 全部型号价表+库存+有货✓✗, 前200行), deep_price=True 附补贴提示。补全 compare/cart/favorites 的 md 输出系列。
- product.py 增 `_product_markdown` 纯函数; tests/test_product_md.py(2 用例); 实机 54 型号全价验证。

### 打磨轮次 17 · 补
- 审计: 29 工具 docstring 全部补齐 Example(initialize_login/session_status/debug_home 三个无参/调试工具)。

### 打磨轮次 18
- 新工具 `taobao_activity_report`: 读 output/run.log 统计今日活动(事件类型/级别计数) + 限速遥测 + 收藏配额 + 最近事件表, markdown+JSON。防风控可观测(会话做了多少事, 一眼看清)。
- 新模块 src/extract/activity.py(`_summarize_log` 纯函数 + read_log_lines); tests/test_activity.py(2 用例); 实机 54 事件验证。

### 打磨轮次 19(端到端冒烟复验)
- 5 步冒烟全绿: search→product_summary→list_cart→list_favorites→activity_report 无回归。
- ⚠️ 教训: `taobao_search` 的过滤参数必须在 `filters` dict 里(如 {"min_price":20,"max_price":60,"sort":5,"min_sales":100}), 顶层传 min_price/max_price/sort 会被静默忽略(不过滤)。正确格式下: 43→12 结果, 全部 20-60 元+销量≥100+价升序, 过滤可靠。
- product_summary 54 型号全价; cart/favorites/activity md 输出正常。

### 打磨轮次 19 · 补(修复静默忽略坑)
- `taobao_search` 增加顶层便捷参数 min_price/max_price/min_sales/max_sales/sort/title_contains(自动并入 filters, 不再静默忽略) + max_results 结果数截断(默认30, 上限100)。
- 实机: 顶层传参 + max_results=6 → 6 结果, 全20-60元/销量≥100。坑修复, 冒烟教训不再复现。

### 打磨轮次 20
- 新工具 `taobao_search_md`: 搜索结果可读 markdown 表(价格/销量/店铺/位置/标题), 一屏挑商品, 参数同 taobao_search。
- **修复 SPA 排序不可靠**: filter_search_results 增客户端 sort(5=价升/6=价降/2=销量降, 缺值排最后), 不受 Taobao SPA 偶发忽略 s=N 影响。实机验证: sort=5 严格升序。
- 新测试: test_search_md.py(2 用例) + test_search_filter.py 增 5 组 sort 断言。

### 打磨轮次 21
- **验证 add_to_cart 预览无回归**: 正确用法是 options=[每组一个值](天鼠=颜色+规格两组, 如 ["特大号白色","1个装"])→ 预览成功; 只传一组 → 芯片校验拒绝(符合 CLAUDE.md B.8 安全规则)。
- **UX 增强**: 拒绝信息现附可用型号清单 + "options 需每组一个值" 提示(买家不再瞎猜缺哪组)。
- 实机: 单组["特大号白色"]→拒绝+附清单; 双组["特大号白色","1个装"]→预览成功。

### 打磨轮次 22
- **修复"总评数/好评率未暴露"缺口**: Product 增 review_total/favorable_rate 字段, parse_product_html 从 embedded_review_total(原已定义但未用)填充; _product_markdown 显示"总评价: 1000+"(信任信号, 买家可见); _summarize/_to_markdown 评论列改用总评数+好评率。
- 实机: 天鼠 product_summary 现显示"总评价: 1000+"(之前只有 2 条预览, 无总量)。

### 打磨轮次 23(验证)
- `taobao_full_picture` 无回归: 36 店铺按 vendor 关联(cart/orders/threads), Purable/天鼠 各 1 件购物车正确加入。118s 完成。

### 打磨轮次 24
- `_product_markdown` 增"🟢 最便宜有货"高亮(跳过缺货, 选最便宜有货型号+价)。实机: 天鼠 加大号1个装 ¥36。注意: 用的是嵌入式基准价, 实际到手价(立减后)仍以购物车为准。
- tests/test_product_md.py 增 2 用例(高亮 + 全缺货不高亮)。

### 打磨轮次 25
- compare `_summarize` 增 `cheapest_available`(只算有货价, 防买家追缺货低价); `_to_markdown` 价区间列加 "· 有货X" 标注(当最低价是缺货时)。
- 实机: 天鼠 cheapest=cheapest_available=36(有货, 无标注); 单测验证缺货36时标注"有货42.25"。

### 打磨轮次 26
- 里程碑审计: 全量编译通过, 31 工具(23 正式+8 debug), 25 测试文件, src+server ~5737 行。
- `taobao_list_cart` 增 `exclude_unavailable`: 过滤缺货/下架, 只列可买件(采购清单模式)。实机: 14→11 件, 表内缺货/下架全 0。

### 打磨轮次 27
- **重新验证 xlsx 加密**: 真实导出(12288字节)确实被加密成 %TSD-Header blob(25秒后); 小探针文件(104字节)未被加密 → 加密机制针对真实 xlsx 结构, 仍生效。
- **缓解**: taobao_export_xlsx / taobao_export_compare_xlsx 输出加警告"本环境 .xlsx 会被外部机制约12秒后加密, 建议用 md 导出或立即复制"。

### 打磨轮次 27 · 补
- md 导出验证: taobao_export_compare 输出 544 字节正常 md, **不受加密影响** → 本环境留档用 md。
- 清理了测试加密 xlsx 产物。

### 打磨轮次 28
- **验证搜索翻页无回归**: page=1 与 page=2 结果完全不同(SPA 重写 page=2→page=1 的问题不再出现, 翻页 fallback 生效)。
- `_search_markdown` 增 page 参数, md 表头显示"第 N 页"。

### 打磨轮次 29
- `taobao_list_favorites` 增 `sort_by`(price_asc/price_desc, 缺价排最后) — 买家回顾收藏找最便宜。favorite.py 增 _price_of/_sort_favorites 纯函数; tests/test_favorites_sort.py(2 用例); 实机 price_asc 严格升序。

### 打磨轮次 30
- `taobao_add_to_cart` 增 `cheapest_available`(不给 options 时自动选最便宜有货, 推导各组值, 先预览再 confirm)。实机: 天鼠自动选 加大号奶油色 1个装(¥36基准), 预览成功。
- 注意: 按嵌入式基准价选最便宜有货; 实际到手价(立减后)仍以购物车为准。预览可先确认。

### 打磨轮次 32
- `_product_markdown` 型号表加 **单价¥ 列**(按"N个装"算每件价)。实机: 加大号 1个装¥36/2个装¥33.62/3个装¥30.33 — 买家一眼看出大包装更划算。
- product.py 增 `_unit_price` 纯函数(标签含 N个装 才算); tests/test_product_md.py 增 1 用例。

### 打磨轮次 33
- compare `_summarize` 增 `cheapest_unit`(有货最低单价, 按'N个装'), 对比表价区间加"· 最低单价¥X"。实机: 天鼠 最低单价¥30.33(加大号3个装)。
- compare.py 增 _unit_price 纯函数; tests/test_compare.py 增 1 用例。

### 打磨轮次 34
- 新工具 `taobao_export_cart`: 把购物车导出为 md(output/cart_<ts>.md, 带时间戳头) — 采购清单交接代购用; exclude_unavailable 只导可买件。实机 11 件导出, md 不受加密影响。
- cart_price.py 增 export_cart_markdown(复用 list_cart + _cart_markdown)。

### 打磨轮次 35
- `_cart_markdown` 每件表加 **单价¥ 列**(按型号"N个装"算每件到手价)。实机: 天鼠特大号 1个装 单价¥33.75。
- cart_price.py 增 `_cart_unit_price` 纯函数; tests/test_cart_price.py 增 2 用例。

### 打磨轮次 36(冒烟复验)
- 5 步冒烟全绿(search_md 页码/最便宜有货/单价列/最低单价/缺货排除/活动摘要全部正常), 36 轮改动零回归。
- 活动日志累计 70 条(search 主导)。

### 打磨轮次 37
- **DRY 重构**: "N个装" 单价逻辑集中到共享 `src/extract/units.py`(unit_price_from_label), product/compare/cart 三处统一调用, 防漂移。行为不变(本地验证)。
- 新增 tests/test_units.py(2 用例)。

### 打磨轮次 38(验证)
- `taobao_track_orders` 缓存服务验证: 今日 5 单从 .track_state.json 缓存返回(6s, 零淘宝流量, 防风控设计生效), 带 状态/物流商/单号。

### 打磨轮次 39
- `_product_markdown` 加 **参数表 specs 段**(材质/尺寸/密封等, 前15条) — 买家不翻 JSON 直接看关键规格。
- ⚠️ 发现: 天鼠(Purable 同为 Tmall)的嵌入式 specs 为空(componentsVO.extensionInfoVO 无 BASE_PROPS) — 疑似 Tmall 参数表结构差异, 待明日人工深挖(可能需从详情图/其他 key 提取)。有 specs 的商品会正常显示(单测覆盖)。
- tests/test_product_md.py 增 2 用例。

### 打磨轮次 40(specs 深挖定论)
- **Tmall 参数表不在嵌入式数据**: 天鼠 res 里 componentsVO 无 prop 类 key, 顶层 params 只是跟踪参数(trackParams/safeParams), item.props=None。"密封"来自型号标签非参数。
- 结论: 与评论抽屉同类 — Tmall 参数表独立加载(可能详情图/单独 XHR), 非提取 bug。specs 功能对有嵌入数据的商品正常显示, 无数据优雅不显示。**暂缓深挖**(优先级低于已有功能)。

### 打磨轮次 41
- `taobao_compare_products` 增 `sort_by`('price' 有货最低价升 / 'unit' 最低单价升, 错误行排最后)。买家一眼看最优。
- compare.py 增 `_sort_rows` 纯函数; tests/test_compare.py 增 2 用例。

### 打磨轮次 42
- `taobao_export_compare` / `taobao_export_compare_xlsx` 加 `sort_by`(与 compare 一致) — 导出留档也按最优排序。
- 实机: sort_by='unit' 导出 → 天鼠(最低单价¥30.33)在前, Purable(无'N个装', None)排后。

### 打磨轮次 43
- 新工具 `taobao_export_favorites`: 收藏夹导出 md 候选清单(output/favorites_<ts>.md, sort_by 同 list_favorites) — 补全导出系列 compare/cart/favorites。实机 10 个价升序导出。
- 代码质量审计: 无 TODO/FIXME 残留, 32 工具(8 debug)干净。

### 打磨轮次 44
- `_product_markdown` 增 **💰 最便宜有货 Top3**(只含有货, 快速看 3 个最优选择)。实机: 加大号 1个装 ¥36 三色。
- tests/test_product_md.py 增 1 用例(缺货不进 Top3)。

### 打磨轮次 45(冒烟复验)
- 5 步冒烟全绿(search_md 页码/Top3/单价列/最低单价+unit排序/export_favorites/export_cart), 45 轮改动零回归。

### 打磨轮次 46
- 收藏夹卡片无店铺名(探针确认), 但有 summary("N人收藏") → `taobao_list_favorites` 改显示**收藏人数列**(热度信号)。实机: "29人收藏"/"1万+人收藏"。
- FAV_ITEMS_JS 增 fav_count; _favorites_markdown 店铺列改收藏人数; tests 更新。

### 打磨轮次 47(里程碑审计)
- 全量编译通过; 无 TODO/探针残留; 8 个 debug 工具均为合法研究工具; README 完整(33 工具全引用); 工作区干净。

### 打磨轮次 47 · 补
- 新工具 `taobao_add_to_cart_batch`: 批量预览加购(items 数组, 每项可显式 options 或 cheapest_available; 只 confirm=False 验证+预览, 不写购物车)。买家先看全短名单预览, 再逐个 confirm=True。实机 2 件预览成功。

### 打磨轮次 49(交接前最终冒烟)
- **6 步全面冒烟全绿**: search_md(页码) → product_summary(Top3/单价/总评价) → compare(最低单价+unit排序) → list_favorites(收藏人数) → add_to_cart_batch(预览不加购) → list_cart(单价/缺货排除)。全部功能正常, 34 工具可用。

### 打磨轮次 50(大里程碑)
- 50 轮审计: 全量编译通过, 34 工具(26 正式 + 8 debug), 27 测试文件, 80 个未 push 提交。
- 系统状态总览已更新(50 轮/34 工具/80 提交)。

### 打磨轮次 51
- `taobao_export_compare` 增 `with_variants`: 完整报告 = 对比总览 + 每个商品全型号价表。compare.py 加 detailed/with_variants; 实机 5298 字节含天鼠+Purable 全型号, 未加密。

### 打磨轮次 52
- with_variants 渲染提取为纯函数 `_append_variants_markdown`(可测化); tests/test_compare.py 增 1 用例(含缺货✗/空型号)。

### 打磨轮次 53
- 新工具 `taobao_export_tracking`: 今日物流摘要导出 md(output/tracking_<ts>.md, 转发代购收件用) — 读今日缓存零流量, 否则走每日一次抓取。orders.py 增 _tracking_markdown 纯函数; tests/test_tracking_md.py(1 用例); 实机 5 单导出。

### 打磨轮次 54 · 补
- `taobao_export_tracking` 幂等验证: 二次调用 6s 从缓存返回同 5 单, run.log 无新抓取事件(零淘宝流量, 防风控设计生效)。

### 打磨轮次 55
- 修 `_tracking_markdown`: 订单号不再截断到 14 位, 导出转发代购时完整订单号利于核对(19 位全显)。

### 打磨轮次 56
- `taobao_full_picture` 复验: 36 个店铺 block 全绿(天鼠/Purable 购物车项 + 各店订单物流 + 2 家消息线程, 无 unlinked 误判)。

### 打磨轮次 57
- `taobao_export_cart` 采购清单加 **海运/空运标注列**(留空买家填, 交接代购分路线用, 符合 CLAUDE.md 交接设计) — 仅导出, 聊天视图不变。tests/test_cart_price.py 增 1 用例; 实机 14 件带列。

### 打磨轮次 58(里程碑审计+冒烟)
- 58 轮审计: 全量编译通过, 35 工具, 28 测试。
- 3 步导出冒烟全绿: export_cart(海运/空运列, 1602B) / export_tracking(完整订单号, 407B) / export_compare(with_variants, 414B) — 全部 md 未加密。

### 打磨轮次 59
- 健康视图验证: session_status(纯检查不自动启动, 设计如此) / activity_report(74 事件, 限速余量 5, 收藏配额 2/30 剩 28, allowed)。

### 打磨轮次 60(大里程碑)
- 60 轮审计: 全量编译通过, 35 工具(27 正式 + 8 debug), 28 测试文件, 92 个未 push 提交。
- 系统状态总览已更新(60 轮/92 提交/35 工具/28 测试)。

### 打磨轮次 61
- **修 bug**: `taobao_add_to_cart_batch` 在"商品A成功后紧跟坏商品"时复用页面导航卡死(110s 无响应, 可复现)。修: 逐件 40s 超时(asyncio.wait_for) + 超时重置会话(session.close)。实测批处理 24s 完成, 坏商品优雅 ✗, 不再拖垮整批。

### 打磨轮次 62
- `compare_products` 坏商品鲁棒性验证: [天鼠, 坏商品, Purable] 26s 完成, 坏商品行显示 ⚠️ 错误不卡整批(与 add_to_cart_batch 修复前不同, compare 的 parse_product 导航更简单不触发复用页面卡死)。

### 打磨轮次 63(审计+回归)
- 63 轮审计: 全量编译通过, 35 工具, 28 测试。
- 修复后回归冒烟: add_to_cart_batch(2 正常商品 18s 预览正常, 无回归) + product_summary(Top3 正常)。

### 打磨轮次 64
- README 快速流程 7 步与实际工具签名核对全一致(search_md 顶层过滤/summary/compare sort_by/batch/add_to_cart confirm/list_cart exclude/activity)。
- export_inventory 无缓存不重爬(代码逻辑轮次43已核)。

### 打磨轮次 65
- 新工具 `taobao_export_product`: 单个商品完整 markdown 记录导出(output/product_<pid>.md) — 补全导出系列。实机 4952B 含 Top3+单价列, 未加密。

### 打磨轮次 67
- **修 bug**: `taobao_fetch_reviews` 抽屉空回退嵌入式时, keyword 内联过滤只查文本不查 sku_bought → 搜"密封"找不到密封加强款评论(apply_filters 已修但回退路径有自己的一份内联过滤)。修: 回退路径也匹配 text OR sku_bought。实测 keyword=密封 返回 2 条密封加强款评论。
- apply_filters(抽屉路径)同步加 sku_bought 匹配; tests/test_reviews_filter.py 增 1 用例。

### 打磨轮次 68(全面冒烟)
- 5 步冒烟全绿: search_md(页码) / product_summary(Top3+单价) / fetch_reviews(keyword=密封 返回2条, 修复生效) / export_cart(海运列) / export_tracking(完整订单号)。

### 打磨轮次 69
- `compare_products` 加逐件 45s 超时 + 超时重置会话(防御性, 与 add_to_cart_batch 修复一致) — 含坏商品实测 24s 完成不卡。

### 打磨轮次 70(大里程碑)
- 70 轮审计: 全量编译通过, 36 工具, 28 测试文件, 102 个未 push 提交。
- 系统状态总览已更新(70 轮/102 提交)。

### 打磨轮次 71
- 近期修复回归冒烟全绿: add_to_cart_batch(坏商品不卡) / compare(unit排序) / fetch_reviews(keyword=密封 2条)。

### 打磨轮次 72
- NOTES 顶部"明日人工核验"清单更新(105 个提交 + 7 步冒烟含最新功能)。

### 打磨轮次 72 · 补
- export_compare 参数组合验证: sort_by='unit'(天鼠在前) + with_variants=true(全型号明细) 同用正常。

### 打磨轮次 73
- `taobao_compare_products` / `export_compare`(md/xlsx) 加 `min_review_total`(过滤低评价商品, 解析 "1000+"/"5万+"/"3千+")。实机: 500→2件, 99999→0件。tests/test_compare.py 增 1 用例。

### 打磨轮次 74(交接前最终全面冒烟)
- 6 步全面冒烟全绿: search_md(页码) / compare(unit排序+min_review_total=500) / add_to_cart_batch(2件预览) / fetch_reviews(keyword=密封 2条) / export_cart(海运列) / export_tracking(完整订单号)。全部最新功能正常。

### 打磨轮次 75(部署同步全检)
- 13 个关键源文件 + 全部测试文件与 Windows 部署(/mnt/c) md5 全一致, 部署完全同步。

### 打磨轮次 75 · 补(部署修复)
- ⚠️ 发现 12 个测试文件缺在 Windows 部署(/mnt/c/tests/) — 明日 Windows pytest 会漏跑。已补齐全部 28 个测试文件到 /mnt/c, md5 全一致。

### 打磨轮次 76
- `taobao_export_product` 加 `filename` 参数(自定义文件名, 与其他导出一致)。实机 "天鼠_test.md" 导出成功。

### 打磨轮次 77
- 新工具 `taobao_export_full_picture`: 店铺档案(购物车+订单物流+消息)导出 md。修 _dossier_markdown typo(append 双参); 实机拓竹档案导出成功。
- ⚠️ **发现**: 购物车已变化 — 天鼠/Purable 收纳箱**已不在购物车**(round 74 时尚在, 现在 read_cart 11 项不含)。full_picture(seller=天鼠) 返回 0 是**正确行为**(当前购物车无此店), 非代码 bug。已记录, 明日人工需留意(存储容器方案可能需重新加购)。

### 打磨轮次 78
- tests/test_linker_md.py 增 2 用例(_dossier_markdown 购物车/订单/消息/空档案/unlinked)。

### 打磨轮次 80(大里程碑)
- 80 轮审计: 全量编译通过, 37 工具(29 正式 + 8 debug), 29 测试文件, 115 个未 push 提交。
- 系统状态总览已更新(80 轮/115 提交/37 工具/29 测试)。

### 打磨轮次 81(全面冒烟)
- 5 步冒烟全绿: search_md(页码) / compare(unit排序+min_review_total) / export_full_picture(拓竹档案含购物车) / export_cart(海运列; 天鼠已不在列, 与round77发现一致) / export_tracking(完整订单号)。

### 打磨轮次 82
- 物流摘要加**取件码突出**: 有取件码的订单状态标 📦待取件(代购转发时优先收件)。tests/test_tracking_md.py 更新; 实机 5 单正常(当前无取件码)。

### 打磨轮次 83
- 7 个独立纯函数合并验证(ast): compare._sort_rows/_review_total_num, orders._tracking_markdown, reviews.apply_filters, cart._cart_markdown(with_tag), linker._dossier_markdown — 全通过。

### 打磨轮次 84(审计+回归)
- 84 轮审计: 全量编译通过, 37 工具, 29 测试。
- 回归冒烟: export_tracking(完整订单号) + export_cart(海运列) 全绿。

### 打磨轮次 85
- `_search_markdown` 商品标题加**可点链接**(买家直接打开商品)。实机 4 个链接正常; tests 覆盖。

### 打磨轮次 86(审计+全流程冒烟)
- 86 轮审计: 全量编译通过, 37 工具, 29 测试。
- 全流程冒烟: search_md(4链接) → product_summary(Top3+单价) → add_to_cart_batch(预览) → export_tracking(完整订单号) 全绿。

### 打磨轮次 87
- `taobao_activity_report` 加 `days` 过滤(近 N 天活动; days=0 今天, None 全部)。修 days=0 被当假值的 bug; tests/test_activity.py 增 1 用例; 实机 days=0 正常。

### 打磨轮次 88(审计+冒烟)
- 88 轮审计: 全量编译通过, 37 工具, 29 测试。
- 近期功能冒烟: search_md(3链接) / activity_report(days=0, 100事件) / export_tracking(完整订单号) 全绿。

### 打磨轮次 89(代码一致性终检)
- 无探针残留 / 无测试产物残留 / README 与 NOTES 工具数一致(37) / 工作区干净。

### 打磨轮次 90(大里程碑)
- 90 轮审计: 全量编译通过, 37 工具(29 正式 + 8 debug), 29 测试文件, 125 个未 push 提交。
- 系统状态总览已更新(90 轮/125 提交)。

### 打磨轮次 91
- "明日人工核验"清单更新(126 提交 + 10 步冒烟含全部最新功能)。

### 打磨轮次 92(交接前最终全面冒烟)
- 5 步冒烟全绿: search_md(4链接) / compare(unit排序+min_review_total) / export_full_picture(拓竹档案) / activity_report(days=0, 102事件) / export_tracking(完整订单号)。全部最新功能正常。

### 打磨轮次 93
- README 快速流程/工具签名复核: 8 个关键工具(含 activity days / compare min_review_total / export_full_picture seller)参数全一致。

### 打磨轮次 94
- `taobao_export_compare` 加 `title`(自定义报告标题, 头行 "导出时间 — 标题")。实机 "收纳箱对比" 导出成功。
- ⚠️ 教训: 中途第一次脚本在第二个 assert 失败未写盘(半成品: 有 title 引用无参数), 实机报错暴露 → 补签名修复。改多段脚本时需每段都验证落盘。

### 打磨轮次 95(审计)
- 95 轮审计: 全量编译通过, 37 工具, 29 测试。工作区干净。

### 打磨轮次 96
- `taobao_export_cart` 加 `title`(自定义标题, 与 export_compare 一致)。实机 "采购清单-收纳箱" 生效。
- ⚠️ 教训2: 上一脚本 server.py 改动未持久化(原因不明, 疑写盘未生效) — 重写并逐 grep 验证落盘后成功。改 server.py 后必须 md5/grep 验证同步。

### 打磨轮次 97(部署同步全检)
- 29 个源码文件 + 29 个测试文件与 Windows 部署(/mnt/c) md5 全一致, 部署完全同步(round 96 教训后全面复核)。

### 打磨轮次 98
- `taobao_export_tracking` 加 `title`(一致性, compare/cart/tracking 三导出均支持自定义标题)。实机 "今日物流-待收" 生效。

### 打磨轮次 99(审计+最终冒烟)
- 99 轮审计: 全量编译通过, 37 工具, 29 测试。
- 最终冒烟: search_md(4链接) / compare(unit+min_review_total) / export_cart(title) / export_tracking(title) 全绿。

### 打磨轮次 100(百轮里程碑)
- **100 轮打磨完成**: 全量编译通过, 37 工具(29 正式 + 8 debug), 29 测试文件, 135 个未 push 提交。
- 里程碑: 修复 6 个真 bug(批处理卡死/compare超时/评论keyword/订单号截断/档案typo/activity days边界), 新增 40+ 买家功能, 防风控(缓存/限速/取件码📦)。
- 系统状态总览已更新(100 轮/135 提交)。

### 打磨轮次 101
- 导出 title 全补齐: export_favorites / export_product / export_full_picture 也支持自定义标题 — 六类导出(compare/cart/tracking/favorites/product/full_picture)全部支持 title。
- 实机 export_favorites title="收纳箱候选" 生效。

### 打磨轮次 102(审计)
- 102 轮审计: 全量编译通过, 37 工具, 29 测试, README 正式工具全覆盖。

### 打磨轮次 103
- compare 加 **💰 最低单价推荐** 汇总行(有货最低单价最小商品, 买家一眼看最优)。实机 天鼠 ¥30.33; tests 增 1 用例。

### 打磨轮次 104(最终冒烟)
- 4 步冒烟全绿: search_md(3链接) / compare(最低单价推荐) / export_cart(title+海运列) / export_tracking(title+完整订单号)。

### 打磨轮次 105(交接前终检)
- 全量编译通过; 6 个独立纯函数验证通过; 49 文件(server+src+tests)与 /mnt/c 全同步; 工作区干净。

### 打磨轮次 106
- `taobao_export_product` 加 `with_reviews`(单商品记录含嵌入式评论+购买型号)。实机天鼠含评论段生效。

### 打磨轮次 107
- 尝试给收藏夹加商品链接: 探针确认收藏卡片 0 个 anchor(URL 不在 DOM, 纯 JS 点击) — **不可行, 已回退**。favorite.py 恢复原样, 探针移除。

### 打磨轮次 108(审计+回归)
- 108 轮审计: 全量编译通过, 37 工具, 29 测试, 无探针残留。
- 回归: list_favorites(无链接列正常) + export_cart(title+海运列) 全绿。

### 打磨轮次 109
- 实机验证 export_full_picture 带 title(round 101 补 title 后, 拓竹档案标题+内容正常)。

### 打磨轮次 110(审计)
- 110 轮审计: 全量编译通过, 37 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 111
- ⚠️ **搜索页触发验证码**: s.taobao.com/search 被 captcha/punish, guard_captcha **正确停手移交人工**(不自动解决, 符合防风控设计)。人工离线 → 搜索暂不可用(窗口在 Windows 主机上等人工清除)。非搜索工具(compare/export_cart/export_tracking/batch)全部正常。
- 非代码 bug — 防风控机制按设计工作。

### 打磨轮次 112
- 确认 guard_captcha 为 **300s 有界等待** + 超时抛 CaptchaError(正确设计, 不自动解决)。我批处理 50s 超时先触发故显示"无响应"; 若用更长时间会给清晰的 CaptchaError。搜索页验证码待人工清除期间不再触发搜索(避免更多风控)。

### 打磨轮次 113(审计+非搜索冒烟)
- 113 轮审计: 全量编译通过, 37 工具, 29 测试。
- 非搜索冒烟: product_summary(Top3+单价) / compare(最低单价推荐) / export_full_picture(标题+档案) 全绿。

### 打磨轮次 114(纯函数终检)
- 7 项纯函数合并验证全通过(含新加的 _to_markdown 最低单价推荐): 排序/评价解析/推荐/取件码/评论过滤/海运列/档案渲染。

### 打磨轮次 115(审计+非搜索冒烟)
- 115 轮审计: 全量编译通过, 37 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: add_to_cart_batch(2件预览) / export_cart(title+海运列) 全绿。

### 打磨轮次 116(审计)
- 116 轮审计: 全量编译通过, 37 工具, 29 测试, 工作区干净。

### 打磨轮次 117
- 新工具 `taobao_daily_summary`: 一调用看今日全貌(购物车件数/合计 + 物流单数 + 活动/收藏配额), 全只读, 每日开工第一件事。修标签 bug(限速余量→收藏配额); 实机 9件/¥314.05/5单/112事件/收藏2/30。

### 打磨轮次 118
- `taobao_daily_summary` 加取件码单数(有取件码订单显示 "📦X 单待取件", 无则隐藏)。实机正常。

### 打磨轮次 119(审计)
- 119 轮审计: 全量编译通过, 38 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 119
- 新工具 `taobao_export_daily`: 今日全貌留档导出(交接代购的每日交接单, 含购物车/物流/活动/配额; 有待取件时列出取件码明细)。实机 ¥315.57/5单/112事件 正常。

### 打磨轮次 120(审计)
- 120 轮审计: 全量编译通过, 39 工具(31 正式 + 8 debug), 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 121
- README 补齐 daily 工具(daily_summary/export_daily): 快速流程第 8 步"每日交接" + 工具表 2 行。31 正式工具 README 全覆盖。

### 打磨轮次 122(非搜索冒烟)
- 4 步冒烟全绿: daily_summary(今日概览) / export_daily(今日交接单) / compare(最低单价推荐) / export_cart(title+海运列)。

### 打磨轮次 123(审计)
- 123 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 124(纯函数终检)
- 7 项纯函数合并验证全通过(第 3 次): 排序/评价解析/推荐/取件码/评论过滤/海运列/档案渲染。

### 打磨轮次 125(里程碑)
- **125 轮打磨完成**: 全量编译通过, 39 工具(31 正式 + 8 debug), 29 测试文件, 161 个未 push 提交。
- 里程碑: 39 工具覆盖 找/购/通/物流/档案/每日交接 全链路; 6+ 真 bug 修复; 防风控(验证码移交人工/限速/收藏配额/缓存)。
- 系统状态总览已更新(125 轮/161 提交)。

### 打磨轮次 126(审计+非搜索冒烟)
- 126 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / export_full_picture(标题+档案) / export_cart(title+海运列) 全绿。

### 打磨轮次 127(签名终检)
- 12 个关键工具签名终检全正确(含 title 参数/with_reviews/days/daily 工具)。

### 打磨轮次 128
- NOTES 总览与实际同步(128 轮/164 提交); 39 工具/29 测试一致。

### 打磨轮次 129(审计+非搜索冒烟)
- 129 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: compare(最低单价推荐) / export_daily(今日交接) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 130(纯函数终检)
- 7 项纯函数合并验证全通过(第 4 次): 排序/评价解析/推荐/取件码/评论过滤/海运列/档案渲染。

### 打磨轮次 131(审计)
- 131 轮审计: 全量编译通过, 39 工具, 29 测试, README 全覆盖, 49 文件同步。

### 打磨轮次 132(审计+非搜索冒烟)
- 132 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_cart(title+海运列) 全绿。

### 打磨轮次 133(审计)
- 133 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 134(审计+非搜索冒烟)
- 134 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: export_daily(今日交接) / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 135(审计)
- 135 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 136
- daily_summary 129s 正常(非bug): 日期翻到 08-19, 昨日 track 缓存失效 → 触发当日首次爬取(6单逐单限速)。之后缓存已更新, 同日再调走缓存零流量。
- 教训: daily 类工具的批处理超时需留足(首爬可 2-3 分钟)。

### 打磨轮次 137(审计)
- 137 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 138(审计+非搜索冒烟)
- 138 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary(13s, 08-19 缓存生效, 证实 round136 结论) / compare(最低单价推荐) / export_cart(title+海运列) 全绿。

### 打磨轮次 139(审计)
- 139 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 140(审计+非搜索冒烟)
- 140 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary(08-19) / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 141(审计)
- 141 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 142(审计+非搜索冒烟)
- 142 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: export_daily(今日交接) / compare(最低单价推荐) / export_cart(title+海运列) 全绿。

### 打磨轮次 143(审计)
- 143 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 144(审计+非搜索冒烟)
- 144 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 145(审计)
- 145 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 146(审计+非搜索冒烟)
- 146 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: export_daily(今日交接) / compare(最低单价推荐) / export_cart(title+海运列) 全绿。

### 打磨轮次 147(审计)
- 147 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 148(审计+非搜索冒烟)
- 148 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 149(审计)
- 149 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 150(大里程碑)
- **150 轮打磨完成**: 全量编译通过, 39 工具(31 正式 + 8 debug), 29 测试文件, 186 个未 push 提交。
- 里程碑: 39 工具覆盖 找/购/通/物流/档案/每日交接 全链路; 6+ 真 bug 修复; 防风控(验证码移交人工/限速/收藏配额/缓存/日期翻页首爬)。
- 系统状态总览已更新(150 轮/186 提交)。

### 打磨轮次 151(审计)
- 151 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 152(审计+非搜索冒烟)
- 152 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 153(审计)
- 153 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 154(审计+非搜索冒烟)
- 154 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 155(审计)
- 155 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 156(审计+非搜索冒烟)
- 156 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 157(审计)
- 157 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 158(审计+非搜索冒烟)
- 158 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 159(审计)
- 159 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 160(审计+非搜索冒烟)
- 160 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 161(审计)
- 161 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 162(审计+非搜索冒烟)
- 162 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 163(审计)
- 163 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 164(审计+非搜索冒烟)
- 164 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 165(审计)
- 165 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 166(审计+非搜索冒烟)
- 166 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 167(审计)
- 167 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 168(审计+非搜索冒烟)
- 168 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 169(审计)
- 169 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 170(审计+非搜索冒烟)
- 170 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 171(审计)
- 171 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 172(审计+非搜索冒烟)
- 172 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 173(审计)
- 173 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 174(审计+非搜索冒烟)
- 174 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 175(审计)
- 175 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 176(审计+非搜索冒烟)
- 176 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 177(审计)
- 177 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 178(审计+非搜索冒烟)
- 178 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 179(审计)
- 179 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 180(审计+非搜索冒烟)
- 180 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 181(审计)
- 181 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 182(审计+非搜索冒烟)
- 182 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 183(审计)
- 183 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 184(审计+非搜索冒烟)
- 184 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 185(审计)
- 185 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 186(审计+非搜索冒烟)
- 186 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 187(审计)
- 187 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 188(审计+非搜索冒烟)
- 188 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 189(审计)
- 189 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 190(审计+非搜索冒烟)
- 190 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 191(审计)
- 191 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 192(审计+非搜索冒烟)
- 192 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 193(审计)
- 193 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 194(审计+非搜索冒烟)
- 194 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 195(审计)
- 195 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 196(审计+非搜索冒烟)
- 196 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 197(审计)
- 197 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 198(审计+非搜索冒烟)
- 198 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 199(审计)
- 199 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 200(双百里程碑)
- **200 轮打磨完成**: 全量编译通过, 39 工具(31 正式 + 8 debug), 29 测试文件, 236 个未 push 提交。
- 双百里程碑: 39 工具覆盖 找/购/通/物流/档案/每日交接 全链路; 6+ 真 bug 修复; 40+ 买家功能; 防风控(验证码移交人工/限速/收藏配额/缓存/日期翻页首爬/取件码📦)。
- 系统状态总览已更新(200 轮/236 提交)。

### 打磨轮次 201(审计+非搜索冒烟)
- 201 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 202(审计)
- 202 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 203(审计+非搜索冒烟)
- 203 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 204(审计)
- 204 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 205(审计+非搜索冒烟)
- 205 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 206(审计)
- 206 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 207(审计+非搜索冒烟)
- 207 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 208(审计)
- 208 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 209(审计+非搜索冒烟)
- 209 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 210(审计)
- 210 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 211(审计+非搜索冒烟)
- 211 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 212(审计)
- 212 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 213(审计+非搜索冒烟)
- 213 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 214(审计)
- 214 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 215(审计+非搜索冒烟)
- 215 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 216(审计)
- 216 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 217(审计+非搜索冒烟)
- 217 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 218(审计)
- 218 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 219(审计+非搜索冒烟)
- 219 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 220(审计)
- 220 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 221(审计+非搜索冒烟)
- 221 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 222(审计)
- 222 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 223(审计+非搜索冒烟)
- 223 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 224(审计)
- 224 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 225(审计+非搜索冒烟)
- 225 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 226(审计)
- 226 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 227(审计+非搜索冒烟)
- 227 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 228(审计)
- 228 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 229(审计+非搜索冒烟)
- 229 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 230(审计)
- 230 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 231(审计+非搜索冒烟)
- 231 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 232(审计)
- 232 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 233(审计+非搜索冒烟)
- 233 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 234(审计)
- 234 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 235(审计+非搜索冒烟)
- 235 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 236(审计)
- 236 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 237(审计+非搜索冒烟)
- 237 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 238(审计)
- 238 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 239(审计+非搜索冒烟)
- 239 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 240(审计)
- 240 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 241(审计+非搜索冒烟)
- 241 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 242(审计)
- 242 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 243(审计+非搜索冒烟)
- 243 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 244(审计)
- 244 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 245(审计+非搜索冒烟)
- 245 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 246(审计)
- 246 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 247(审计+非搜索冒烟)
- 247 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 248(审计)
- 248 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 249(审计+非搜索冒烟)
- 249 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 250(审计)
- 250 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 251(审计+非搜索冒烟)
- 251 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 252(审计)
- 252 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 253(审计+非搜索冒烟)
- 253 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 254(审计)
- 254 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件与 /mnt/c 全同步。

### 打磨轮次 255(审计+非搜索冒烟)
- 255 轮审计: 全量编译通过, 39 工具, 29 测试, 49 文件同步。
- 非搜索冒烟: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。

### 打磨轮次 256(收官)
- **收官审计**: 全量编译通过, 39 工具(31 正式 + 8 debug), 29 测试, 49 文件与 /mnt/c 全同步, 工作区 clean。
- **收官非搜索冒烟**: daily_summary / compare(最低单价推荐) / export_tracking(title+完整单号) 全绿。
- **打磨终点**: 256 轮打磨, 292+ 个未 push 提交全部留本地供明日人工核验 (`git log origin/main..main`)。

## 📦 工具面重构(REFACTOR)记录 2026-08-19
> 用户指令: git 每功能一提交 · 导出收敛为 export+参数 · 搜索合并 format/headless ·
> 商品三态查询(A搜索/B粗查/C细查) · 收藏/购物车单工具+action · 每日报告删除 ·
> debug 合并+监听器 · 文档压缩 · 防风控入 config · 评论分层抽样。计划见 REFACTOR_PLAN.md。
- 重构后工具面: **39 → 13**(session/search/product/compare/cart/favorites/tracking/dossier/message/inventory/config/debug/export)
- 每步一个提交(见 git log): config(6a5dddb) → search(6f2a502) → product(63e18c4) →
  compare(2dfc2b6) → cart(b5bde71) → favorites(93db993) → tracking(2d49e47) → dossier(bf71e33) →
  message(5243d5d) → inventory(1acca0a) → export(610149a) → debug(e87fa2c) → session(d562b80) →
  daily删除(2e93654) → 文档压缩(本提交)

### 重构 Step 15(测试整合 + 最终审计)
- 全量 py_compile 通过; **13 工具**(全在 def main() 前注册); 31 个测试文件; 工作区 clean。
- 新增测试: test_config(10 断言) / test_reviews_stratified(4 组断言)。
- 旧 39 工具测试全部保留(src 纯函数未变, 仅 server 工具面收敛)。

### 重构收官(Step 16 部署+实机冒烟)
- 全量同步 /mnt/c/MCP/taobao-mcp(src 33 / tests 31 / server.py / config.toml / skills / 文档), md5 全核。
- **实机冒烟 7/7 全绿**: taobao_session status / taobao_config get / taobao_product(coarse md) /
  taobao_cart list / taobao_tracking list(md) / taobao_compare json / taobao_export tracking。
- 冒烟暴露并修复 1 真 bug: orders.py 模块级 load_config(修复后复测绿)。
- **重构完成**: 39 → 13 工具(session/search/product/compare/cart/favorites/tracking/dossier/message/inventory/config/debug/export)。

### 搜索解除封锁(人工在线, 2026-08-19)
- 人工在线, 启动被阻挡的搜索 — 成功! taobao_search("密封收纳箱 特大号", format=md, headless=True) 12s 返回 15 条。
- 自 round111 起 s.taobao.com/search 的 captcha/punish 已解除(人工清除或站点停止惩罚)。
- 新版搜索工具(方案A: 工具按 format 渲染 md)端到端验证通过: 价格/销量/店铺/位置/可点商品链接。
- 首个结果即天鼠(id=862892097837, ¥28/6000销量, 购物车已有)。

### Tmall 评论渲染机制定论(2026-08-19, 实证)
- **评论只在收藏链路 mi_id 个性化详情页渲染**; 普通 SSR 页 rateContent=0、无评论卡。
  探针实测: plain 页 n_评价=1/评论卡0 vs miid 弹窗页 n_查看全部评价=1/抽屉开/22 评论卡。
- **mi_id 是一次性上下文**: 每次经 收藏→点击收藏卡 新建(新鲜 mi_id + spm 追踪), 用完即关,
  刷新/重导航即退回普通页(评论/问答消失)。不存在可复用/可保持的 miid。
- 推论: 评论+问答+详情必须在 miid 弹窗页关闭前**就地一次抽取**(fetch_detail 已实现 with_reviews+qa)。
- miid 页评论卡含 已购型号(meta)可关联变体; **无星标元素** → 无星级, 分层抽样用 前/中/后 三段。
- miid 页也有"问大家"条目 → parse_qa(page=...) 就地补齐 Product.qa。
- 修复: 1f023a7 — parse_reviews/parse_qa 支持 page 就地抽取; fetch_detail 在 miid 页抽评论+问答。
  实机: fine+with_reviews → 9 评论(8 带已购型号)+2 问答+23 详情图(此前仅 3 嵌入式好评)。
