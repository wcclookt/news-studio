@echo off
chcp 936 >nul
rem ========================================
rem  QQ 交互机器人单独运行
rem ========================================

set PROJECT_DIR=%~dp0..
cd /d %PROJECT_DIR%

set PYTHON=python
where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
set /p PYTHON=<"%TEMP%\pyfind.txt"
del "%TEMP%\pyfind.txt" 2>nul

echo [INFO] 启动 QQ 交互机器人...
%PYTHON% -X utf8 run.py --mode qqbot --config config.json
echo.
pause