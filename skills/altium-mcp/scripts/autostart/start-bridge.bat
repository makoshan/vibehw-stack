@echo off
rem Called by task AltiumStartBridge. Opens a .lnk through explorer so Altium starts like a double-click
rem (child of the desktop shell, normal priority, visible). Launching X2.EXE directly from the task hangs before splash.
rem Only use when Altium is NOT running: with Altium open, X2.EXE -R starts a second stuck instance.
cd /d %~dp0
if exist ibom-job.txt (explorer.exe "%~dp0Altium-iBOM.lnk") else (explorer.exe "%~dp0Altium-Bridge.lnk")
