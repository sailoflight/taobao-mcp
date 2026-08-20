# MCP 工具面重构执行计划(REFACTOR PLAN)

> **状态: ✅ 全部完成(2026-08-19)。** 39 → 13 工具, 每功能一个提交, 实机冒烟 7/7 全绿。
> 用户已确认: ①历史重置到 origin/main 后按功能重提交(已执行: 297→单基线+每功能一提交);
> ②activity_report 保留并入 debug(已并入 taobao_debug action=activity);
> ③部署 + 实机冒烟(已执行, /mnt/c 全同步, 7/7 绿, 修复 orders.py load_config 真 bug);
> ④每功能实现提交一次(已执行, 见下方步骤 git log)。

---

## 0. 设计定稿(39 工具 → ~13 工具,参数化)

| # | 新工具 | 参数 | 取代(旧) |
|---|--------|------|----------|
| 1 | `taobao_session` | action=status\|login | initialize_login, session_status |
| 2 | `taobao_search` | query, page, filters, **format=json\|md, headless** | search, **search_md(删)**。方案A:工具按需渲染 md;headless=A 类查询(标题+外部标价,不点击进入) |
| 3 | `taobao_product` | url_or_id, **mode=coarse\|fine**, with_reviews, with_images | fetch_product, fetch_detail, product_summary, fetch_reviews, save_detail_images(全部并入)。B 粗查=全型号原价(无 miid);C 细查=完整收藏线路(**miid 内建**,删 get_miid) |
| 4 | `taobao_compare` | **ids(仅商品 id 最小输入)**, sort_by, format=md\|xlsx | compare_products, export_compare, export_compare_xlsx |
| 5 | `taobao_cart` | action=list\|add\|add_batch | list_cart, add_to_cart, add_to_cart_batch |
| 6 | `taobao_favorites` | action=list | list_favorites |
| 7 | `taobao_tracking` | action=list, force | track_orders |
| 8 | `taobao_dossier` | action=view | full_picture |
| 9 | `taobao_message` | action=list\|reply, **confirm** | read_messages, send_reply(仍确认后发送) |
| 10 | `taobao_inventory` | action=export\|refresh | export_inventory |
| 11 | `taobao_config` | **action=get\|set**, key, value | **新增**:防风控全参数落 config.toml;首次 set 返回确认+提醒人工 |
| 12 | `taobao_debug` | action=detail\|sku\|sweep\|miid\|home\|collect\|favorite\|**watch(监听器)**\|**activity** | 合并 8 个 debug + activity_report |
| 13 | `taobao_export` | **type**=compare\|cart\|tracking\|favorites\|product\|dossier\|inventory, filename, title, format | **替代全部 export_\*** |

**查询三分类(用户要求保持工具内语义):**
- A 类:仅搜索框查询标题+外部标价,不点击进入 → `taobao_search(headless=true)`
- B 类:粗查,点击进商品,全型号原价(无 miid 模式)→ `taobao_product(mode=coarse)`
- C 类:细查,完整收藏线路(miid 内建)→ `taobao_product(mode=fine)`

**其他落地项:**
- 评论聚合:好/中/差评各自分层抽样,防注入好评(reviews.py)
- 防风控每一步在 config.toml 体现 + `taobao_config` 查询/修改(首改二次确认+人工提醒)
- 文档压缩:工具 description 一行化;README/NOTES 压缩合并,减小 MCP 引导文档上下文占用

---

## 执行步骤(每步 = 一个提交)

