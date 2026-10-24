@echo off
chcp 936 >nul
rem ========================================
rem  实时推送测试
rem ========================================

set PROJECT_DIR=%~dp0..
cd /d %PROJECT_DIR%

set PYTHON=python
where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
set /p PYTHON=<"%TEMP%\pyfind.txt"
del "%TEMP%\pyfind.txt" 2>nul

echo [INFO] 运行实时推送...
%PYTHON% -X utf8 run.py --mode realtime --config config.json
echo.
pause