# Register (or remove) the login-time Task Scheduler entry that keeps the
# taobao MCP Windows bridge alive across reboots.
#
# Same recovery model as DSH_WSL_BRIDGE.md:
#   * Windows Task Scheduler starts tools\bridge_server.py at user logon.
#   * Task restarts the bridge on failure every minute, up to 999 times.
#   * The WSL MCP client owns reconnect/backoff; once 127.0.0.1:8765 is up
#     again it reconnects and re-discovers tools automatically.
#
# Usage (PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\tools\windows\register-bridge-task.ps1
#   powershell -ExecutionPolicy Bypass -File .\tools\windows\register-bridge-task.ps1 -Uninstall
param(
    [switch]$Uninstall,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$TaskName = 'TaobaoMCPBridge'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed scheduled task '$TaskName'"
    } else {
        Write-Host "scheduled task '$TaskName' not present"
    }
    exit 0
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$bridge = Join-Path $root 'tools\bridge_server.py'

if (-not (Test-Path $python)) {
    throw "virtualenv windowless python not found: $python — run the Windows install steps first"
}
if (-not (Test-Path $bridge)) {
    throw "bridge server not found: $bridge"
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}" {1}' -f $bridge, $Port) `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "taobao MCP Windows bridge (127.0.0.1:$Port) — one persistent Chrome profile, single client" `
    -Force

Write-Host "registered scheduled task '$TaskName'"
Write-Host "  python : $python (windowless)"
Write-Host "  bridge : $bridge $Port"
Write-Host "  trigger: at logon of $env:USERDOMAIN\$env:USERNAME (restart on failure every 1 min)"
Write-Host "Start it now without a window with:  wscript.exe .\tools\windows\start-bridge-hidden.vbs"