| 步 | 提交名(建议) | 内容 | 涉及文件 | 验证 |
|----|-------------|------|----------|------|
| 0 | `refactor(base): 297提交压并为基线` | `git reset --soft origin/main` 后一次性提交原状(39工具/29测试/295+提交内容合并为单一基线) | 全部 | git log 仅剩基线;git status clean |
| 1 | `feat(config): 防风控参数全量入config + taobao_config工具` | 重写 config.toml 全量防风控(延迟/限速/收藏配额/验证码超时/缓存/收藏线路开关);新增 taobao_config(action=get\|set, key, value);首次 set 返回二次确认+人工提醒 | config.toml, server.py, src/config.py, 新测试 | py_compile;ast 单测 get/set 纯函数 |
| 2 | `refactor(search): 合并 search_md → format参数, 加headless` | taobao_search(query,page,filters,format=json\|md,headless);删 taobao_search_md;A 类查询;search.py 渲染 md | server.py, src/extract/search.py, tests | py_compile;单测(format/headless) |
| 3 | `refactor(product): 三态查询coarse/fine + 评论分层 + 图片并入` | taobao_product(url_or_id,mode=coarse\|fine,with_reviews,with_images);删 fetch_detail/product_summary/fetch_reviews/save_detail_images/get_miid(miid 内建);好中差评分层抽样 | server.py, src/extract/product.py, reviews.py, linker.py, favorite.py, tests | py_compile;单测(mode/分层抽样) |
| 4 | `refactor(compare): 仅ids最小输入 + 并入导出` | taobao_compare(ids,sort_by,format=md\|xlsx);删 compare_products/export_compare/export_compare_xlsx | server.py, src/extract/compare.py, tests | py_compile;单测 |
| 5 | `refactor(cart): 单工具action=list|add|add_batch` | 删 list_cart/add_to_cart/add_to_cart_batch → taobao_cart | server.py, src/extract/cart_price.py, src/cart.py, tests | py_compile;单测 |
| 6 | `refactor(favorites): 单工具action=list` | 删 list_favorites → taobao_favorites | server.py, src/extract/favorite.py, tests | py_compile;单测 |
| 7 | `refactor(tracking): 单工具action=list,force` | 删 track_orders → taobao_tracking | server.py, src/extract/orders.py, tests | py_compile;单测 |
| 8 | `refactor(dossier): 单工具action=view` | 删 full_picture → taobao_dossier | server.py, src/extract/linker.py, tests | py_compile;单测 |
| 9 | `refactor(message): 单工具action=list|reply,confirm` | 删 read_messages/send_reply → taobao_message(确认后发送不变) | server.py, tests | py_compile;单测 |
| 10 | `refactor(inventory): 单工具action=export|refresh` | 删 export_inventory → taobao_inventory | server.py, src/inventory.py, tests | py_compile;单测 |
| 11 | `refactor(export): 通用导出type参数` | taobao_export(type,filename,title,format);删全部 export_\* 独立工具 | server.py, 各 extract 导出函数, tests | py_compile;单测 |
| 12 | `refactor(debug): 合并8debug + activity_report + 监听器` | taobao_debug(action=...\|watch\|activity);删 8 个 debug 独立工具;activity_report 并入 | server.py, src/extract/activity.py, tests | py_compile;单测 |
| 13 | `refactor(session): 单工具action=status|login` | 删 initialize_login/session_status → taobao_session | server.py, tests | py_compile;单测 |
| 14 | `docs: 压缩合并 README/NOTES/工具描述` | README 重写为 ~13 工具表+快速流程;NOTES 压缩归档;工具 description 一行化 | README.md, NOTES.md, server.py | 审阅描述长度 |
| 15 | `test: 测试整合 + 最终审计` | 测试合并/新增覆盖新工具面;全量 py_compile + ast 单测 | tests/, server.py | 全绿;git log 逐功能核对 |
| 16 | `deploy: 同步 /mnt/c + 实机冒烟` | cp 同步 /mnt/c/MCP/taobao-mcp;dsh_mcp_batch 冒烟(搜索/商品/购物车/物流/导出);md5 抽查 | /mnt/c 部署 | 冒烟全绿(需 /mnt/c 写权限,可能触发批准) |

---

