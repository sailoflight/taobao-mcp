@echo off
rem One-click reboot recovery for the taobao MCP Windows bridge.
rem   1. Register a login-time Task Scheduler entry (restarts the bridge on failure).
rem   2. Start the bridge right now, so the WSL client can reconnect immediately.
setlocal
set ROOT=%~dp0..\..
cd /d "%ROOT%"

echo [1/2] Registering login-time autostart task 'TaobaoMCPBridge' ...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\windows\register-bridge-task.ps1"
if errorlevel 1 (
    echo autostart registration failed - see the PowerShell output above.
    exit /b 1
)

echo [2/2] Starting the bridge now ...
wscript.exe //B //Nologo "tools\windows\start-bridge-hidden.vbs" 8765
echo Done. The bridge listens on 127.0.0.1:8765 and auto-starts at next logon.
echo Check logs in output\bridge-server.log
endlocal
