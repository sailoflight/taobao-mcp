# MCP 工具面重构执行计划(REFACTOR PLAN)

> 状态:待执行(2026-08-19 用户拍板)。每条 = 一个 git 提交,按序推进,每步含验证。
> 用户已确认:①历史重置到 origin/main 后按功能重提交;②activity_report 保留并入 debug;
> ③尝试部署 + 实机冒烟(需 /mnt/c 写权限);④每功能实现提交一次。

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