## 待办提醒(防遗忘)
- [ ] 每步完成后立即 git 提交(每功能一次),**不 push**,留本地
- [ ] 新工具注册必须插在 `def main()` 之前(否则 Unknown tool)
- [ ] server.py 为共享文件,跨步改动用 hunk 分块 staging 归入对应功能提交
- [ ] 评论分层抽样需覆盖 好/中/差 三档,防被注入好评
- [ ] 搜索 A/B/C 三态语义固化在工具 description(防 AI 误用)
- [ ] config 首次 set 必须二次确认 + 人工提醒文案
- [ ] 部署后 md5 抽查(防 round96 式未落盘)
- [ ] 冒烟避开搜索页验证码(当前 s.taobao.com/search 仍被 captcha 拦截,待人工清除)

---

## 模块整理轮(2026-08-20): 常复用代码抽成独立模块

> 用户要求: 结束后整理项目, 将常复用的代码单独提出做成模块, 便于修改。
> 已分 3 个提交完成, 全量测试 233 passed。

| 提交 | 动作 | 说明 |
|---|---|---|
| `1e1480e` | `refactor(quota)` | `fav_quota`+`search_quota`(两份几乎相同的每日配额)合并为 **`src/quota.py` 通用工厂** `make_daily_quota(state_filename, limit_key[, state_dir])`; 两个业务模块变薄封装, 调用点零改动 |
| `fae6d5a` | `refactor(scroll)` | **`src/browser/scroll.py` 复用 `pacing.human_delay`**, 删掉自己的重复副本(函数体延迟引用, 无循环导入) |
| `35d1e84` | `refactor(dates)` | 抽 **`src/dates.py`** 统一日期工具: `today_cn()` / `parse_date_iso()` / `days_cutoff_iso()`; reviews/orders/activity 三处改用共享函数 |

**模块地图(常复用核心):**
- `src/quota.py` — 每日配额工厂(收藏/搜索共用)
- `src/dates.py` — 日期解析/生成(中国时区)
- `src/browser/scroll.py` — 类人工滑动(人类节奏 + 到底即停)
- `src/browser/pacing.py` — 延迟/模拟点击/限速
- `src/extract/units.py` — "N个装"单价
- `src/extract/selectors.py` — 全部页面选择器集中(防漂移)

---

## 推荐近似搜索(A) 设计定稿(2026-08-20, 用户定架构)

> 背景: 搜索页(s.taobao.com/search)当前每次触发验证码, 但详情页(coarse/fine)零验证码。
> 方案: 用"详情页同类推荐"近似替代搜索, 横向找同类候选。

### 关键事实(人工实机观察 + 实测, 2026-08-20)
- 粗查 = **URL 拼接** goto item.htm(无 mi_id 参数), 但落地页面**带完整详情 + 优惠价**,
  且**无 mi_id 也能渲染推荐区块**(raw 72 条, 拓竹 top 全自家耗材, 35s 零验证码)。
- fine(足迹/收藏链路)页面带 mi_id + spm=tbpc.mytb_footmark, 推荐 raw 26 条。
- **结论: A2 游走原语用粗查即可**(快/不耗收藏配额/推荐质量不输 fine, 量还更大)。

### 三模式架构(用户设计)
| 模式 | 适用 | 原理 |
|---|---|---|
| **全自动** | 用户高度明确(如"查 拓竹 PETG-HF 1kg 黑色"), 无误区 | 直接自动游走到底 |
| **人机协同** | 用户模糊(如"打印零件, 材料性价比"), 有取舍 | **较短且过滤低的全自动** — 每轮给用户看候选, 用户定延伸方向 |
| **AI 自驱队列**(特殊全自动) | AI 判断客户可能需要 PETG/ABS/ASA 等 → 建搜索队列 | AI 自建**串行短全自动队列**, 队列依次搜 PETG→ABS→ASA, 最终**一次返回筛选拼接结果**; 避免 ①AI多轮介入 ②单轮长搜索同时过滤多个材料导致搜索算法分散 |

### A2 游走参数(初稿)
- 原语 `extract_recommendations(pid)`: 粗查 goto → 滚到底 → RECOMMEND_JS → rank
- budget(总粗查次数)建议 10–15; per_node 8; min_score 6(只延伸真耗材项)
- 全局去重池 + 按 score+跨节点频次 排序 + 输出游走轨迹
- 跨页 human_delay 拟人; 零验证码(captcha 出现仍人工交接)

