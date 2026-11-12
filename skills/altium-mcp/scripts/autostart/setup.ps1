# One-time setup on the Windows host (keep this file ASCII: PowerShell 5 reads BOM-less UTF-8 as GBK).
# Writes the two shortcuts next to itself, registers the on-demand task AltiumStartBridge
# (interactive only, no trigger time). Run from the folder that holds these files:
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
param(
    [string]$X2 = "C:\Program Files\Altium\AD26\X2.EXE",
    [string]$Bridge = "$env:USERPROFILE\EDA Agent\scripts\Altium_API.PrjScr"
)
$d = $PSScriptRoot
$sh = New-Object -ComObject WScript.Shell
$a = $sh.CreateShortcut("$d\Altium-Bridge.lnk"); $a.TargetPath = $X2; $a.WorkingDirectory = Split-Path $X2
$a.Arguments = "-RScriptingSystem:RunScript(ProjectName=""$Bridge""|ProcName=""Dispatcher.pas>StartMCPServer"")"; $a.Save()
$b = $sh.CreateShortcut("$d\Altium-iBOM.lnk"); $b.TargetPath = $X2; $b.WorkingDirectory = Split-Path $X2
$b.Arguments = "-RScriptingSystem:RunScript(ProjectName=""$d\ibom-auto.PrjScr""|ProcName=""auto.js>AutoIbom"")"; $b.Save()
schtasks /create /tn AltiumStartBridge /tr "`"$d\start-bridge.bat`"" /sc once /st 00:00 /sd 2020/01/01 /it /rl limited /f
