# Force-restart the taobao MCP Windows bridge and its automation browser.
#
# This is the "一键自愈" script for a wedged bridge: it force-kills (NOT
# gracefully closes) both the automation browser (the one using the
# user_data\chrome_profile user-data-dir) and any python/pythonw running
# tools\bridge_server.py or run_mcp_stdio.py, then starts a fresh bridge.
# Force-kill keeps the persistent profile files so the next launch reuses the
# login session.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\windows\restart-bridge.ps1
param(
    [int]$Port = 8765
)

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$bridge = Join-Path $root 'tools\bridge_server.py'

Write-Host "[1/4] force-killing automation browser (user_data\chrome_profile) ..."
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('chrome.exe', 'msedge.exe') -and
                   $_.CommandLine -like '*user_data*chrome_profile*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Write-Host "[2/4] force-killing bridge child server (run_mcp_stdio.py) ..."
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and
                   $_.CommandLine -like '*run_mcp_stdio.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Write-Host "[3/4] force-killing bridge server python/pythonw ..."
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and
                   $_.CommandLine -like '*bridge_server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 2

if (-not (Test-Path $python)) {
    throw "virtualenv windowless python not found: $python"
}
if (-not (Test-Path $bridge)) {
    throw "bridge server not found: $bridge"
}

Write-Host "[4/4] starting fresh bridge on 127.0.0.1:$Port (windowless) ..."
Start-Process -FilePath $python `
    -ArgumentList ('"{0}" {1}' -f $bridge, $Port) `
    -WorkingDirectory $root `
    -WindowStyle Hidden
Write-Host "done. logs: $root\output\bridge-server.log"