---

## 粗查入口对比实验(2026-08-20 用户定案)

> 用户纠正: 足迹/收藏/购物车是**细查(miid)进入法**, 不属于粗查研究范围。
> 粗查 = 无 mi_id 依赖的 URL 进入。要对比的是**三种粗查进入方式**的区别。

### 三种粗查进入方式(研究对象)
| # | 进入方式 | 代码形态 | URL |
|---|---|---|---|
| 1 | **直接输入 URL**(仅含商品 id) | `page.goto("item.htm?id=X")`, 无 referer/spm | 裸 |
| 2 | **由细查商品推荐 goto** | A2 游走原语 goto, 页面处于推荐上下文 | 裸(当前) |
| 3 | **搜索框搜索后 goto** | 搜索结果提取 id 重拼 URL goto | 裸(当前) |

> 代码实证(2026-08-20): 三种粗查在代码里**全部退化为同一形态** —
> `page.goto("item.htm?id=X")`, 均无 spm/referer/mi_id。差异来自**页面上下文状态**
> 与 referer(真实点击才有 referer; goto 无)。历史经验: referer 影响 SSR 详情渲染。

### 一次性对比实验(只执行一次, 不重复)
对同一商品(建议 拓竹 PETG 990615757513), 分别用 3 种粗查进入, 捕获:
| 捕获项 | 检查点 |
|---|---|
| 详情(图文详情) | desc-root / 详情图数是否存在 |
| 推荐 | RECOMMEND_JS raw 数量 + top 质量 |
| 评论 | 评论容器/抽屉是否可提取 |
| 问答 | QA 容器是否渲染 |
| 优惠价 | 平台加补后 vs 优惠前 是否同时可见 |

### 补充 1: 上下文 × 进入方式 全矩阵
> 用户要求补: 每一种上下文(收藏/推荐/购物车/足迹/搜索/网址) × 每一种进入方式
> (直接输入网址/模拟点击/goto) 的**详情情况**。
>
> 注: 收藏/足迹/购物车属于细查(miid)路径 — 它们天然用"模拟点击"(点真实卡,
> 淘宝生成带 spm+mi_id 的 URL)。网址/搜索/推荐属于粗查 — 用 goto/直接输入。
> 矩阵 = 6 上下文 × 3 进入方式, 但**粗查只用网址/搜索/推荐 + goto/直接输入**;
> 细查(收藏/足迹/购物车)只用模拟点击。最终执行集聚焦在"粗查 3×2 + 细查 3×1 抽样"。

### 补充 2: 不同详情情况对推荐算法的影响
- 若某进入方式落地页**无详情/无推荐区** → 该方式不能用于 A2 游走(推荐没渲染)
- 若**有推荐但无优惠价/无评论** → 推荐可用但需注意价格口径(优惠前 vs 平台加补后)
- 若**推荐区内容因 referer 不同而不同** → A2 游走需显式指定进入方式语义

---

## 开发日志(2026-08-20, 用户要求记录)

### 操作队列优化: 浏览器跨多次操作不关
- **背景**: 每次 `dsh_mcp_batch.py` 调用 = 一个 TCP 连接 = 一个 server.py 子进程 =
  一个 Chrome。batch 结束 → 连接关 → server 退出 → Chrome 关。多次手动调用间反复
  开关 Chrome 加深风控。
- **落地**: `dsh_mcp_batch.py --dir <plans目录>` 合并模式 — 把目录下所有 plan_*.json
  的 ops 合并成一个大 plan, 单连接一次跑完, 浏览器只开一次、关一次。自动补 login,
  out 统一写 merged/。已实测(2 plan + login = 3 ops)。
