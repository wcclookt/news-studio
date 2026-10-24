@echo off
chcp 936 >nul
rem ========================================
rem  管理 QQ 推送号
rem ========================================

set PROJECT_DIR=%~dp0..
cd /d %PROJECT_DIR%

set PYTHON=python
where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
set /p PYTHON=<"%TEMP%\pyfind.txt"
del "%TEMP%\pyfind.txt" 2>nul

echo [INFO] 启动 QQ 管理工具...
%PYTHON% -X utf8 scripts\manage_qq.py
echo.
pause