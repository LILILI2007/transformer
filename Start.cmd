@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python -m modlingo
) else (
    py -3 -m modlingo
)
if errorlevel 1 pause