- **约束**: MCP stdio 子进程在 stdin EOF 时退出(连接断开即死), 无法跨连接复用同一
  server 进程; 单副本铁律(一个 profile 一个进程)不变。因此合并队列是调用方层的
  最优解(无需改传输层)。

### 标签页管理(审计结论, 2026-08-20)
- 粗查 `parse_product`/`extract_recommendations`: `session.start()` 复用活的 page,
  用 `page.goto` 同标签导航 — **不新开标签** ✅
- 细查 `fetch_detail`: 足迹/收藏点卡必开 popup(淘宝行为), 提取完 `popup.close()` —
  **单标签卫生已实现**(desc.py:731) ✅
- 启动 `session.start()`: 清理 user_data 残留标签, 只留一个工作页(373aa2a) ✅
- **结论: 标签页管理已合规**(单标签复用 + popup 用完即关 + 启动清理残留), 无需改动;
  剩余开关风险来自"多次 batch 调用" → 用 --dir 合并模式解决。

### 粗查优惠价事实(人工观察实锤, 2026-08-20)
- **粗查(URL 拼接 goto, 无 mi_id)页面只有优惠前价, 无明显优惠价文案**。
- 粗查 `sku2info` 记优惠前(¥51–61); 页面未渲染"平台加补后/优惠前"显式文案
  (ENTRY_PROBE_JS 只抓到"立减"标记)。
- 待办: 跑同样商品细查(fine, 带 mi_id)比对优惠价 — 验证 **mi_id 是否影响优惠价显示**。

### 粗查 vs 细查优惠价比对结果(2026-08-20, 拓竹 PETG 990615757513)
- 粗查(URL拼接, 无mi_id) 与 细查(足迹链路, 带mi_id): **价格完全一致**(¥51无料盘/¥61含料盘,
  全型号相同)。
- **mi_id 对价格无影响**。两种进入方式的页面都显示"超级立减活动价 ¥51" — ¥51 本身就是
  立减后的活动价, 无"优惠前→优惠后"双价结构可比对。
- 结论: 用户观察"粗查只有优惠前" = ¥51 即最终活动价, 页面未展示双价文案; 非 mi_id 差异。

## 历史问题: 早期"直接 goto 粗查无详情"之谜(待研究)
> 2026 早期开发记录(_probe_pc_detail 注释)称"从搜索页进入(referer=s.taobao.com/search)
> 才渲染详情, 裸直接导航不渲染"。但 2026-08-20 实测: 裸 goto item.htm 也出完整详情
> (desc-root + 18图)。两者矛盾 — 需研究为何早期粗查无详情, 现在有了。
> 待查方向: ①早期页面是旧 SSR(tbpcDetail_ssr2025 之前的版本), 详情依赖 referer 触发;
> ②或早期未登录/无活动态; ③或淘宝前端升级后详情改为主文档直接 SSR。

### 早期"粗查无详情"之谜 — 研究结论(2026-08-20)
- **不是"渲染机制变了", 是淘宝前端版本迭代 + 采集路径演进的历史误会**。
- ①早期(旧SSR): 桌面 item.htm 从不渲染详情, 详情靠 H5(`detail.tmall.com?x-ssr=true`)
  经 `mtop.taobao.detail.data.get` 拉取(desc.py:217 历史注释) → 裸 goto 真无详情。
- ②中期(新SSR tbpcDetail_ssr2025): 详情渲染进可滚动容器 `#tbpcDetail_SkuPanelBody`
  (greasyfork 460143), 懒加载需滚容器; 从搜索页带 referer 进入会预渲染 →
  当时误以为"必须从搜索进入才有详情"。
- ③现在(2026-08-20 实测): 详情改为主文档直接 SSR(`.desc-root`+18图), 裸 goto 也完整。
  → 早期"referer 决定详情"是特定版本的中间态, 现已不成立(三入口实验实证)。
- **历史影响**: 早期因"裸粗查无详情"而设计 fine 链路必须带 mi_id 进(足迹/收藏)。
  现在裸粗查已有详情 → A2 游走用粗查成立, 用户判断正确且被当前前端验证。
