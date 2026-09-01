# 已退役的 DSH (WSL) ↔ taobao-mcp (Windows) 项目中继手册

> **历史记录，不是当前运行手册。** 本文引用的项目内 relay/listener/launcher
> 已从源码删除，所有命令均不再受支持。当前普通 stdio 与外部共享桥部署见
> `../operations/MCP_RUNBOOK.md`。

本手册描述如何在 **Windows 上运行 taobao-mcp server（真实 Chrome + 持久化 profile）**，并从 **WSL 中的 DeepSeek Harness（DSH）** 通过官方 MCP 客户端插件稳定调用它的 13 个工具。

## 架构

```
DSH (WSL, dsh-tui)  ── 官方插件 @deepseek-ai/dsh-mcp-client (stdio transport)
        │ spawn 原生 Linux 进程
        ▼
tools/mcp_tcp_bridge.py (WSL)  ── stdio ↔ TCP 中继（raw fd，不做缓冲读）
        │ loopback TCP (镜像网络共享 127.0.0.1)
        ▼
tools/bridge_server.py (Windows, 常驻监听 127.0.0.1:8765，pythonw)
        │ 每个 TCP 连接拉起一个无控制台 stdio 子进程；同一时刻只允许一个客户端
        ▼
run_mcp_stdio.py → server.py (python.exe + CREATE_NO_WINDOW) → Chrome (Windows 桌面可见)
```

- **stdio MCP 协议端到端不变**，零 OAuth、零公网暴露。
- `streamable-http` 模式（`run_mcp_http.py`）仍**仅供公网部署**（ChatGPT 插件后端，强制 OAuth），本地链路不使用它。

### 架构不变量（WIN-WSL 桥接模板验收）

| 模板要求 | taobao-mcp 落点 | 校验 |
|---|---|---|
| WSL 端是纯 stdlib 的 MCP 门面 | `tools/mcp_tcp_bridge.py`（os/select/socket/sys） | `tests/test_bridge_architecture.py` |
| 内层仅回环、本机可达 | `127.0.0.1:8765`，不绑 `0.0.0.0` | 同上 |
| Windows 运行时状态归属明确 | `bridge_server.py` 常驻；每个客户端拥有一个 FastMCP/浏览器子进程；断开时子进程干净关闭，但 Windows `user_data\chrome_profile` 保留登录态 | 运行探针 + lifecycle 测试 |
| 健康检查不抢业务锁 | `status` 发送单字节 preface，由 listener 在 `_ACTIVE_LOCK` 前响应，不拉起 FastMCP 子进程 | `tests/test_bridge_runtime.py` |
| Windows 全链路无控制台 | listener/任务用 `pythonw.exe`；stdio 子进程用 `python.exe + CREATE_NO_WINDOW` | bridge/windows 测试 |
| 共享资源单属主 | `bridge_server.py` 同一时刻只放行一个客户端，多余连接拒绝 | `tools/bridge_server.py` |
| 主体顶层不拉 GUI/内核依赖 | `server.py` 顶层不 import `src.browser.session`/`playwright`；`_get_session()`/`_ensure_logged_in()` 在工具执行路径内懒加载 | `tests/test_bridge_architecture.py` |
| 运行时角色提示同代部署 | `src/runtime_prompt.py` 是唯一权威；DSH companion 由脚本生成并 `--check`；live probe 校验 revision | `tests/test_runtime_prompt.py` |
| Windows 运行时对象不进 Git | `/user_data/`、`/output/`、`/config.local.toml`、`.venv/` 均已 gitignore | `verify_git_safety.py` |

> 本地维护改完跑：`python3 tools/mcp_probe.py`（纯桥接链路）与
> `.venv/bin/python -m pytest tests/test_bridge_architecture.py -q`（结构守卫）。

## 为什么需要这套桥（三条实测教训）

