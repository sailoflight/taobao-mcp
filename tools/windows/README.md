# Windows 侧桥接启动/自愈脚本

WSL 侧不直接起 `server.py`；浏览器本体运行在 Windows。本目录是 Windows 部署副本内
的无窗口启动/重启/自启脚本，由 WSL 端 `tools/wsl_bridge_ctl.sh` 或人工双击调用。

## 文件

| 文件 | 作用 |
|---|---|
| `start-bridge-hidden.vbs` | 用 `.venv\Scripts\pythonw.exe` 无窗口启动 `tools\bridge_server.py 8765` |
| `start-bridge.bat` | 兼容入口，委托给上面的 VBS |
| `restart-bridge.ps1` | 强杀自动化 Chrome/Edge + 旧 bridge，再无窗口拉起新 bridge（一键自愈） |
| `restart-bridge-hidden.vbs` / `.bat` | 无窗口调用 `restart-bridge.ps1` |
| `register-bridge-task.ps1` | 注册登录时启动 + 失败每分钟重启的计划任务 `TaobaoMCPBridge`（`-Uninstall` 移除） |
| `setup-autostart.bat` | 注册计划任务 + 立即启动 bridge（重启后恢复入口） |

## WSL 直接呼唤 Windows 启动

在 WSL 里运行：

```bash
tools/wsl_bridge_ctl.sh start     # 启动（无窗口）
tools/wsl_bridge_ctl.sh restart   # 强杀并重启（桥接卡死时用）
tools/wsl_bridge_ctl.sh status    # 检查 127.0.0.1:8765 是否可连
```

脚本默认假设 Windows 部署副本在 `C:\MCP\taobao-mcp`；可用环境变量覆盖：

```bash
TAOBAO_WIN_ROOT='C:\MCP\taobao-mcp' tools/wsl_bridge_ctl.sh restart
```

> `wsl_bridge_ctl.sh` 通过 WSL interop 调用 Windows 的 `wscript.exe` /
> `powershell.exe`，不把 `bridge_server.py` 跑在 WSL 里，符合 WIN-WSL 职责划分。
