@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_autoshift.ps1" %*
pause
