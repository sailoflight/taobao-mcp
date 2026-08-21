#!/usr/bin/env bash
# WSL-side one-shot control for the Windows-hosted taobao MCP bridge.
#
# The bridge body (tools/bridge_server.py -> run_mcp_stdio.py -> server.py)
# must run on Windows so it can drive a real Chrome/Edge window with the
# persistent user_data\chrome_profile login. This script only *triggers* the
# Windows-side hidden launchers via WSL interop; it never starts server.py in WSL.
#
# Usage:
#   tools/wsl_bridge_ctl.sh start     # windowless start on Windows
#   tools/wsl_bridge_ctl.sh restart   # force-kill browser+bridge, then start
#   tools/wsl_bridge_ctl.sh status    # check loopback port (no Windows call)
#
# Env overrides:
#   TAOBAO_WIN_ROOT      Windows repo root (default C:\MCP\taobao-mcp)
#   TAOBAO_BRIDGE_PORT   bridge port (default 8765)

set -euo pipefail

PORT="${TAOBAO_BRIDGE_PORT:-8765}"
WIN_ROOT="${TAOBAO_WIN_ROOT:-C:\\MCP\\taobao-mcp}"
WIN_START_VBS="$WIN_ROOT\\tools\\windows\\start-bridge-hidden.vbs"
WIN_RESTART_VBS="$WIN_ROOT\\tools\\windows\\restart-bridge-hidden.vbs"

WSCRIPT="/mnt/c/Windows/System32/wscript.exe"

die() { echo "wsl_bridge_ctl: $*" >&2; exit 1; }

case "${1:-status}" in
  start)
    [ -x "$WSCRIPT" ] || die "wscript.exe not found at $WSCRIPT (WSL interop unavailable?)"
    echo "wsl_bridge_ctl: starting Windows bridge on 127.0.0.1:$PORT (windowless)"
    "$WSCRIPT" //B //Nologo "$WIN_START_VBS" "$PORT"
    ;;
  restart)
    [ -x "$WSCRIPT" ] || die "wscript.exe not found at $WSCRIPT (WSL interop unavailable?)"
    echo "wsl_bridge_ctl: force-restarting Windows bridge on 127.0.0.1:$PORT"
    "$WSCRIPT" //B //Nologo "$WIN_RESTART_VBS" "$PORT"
    ;;
  status)
    if timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
      echo "bridge reachable on 127.0.0.1:$PORT"
    else
      echo "bridge NOT reachable on 127.0.0.1:$PORT"
      exit 1
    fi
    ;;
  *)
    die "unknown command '${1}'; use start|restart|status"
    ;;
esac
