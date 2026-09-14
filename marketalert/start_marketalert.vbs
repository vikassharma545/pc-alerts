' Launches MarketAlert invisibly (no console window).
' Paths are derived from this script's own location so the folder can live
' anywhere on any PC. pythonw.txt (written by install.ps1) pins the
' interpreter; without it we fall back to whatever pythonw is on PATH.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = "pythonw.exe"
pin  = fso.BuildPath(here, "pythonw.txt")
If fso.FileExists(pin) Then
    line = Trim(fso.OpenTextFile(pin, 1).ReadLine())
    If line <> "" And fso.FileExists(line) Then pyw = line
End If
sh.CurrentDirectory = here
sh.Run """" & pyw & """ """ & fso.BuildPath(here, "marketalert.py") & """", 0, False
