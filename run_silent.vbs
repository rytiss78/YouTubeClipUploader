Set shell = CreateObject("WScript.Shell")
base = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd.exe /c cd /d """ & base & """ && py -3 clip_uploader.py --non-interactive", 0, False
