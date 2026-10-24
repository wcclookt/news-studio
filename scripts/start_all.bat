@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
rem Exact process/config matching is shared with the desktop UI.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 scripts\manage_services.py --action start --config "%CD%\config.json" %*
    goto finished
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -X utf8 scripts\manage_services.py --action start --config "%CD%\config.json" %*
    goto finished
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -X utf8 scripts\manage_services.py --action start --config "%CD%\config.json" %*
    goto finished
)
python -X utf8 scripts\manage_services.py --action start --config "%CD%\config.json" %*
:finished
set "RESULT=%ERRORLEVEL%"
if /i not "%~1"=="--no-pause" pause
exit /b %RESULT%