1. **WSL interop 管道无法承载常驻 stdio**：DSH 插件直接 spawn `C:\...\python.exe` 时，interop 管道在暂时无数据时向 Windows 子进程报 EOF，server 响应完 `initialize` 后数秒内自行退出。一次性灌入的请求能成功，但真实会话的延迟调用必然断链。
2. **桥接程序必须用 raw fd 中继 stdio**：`sys.stdin.buffer.read(65536)` 是 `BufferedReader` 语义——会一直等到读满 64 KiB 或 EOF 才返回。DSH 写入 `initialize` 请求后保持 stdin 打开，旧桥接因此在第一次请求上永久阻塞，一个字节都没有转发到 Windows 侧（表现为 Windows 日志里 server 秒退 rc=0、DSH 报 0 个工具）。`tools/mcp_tcp_bridge.py` 现在用 `os.read(0, 65536)`，读到多少转发多少。
3. **单副本铁律**：一个持久化 Chrome profile 只能被一个进程持有（见 `PUBLIC_DEPLOYMENT.md`）。因此 **DSH 与 Codex 不可同时**运行各自拉起的 server 进程；`bridge_server.py` 现在会在同一时刻只允许一个客户端连接，多余的连接直接拒绝并记录日志，避免双进程撞 profile 锁。

## 前置条件

- Windows 11 22H2+，WSL2 开启**镜像网络**（`%UserProfile%\.wslconfig` 中 `[wsl2] networkingMode=Mirrored`，改后 `wsl --shutdown` 重启）；`127.0.0.1` 因此双侧共享。
- Windows 侧已完成 `C:\MCP\taobao-mcp` 部署：`.venv`（Python 3.11+）、依赖安装、`config.local.toml` 中 `executable_path` 指向 Edge（`channel="chrome"` + 显式 `executable_path` 时 Playwright 会使用该浏览器）。
- DSH profile 已安装与当前 Harness 兼容的 MCP client；profile 同时加载工具 row 与生成的 runtime-policy companion row。

## Windows 侧运行手册

> 无窗口启动/重启/自启脚本位于 `tools/windows/`（与 onshape 的
> `mcp_main/bridge/windows/` 同款）。WSL 端可直接呼唤 Windows 桥接：
> `tools/wsl_bridge_ctl.sh start|restart|status`。

1. 常驻启动桥接服务（**无窗口**，推荐）：

   ```powershell
   cd C:\MCP\taobao-mcp
   wscript.exe .\tools\windows\start-bridge-hidden.vbs 8765
   ```

   或双击 `tools\windows\start-bridge.bat` / `setup-autostart.bat`。建议用任务计划程序
   （Task Scheduler，登录时启动）或由 Codex 侧统一托管，避免依赖 WSL 会话生命周期。
   桥接本体用 `.venv\Scripts\pythonw.exe` 运行，不产生 CMD/Python 控制台窗口；运行日志在
   `output\bridge-server.log`。**仅排查故障时**才用前台命令
   `.\.venv\Scripts\python.exe .\tools\bridge_server.py 8765`（会开控制台）。

2. 日志：子进程 stderr 落 `output\bridge-server.log`（stdout 保持协议纯净，勿合并）。正常调用应看到 `client ... -> spawned server pid=...`，并且**在客户端保持连接期间没有紧随其后的 `exited rc=0`**。
3. 监听 `127.0.0.1:8765`，仅回环，无需放行 Windows 防火墙（镜像模式下 WSL 直连回环）。

## WSL 侧运行手册

1. 在目标 DSH profile 安装兼容的 `@deepseek-ai/dsh-mcp-client`。
2. 将 `tools/dsh/cordis.patch.yml.example` 的两个 entry 一起合入该
   profile 的 `cordis.patch.yml`，并把 `<repo>` 替换为 WSL checkout 绝对路径：
   - MCP row 启动 `tools/mcp_bridge_entry.sh 8765`；它先做无副作用健康检查，
     Windows listener 不可达时通过 `wsl_bridge_ctl.sh start` 拉起并等待就绪，
     然后 `exec` 纯 stdlib relay。
   - companion row 加载生成的 `tools/dsh/runtime-prompt-companion.js`，因为只
     注册工具但不把 `initialize.instructions` 投递给模型的客户端不兼容。
3. 部署前检查 companion 与 Engine 是否同代：

   ```bash
   .venv/bin/python tools/dsh/build_runtime_prompt_companion.py --check
   .venv/bin/python -m pytest tests/test_runtime_prompt.py -q
   ```

4. 重启 DSH profile 后，工具列表应显示 13 个 `taobao_*` 工具；模型在首次
   工具决策前还必须能看到当前 `[revision=...]`、`Production / User` 和
   `Production / Operator`。只看得到工具但看不到 policy 不是健康部署。
