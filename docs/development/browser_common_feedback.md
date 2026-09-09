# browser_common 集成反馈(移交共享库维护方)

> **处理状态(2026-09-10,0.1.0.dev2)**:共享库已发布 dev2 回应本件,五条全部处理
> (详见共享库仓库 `FEEDBACK_DEV2.md`):§1 语义保持并补库侧测试;§2 新增
> `scope.try_track(page)->bool`(严格 track 不变);§3 手册补内层函数/AsyncExitStack
> 建议;§4 新增 `ReleaseReport.failures_summary`;§5 错误消息带 owner/current loop id。
> taobao-mcp 已收编:门面 `track_temporary_page` 改用 try_track(保留返回原 Page 语义)、
> `_report_failures` 委托 `failures_summary`、依赖固定 ==0.1.0.dev2
> (wheel sha256 `c1763265aa5c9680…913cc8`)。本件保留为历史移交记录。

来源:taobao-mcp 接入 `lijq-browser-common==0.1.0.dev1`(wheel sha256 `0a32e9f0803cabab…`)的
阶段 0-3 实施过程(2026-09-09/10)。每条标注证据与建议等级。本文是移交件,不是本仓库的
权威边界文档。

## 1. 确认正确:scope 退出不遮蔽业务异常(建议库侧加测试锁定)

`AsyncTemporaryPages.__aexit__`(async_session.py:195-199):业务异常传播途中清理不完整 →
仅 `exc.add_note(...)`,原异常照常上浮;仅正常退出才抛 `PageCleanupError`。

这正是业务需要的语义:taobao-mcp 的 CaptchaError/SelectorDriftError(风控墙/布局漂移)
必须原样上浮给调用方,不能被清理失败顶替。taobao-mcp 已在
`tests/test_browser_facade.py::test_scope_exit_never_masks_body_exception` 用假驱动锁定该
契约(两个场景:body 异常 + 关闭失败 → 原异常 + add_note;正常退出 + 关闭失败 →
PageCleanupError)。**建议共享库自身加同款测试,防止未来回退成一律抛 PageCleanupError。**

## 2. 建议:track() 对"已关闭页"的拒绝迫使所有跨函数消费者预检

`scope.track(page)` 拒绝已关闭的页(手册 §6 契约)。但 popup 经生产者函数返回给消费者时,
生产者可能在内部已关闭该页(URL 校验失败自清理)——消费者拿到的是**合法返回值但已关闭**的
页。结果:每个跨函数消费者都得写 `if page is not None and not page.is_closed(): scope.track(page)`,
taobao-mcp 为此在 `src/browser/session.py` 提供了 `track_temporary_page()` 辅助并复制到
5 个消费现场。

建议:库提供 `scope.try_track(page) -> bool` 或 `track(page, missing="skip")`,把这一
普遍模式收进库内(拒绝语义保留给显式 `track()`)。

## 3. 建议:长流程使用 scope 时的整段缩进重排

`temporary_pages()` 仅支持 `async with`。当 popup 的生命周期跨越函数体大半
(taobao-mcp `desc.fetch_detail`:获取→harvest→finally 清理,约 175 行)时,包裹迫使整段
重排缩进,review diff 噪声大、误改风险高(本仓库用脚本化 +4 缩进完成)。

建议:库文档明示该取舍;可选提供显式 `scope = session.temporary_pages(); …; await
scope.finish()` 模式(需在文档标注:async-with 仍是防泄漏首选,显式模式仅在
try/finally 全程覆盖时使用)。若认为显式模式风险大于收益,也请在手册写明"长流程请先
提取内层函数再包裹",给集成方一个官方答案。

## 4. 建议:ReleaseReport 提供 failures 一行式摘要

日志需要把 `report.failures` 渲染成 `operation:error_type` 串。taobao-mcp 在门面里手写了
`_report_failures()`。各集成方各自手拼容易漂移,建议 `ReleaseReport` 增加
`summary`/`failures_summary` 属性。

## 5. 轻微 DX:ExecutionContextError 的首次触发体验

库的 loop/thread pinning 契约正确,但集成方写测试时最先撞到的是 asyncio 自己的
`RuntimeError: no running event loop`(在 owning loop 外访问属性),定位到库契约花了
三轮迭代。建议 `ExecutionContextError` 的消息直接携带 owning-loop 标识(id/thread),
并把"测试断言必须收集后在 owning loop 外执行"写入手册测试章节的显眼位置。

## 6. 无缺陷确认(集成中验证过、无需改动)

- `cleanup_restored=True` 的启动清理与工作页选择符合旧启动行为(§7.3);
- profile 绝对路径/边界校验、channel 与 executable_path 互斥的 fail-closed 顺序正确;
- `adopt_page()` 不关闭原工作页,责任划分清楚;
- `reconcile_pages()` 的 protected/closed 分类与报告结构可直接用于诊断;
- 预算/序列化(并发 start 串行化)在离线测试与假驱动下行为一致。

## 移交清单(供共享库 agents 复核)

| 项 | taobao-mcp 侧证据 |
|---|---|
| §1 语义锁定测试 | `tests/test_browser_facade.py::test_scope_exit_never_masks_body_exception` |
| §2 辅助函数 | `src/browser/session.py::track_temporary_page` + desc/qa/reviews 5 个调用点 |
| §3 缩进重排现场 | `src/extract/desc.py::fetch_detail`(阶段 2 提交 e4db937) |
| §4 手拼摘要 | `src/browser/session.py::_report_failures` |
| §5 测试迭代 | 阶段 1 `tests/test_browser_facade.py` 三轮修正记录 |
