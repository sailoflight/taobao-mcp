"""Offline structural checks for the Windows-side bridge launchers.

These guard the "WSL 端直接呼唤 Windows 端桥接启动" maintenance path: the WSL
one-shot `tools/wsl_bridge_ctl.sh` triggers hidden VBS launchers in
`tools/windows/`, which in turn run `pythonw.exe`/PowerShell without spawning a
console and without starting `server.py` on the WSL side.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "tools" / "windows"
WSL_CTL = ROOT / "tools" / "wsl_bridge_ctl.sh"


def _read(name: str) -> str:
    return (WINDOWS / name).read_text(encoding="utf-8")


def test_start_hidden_vbs_uses_pythonw_and_no_dialog() -> None:
    script = _read("start-bridge-hidden.vbs")
    assert r".venv\Scripts\pythonw.exe" in script
    assert "shell.Run command, 0, False" in script
    assert "WScript.Echo" not in script


def test_restart_hidden_vbs_runs_powershell_hidden_and_waits() -> None:
    script = _read("restart-bridge-hidden.vbs")
    assert "powershell.exe -NoProfile" in script
    assert "shell.Run(command, 0, True)" in script


def test_batch_launchers_delegate_to_hidden_vbs() -> None:
    assert "start-bridge-hidden.vbs" in _read("start-bridge.bat")
    assert "restart-bridge-hidden.vbs" in _read("restart-bridge.bat")
    assert "start-bridge-hidden.vbs" in _read("setup-autostart.bat")


def test_task_and_restart_use_pythonw_and_hidden_window() -> None:
    task = _read("register-bridge-task.ps1")
    restart = _read("restart-bridge.ps1")
    assert r".venv\Scripts\pythonw.exe" in task
    assert r".venv\Scripts\pythonw.exe" in restart
    assert "-WindowStyle Hidden" in restart
    assert "TaobaoMCPBridge" in task


def test_restart_kills_browser_and_bridge_children() -> None:
    restart = _read("restart-bridge.ps1")
    assert "'chrome.exe', 'msedge.exe'" in restart
    assert "*user_data*chrome_profile*" in restart
    assert "*run_mcp_stdio.py*" in restart
    assert "*bridge_server.py*" in restart


def test_wsl_ctl_triggers_windows_vbs_not_wsl_server() -> None:
    ctl = WSL_CTL.read_text(encoding="utf-8")
    assert ctl.startswith("#!/usr/bin/env bash")
    assert r"tools\\windows\\start-bridge-hidden.vbs" in ctl
    assert r"tools\\windows\\restart-bridge-hidden.vbs" in ctl
    assert "wscript.exe" in ctl
    assert "python" not in ctl
    assert "8765" in ctl
