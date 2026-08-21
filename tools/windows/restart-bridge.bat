@echo off
rem One-click force-restart of the taobao MCP Windows bridge + automation browser.
wscript.exe //B //Nologo "%~dp0restart-bridge-hidden.vbs" 8765
