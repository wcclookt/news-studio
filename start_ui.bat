@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
rem The bootstrap detects an existing interpreter and repairs missing UI dependencies.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap_ui.ps1" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo.
    echo Launch failed. See logs\bootstrap-ui.log and double-click again to retry.
    if /i not "%~1"=="-DryRun" pause
)
exit /b %RESULT%
