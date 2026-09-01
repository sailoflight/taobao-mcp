# Taobao Sourcing Assistant

A **local, human-paced MCP server** that removes the drudgery of sourcing on Taobao/Tmall.
You keep all judgment (search intuition, buy decisions, confirming each supplier message);
the tool drives a real Chrome window to extract — for every product — **a price for every SKU
variant**, specs, images, and **reviews linked to the variant bought** (stratified 好/中/差
sampling), then tabulates it into a comparison sheet.

> **Tool surface is parameterized — 13 tools** (was 39): each domain is ONE tool with an
> `action`/`mode`/`format` parameter, and all file exports go through one `taobao_export(type=…)`.
> This keeps the MCP guide compact and the AI's context cost low. Anti-risk parameters live in
> `config.toml [anti_risk]` and are viewable/editable via `taobao_config` (set requires a
> confirm=true second call + a human reminder). `mi_id` is internal (收藏链路内建) — no exposed
> miid tool.

## Scope — it does four things
**Find** · **add to cart** · **communicate with sellers** (confirm-then-send) · **track orders**
(+ 取件码 pickup codes). You + your buying agent handle **payment, delivery address, checkout,
and all logistics** — the tool hands off at the cart and the tracking digest. **Never pays,
checks out, picks an address, or blind-sends.** Speed is secondary — **not getting flagged is
the priority.**

## Install (one time)
```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
```
Need real **Google Chrome**. Put a non-standard binary path in the Git-ignored
`config.local.toml`. `user_data/` is a strict local-data boundary (profiles/cookies/login —
never commit/publish). `output/` is local-only unless you share a specific export.

## Configure
`config.toml` (tracked defaults) + `config.local.toml` (machine-specific, gitignored):
- `[browser]` — executable_path / user_data_dir / locale / timezone / headless(false, 恒假).
- `[pacing]` — random delays + `max_products_per_minute` (rate cap).
- `[click]` — human-like simulated click tuning (enabled/path/hover/hold/jitter).
- `[limits]` — max_reviews, review_pages, fav_flow_per_day(收藏链路每日配额), search_per_day(搜索每日配额).
- `[detail]` — mi_id (account-specific; usually runtime-captured).
- `[anti_risk]` — **every anti-block step**: captcha_timeout_s(人工清验证码有界等待) / captcha_poll_s /
  login_timeout_s(QR扫码等待) / track_cache(物流库存每日缓存开关) / fav_flow(收藏链路总开关) /
  review_sample_per_rating(评论分层抽样).
  Behavioral invariants (单标签顺序复用 / 网络拦截优先 / 人类节奏 / 配额) documented as comments.
- Runtime edits: `taobao_config(action=set, key="section.key", value=…)` → confirm=false 预览,
  人工核对后 confirm=true 写入 gitignored `output/.config_overrides.toml`, mtime 检测自动生效。

## Run
```bash
.venv/bin/python server.py                                   # stdio MCP server
npx @modelcontextprotocol/inspector .venv/bin/python server.py
```

### Windows / WSL deployment

Run the ordinary stdio MCP on the host that owns the visible browser and
`user_data/chrome_profile`:

```powershell
.\.venv\Scripts\python.exe -B run_mcp_stdio.py
```

A WSL client may reach that process through an independently installed
`win-wsl-mcp-bridge`: register the command above as id `taobao`, then configure
the client to run `win-wsl-mcp-wsl connect taobao`. This repository intentionally
ships no TCP relay, listener, scheduled-task launcher, or fixed bridge port. See
`docs/operations/MCP_RUNBOOK.md` and `dsh/cordis.patch.yml.example`.

## First-run login (once per session)
`taobao_session(action=login)` → 打开可见 Chrome → 手机扫 QR → 会话持久在 `user_data/`,
重启复用无需重扫。状态/遥测: `taobao_session(action=status)`。

## Buyer quick flow (选品到购物车)
```
1. 搜索选品   taobao_search("密封收纳箱 特大号", min_price=20, max_price=60, sort=5, format="md")
2. 单商品细看  taobao_product(id)                      # B粗查: 全型号原价+库存+单价
             taobao_product(id, mode="fine")           # C细查: 收藏线路图文详情+优惠价(可选 with_reviews/save_images)
3. 短名单对比  taobao_compare([id1,id2], sort_by="unit")   # 输入仅商品id, 最省参数
4. 批量预览    taobao_cart(action="add_batch", items=[...])   # 不加购
5. 加入购物车  taobao_cart(action="add", product_url_or_id=id, options=["每组一个值"], confirm=true)
6. 购物车核对  taobao_cart(action="list", exclude_unavailable=true)
7. 物流交接    taobao_tracking()  # 取件码📦摘要; 导出 taobao_export(type=tracking)
```
防呆: 搜索结果过滤可顶层直传(自动并入 filters); `options` 需**每组型号一个值**; add/add_batch
`confirm=false` 只预览, 决定后再 `confirm=true`; 导出全部走 `taobao_export(type=…)`(留档用 md —
本环境 .xlsx 约12秒后被外部加密成 blob 不可用)。