5. 首次登录：调用 `taobao_session(action="login")`，在 Windows 可见浏览器中
   扫码。客户端断开会关闭其浏览器进程；下一连接用相同 Windows profile
   恢复 cookies，若站点要求重新认证则交给人处理。

### 从 WSL 直接启动/重启 Windows 桥接

不用切到 Windows 双击脚本，WSL 侧一条命令即可：

```bash
tools/wsl_bridge_ctl.sh start     # 无窗口启动 bridge（127.0.0.1:8765）
tools/wsl_bridge_ctl.sh restart   # 桥接卡死时：强杀浏览器+旧 bridge 再拉起
tools/wsl_bridge_ctl.sh status    # 单字节健康握手；不拉起子进程、不占 MCP 锁
```

默认 Windows 部署副本为 `C:\MCP\taobao-mcp`；若不在该路径，用
`TAOBAO_WIN_ROOT='C:\你的路径\taobao-mcp' tools/wsl_bridge_ctl.sh start` 覆盖。
这些命令调用 `tools/windows/` 下的隐藏 VBS/PowerShell 脚本，不在 WSL 内起
`server.py`。

> Windows 部署副本需先同步 `tools/windows/` 与 `tools/wsl_bridge_ctl.sh`；
> 若 WSL 侧报找不到 VBS，先把仓库里的 `tools/windows/` 拷到
> `C:\MCP\taobao-mcp\tools\` 再重试。

## 快速验证（不经 DSH 的纯桥接链路）

在 WSL 里执行下面的 Python 探针：它通过 `tools/mcp_tcp_bridge.py` 完成 `initialize` → `notifications/initialized` → `tools/list` → `tools/call taobao_session(action=status)`，并把连接空置 12 秒后确认桥进程仍然存活（验证“持久连接”本身）。

```bash
cd /home/<user>/code/taobao-mcp
python3 tools/mcp_probe.py
```

预期输出包含当前 runtime-policy revision、`tools/call ... status ok` 和
`idle-ok`。若 Windows deployment 与 WSL checkout/companion 不同代，probe
会在任何业务工具调用前失败。

## 故障排查

| 症状 | 处理 |
|---|---|
| DSH 里工具调用失败/无响应 | 先跑 `python3 tools/mcp_probe.py`。若连接被拒：Windows 桥接服务没在跑，按上文启动；然后看 `output\bridge-server.log` |
| `taobao（0 个工具）` 或插件空转 | 确认 WSL 侧 `tools/mcp_tcp_bridge.py` 是 raw-fd 版本（文件头注释含 `Critical implementation note`）；旧缓冲读版本会在此场景永久阻塞。确认桥接服务先于 DSH 启动，或重启 DSH 会话 |
| Windows 日志反复出现 `spawned server pid=...` 数秒后 `exited rc=0` | 客户端连接后没有把任何字节送过来。最常见原因就是上面第 2 条（旧桥接脚本），替换成新脚本后重启 DSH |
| Windows 日志出现 `rejected client ... another MCP session is active` | 有第二个客户端在抢唯一的 server 进程（Codex / 双 DSH / 未退出的旧会话）。关掉一个，必要时任务管理器清理残留 Chrome 和 python 进程 |
| Chrome 报 "个人资料正在使用"（profile 锁） | 有第二个进程在跑（Codex stdio / 双客户端并发）；关掉一个，必要时任务管理器清理残留 Chrome |
| 非镜像模式下 127.0.0.1 不通 | 改用 Windows 主机 LAN IP 或 WSL NAT 网关 IP，并放行防火墙端口（降级方案） |
| Windows 桥接服务需重启后 DSH 自动恢复 | 插件自带指数退避重连（默认开启），恢复后自动重新发现工具 |

## 安全边界

- 全程仅回环 TCP + 本地 stdio，无认证是因为**没有跨信任边界**：能连 127.0.0.1:8765 即本机进程。
- 公网/跨机访问仍必须走 `PUBLIC_DEPLOYMENT.md` 的 OAuth 流程，勿把本桥暴露到非回环接口。
- `user_data/`（含登录态）、`output/` 永远不得提交/打包/上传，见 `.gitignore` 与 `verify_git_safety.py`。
