@echo off
rem One-click windowless start of the taobao MCP Windows bridge (127.0.0.1:8765).
wscript.exe //B //Nologo "%~dp0start-bridge-hidden.vbs" 8765
