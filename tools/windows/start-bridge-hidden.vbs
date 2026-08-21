Option Explicit

Dim fso, shell, scriptDir, root, pythonw, bridge, port, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetAbsolutePathName(fso.BuildPath(scriptDir, "..\.."))
pythonw = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
bridge = fso.BuildPath(root, "tools\bridge_server.py")
port = "8765"
If WScript.Arguments.Count > 0 Then port = WScript.Arguments(0)

If Not fso.FileExists(pythonw) Then WScript.Quit 1
If Not fso.FileExists(bridge) Then WScript.Quit 1

shell.CurrentDirectory = root
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & bridge & Chr(34) & " " & port
' Window style 0 = hidden; False = return immediately and keep bridge running.
shell.Run command, 0, False