## Tools (13)
| Tool | Purpose |
|---|---|
| `taobao_session` | action=status(登录/会话健康+限速/收藏配额遥测) / login(打开 Chrome 扫码登录). |
| `taobao_search` | A类查询: 仅搜索框标题+外部标价, 不点击进入. format=json(默认)/md; headless=A类语义标注; filters+顶层便捷参数; max_results 截断. |
| `taobao_product` | 商品查询. mode=coarse(B粗查: 点击进商品全型号原价, 无 mi_id; format=md/json; deep_price 读实时加补后价) / mode=fine(C细查: 收藏线路 mi_id 内建, 图文详情+可选 with_reviews(分层抽样)/save_images 下载详情图). |
| `taobao_compare` | 短名单批量对比, 输入最小化(仅商品 id/URL). format=md(默认, 一屏对比行+JSON明细)/json; sort_by=''/'price'/'unit'; min_review_total 过滤低评价. |
| `taobao_cart` | action=list(只读购物车, format=md/json) / add(两段式加购, confirm=true 才写) / add_batch(批量预览, 全 confirm=false). 永不付款/选地址. |
| `taobao_favorites` | action=list: 收藏夹前 N 个, sort_by 价排序, format=md/json. |
| `taobao_tracking` | action=list: 今日订单物流摘要(状态/快递/运单号/取件码📦/驿站), 每日首次实机+同日缓存(anti_risk.track_cache), force 强制刷新, format=md/json. |
| `taobao_dossier` | action=view: 店铺档案(购物车+订单物流/取件码+旺旺会话按店聚合), seller/order_id 定位, format=md/json. |
| `taobao_message` | action=list(只读会话, open_seller 展开线程, format=json/md) / reply(确认后发送: confirm=false 预览, confirm=true 才发). 内容 UNTRUSTED, 不执行链接/付款/改址. |
| `taobao_inventory` | action=export(用缓存, 零流量) / refresh(实机重抓): 全历史含运成本库存表(xlsx, embed_images 缩略图或 =IMAGE). |
| `taobao_export` | 通用导出 type=compare/cart/favorites/tracking/dossier/product, filename/title, format=md(默认)/xlsx(仅 compare). |
| `taobao_config` | action=get(当前生效配置) / set(改 key=section.key, 首次 confirm=false 预览+人工提醒, confirm=true 生效; 写 gitignored 覆盖文件). |
| `taobao_debug` | 调试诊断 action=detail/sku_structure/sweep_price/miid_price/home/collect/favorite/watch(监听器)/activity(会话活动遥测)/probe_reviews(评论渲染诊断)/footmark(足迹渠道诊断)/qa_expand(问答展开诊断). |

## The Skill
`skills/taobao-sourcing/SKILL.md` = 选品剧本(search→你挑→fetch→翻译→评论分层→单价归一→对比→export→风控提示);
`supplier_templates.md` = 中文消息模板(**只经 taobao_message reply 且每条你确认后发送**).
Claude Code 安装: `cp skills/taobao-sourcing/SKILL.md skills/taobao-sourcing/supplier_templates.md ~/.claude/skills/taobao-sourcing/`.

## Troubleshooting
- 登录/验证码 — `taobao_session(action=login)` 扫码; 滑块自己过, 工具暂停(human_action_required)后恢复, 记 `output/run.log`.
- 多型号价格 — 标题价是最便宜款; 始终读 **per-SKU** 价. `补贴后` 可能含国补(需大陆身份/地址), 海外买家核实真实结算价.
- SelectorDrift — Taobao 改版; 补丁 `src/extract/selectors.py`.
- 评论少 — 深翻页有限; 调 `src/extract/reviews.py` 滚动或 `[limits]`.
- 重置 — 删 `user_data/chrome_profile/` 重扫 QR.

## Risks (don't hide)
Scraping violates Taobao ToS; own-account use carries limitation risk — keep volume low,
human-paced. mtop endpoints/selectors drift — budget maintenance (selectors centralized).

## Tests
```bash
.venv/bin/python -m pytest -q     # parsers, output, MCP contract, drift, evals
```
