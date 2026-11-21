@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "MODLINGO_EXIT_CODE=%errorlevel%"
if not "%MODLINGO_EXIT_CODE%"=="0" pause
exit /b %MODLINGO_EXIT_CODE%
