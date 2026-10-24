@echo off
chcp 936 >nul
rem ========================================
rem  QQ 推送测试
rem ========================================

set PROJECT_DIR=%~dp0..
cd /d %PROJECT_DIR%

set PYTHON=python
where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
set /p PYTHON=<"%TEMP%\pyfind.txt"
del "%TEMP%\pyfind.txt" 2>nul

echo [INFO] 发送测试消息...
%PYTHON% -X utf8 run.py --mode test --config config.json
echo.
pause