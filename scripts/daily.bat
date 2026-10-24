@echo off
chcp 936 >nul
rem ========================================
rem  日报测试
rem ========================================

set PROJECT_DIR=%~dp0..
cd /d %PROJECT_DIR%

set PYTHON=python
where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
set /p PYTHON=<"%TEMP%\pyfind.txt"
del "%TEMP%\pyfind.txt" 2>nul

echo [INFO] 运行日报生成...
%PYTHON% -X utf8 run.py --mode daily --config config.json
echo.
pause