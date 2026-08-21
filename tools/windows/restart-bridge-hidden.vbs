Option Explicit

Dim fso, shell, scriptDir, restartScript, port, command, exitCode
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
restartScript = fso.BuildPath(scriptDir, "restart-bridge.ps1")
port = "8765"
If WScript.Arguments.Count > 0 Then port = WScript.Arguments(0)

If Not fso.FileExists(restartScript) Then WScript.Quit 1

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & _
    Chr(34) & restartScript & Chr(34) & " -Port " & port
' Run PowerShell hidden and wait so failures produce a non-zero launcher exit.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
