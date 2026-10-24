@echo off
chcp 936 >nul
rem ========================================
rem  新闻推送服务安装/启动脚本 (nssm)
rem  用法: install_service.bat [--silent]
rem ========================================

set PROJECT_DIR=%~dp0..
set NSSM=%~dp0nssm.exe

rem ---- 查找真实 Python 绝对路径 ----
set PYTHON=
if exist "D:\Program Files\Python311\python.exe" set PYTHON=D:\Program Files\Python311\python.exe
if not defined PYTHON if exist "C:\Program Files\Python311\python.exe" set PYTHON=C:\Program Files\Python311\python.exe
if not defined PYTHON if exist "C:\Python311\python.exe" set PYTHON=C:\Python311\python.exe
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if not defined PYTHON if exist "%PROGRAMFILES%\Python311\python.exe" set PYTHON=%PROGRAMFILES%\Python311\python.exe
if not defined PYTHON (
    where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
    set /p PYTHON=<"%TEMP%\pyfind.txt"
    del "%TEMP%\pyfind.txt" >nul 2>&1
)

if not defined PYTHON (
    echo [错误] 未找到 Python 3.11。请先安装并勾选 "Add python.exe to PATH"。
    if /i not "%~1"=="--silent" pause
    exit /b 1
)
echo 使用 Python: %PYTHON%

rem 检查 nssm
if not exist "%NSSM%" (
    echo [错误] 未找到 %NSSM%
    if /i not "%~1"=="--silent" pause
    exit /b 1
)

rem ---- 启动/安装服务 ----
echo 正在启动服务...
sc query news-agent >nul 2>&1
if %ERRORLEVEL%==0 (
    echo 服务已存在，尝试启动...
    sc start news-agent >nul 2>&1
    goto wait_svc
)

echo 服务不存在，开始安装...
"%NSSM%" install news-agent "%PYTHON%" "run.py" --mode service --with-qqbot --config "%PROJECT_DIR%\config.json"
"%NSSM%" set news-agent AppDirectory "%PROJECT_DIR%"
"%NSSM%" set news-agent AppStdout "%PROJECT_DIR%\logs\service.out.log"
"%NSSM%" set news-agent AppStderr "%PROJECT_DIR%\logs\service.err.log"
"%NSSM%" set news-agent AppRotateFiles 1
"%NSSM%" set news-agent AppRotateBytes 2097152
"%NSSM%" set news-agent Start SERVICE_AUTO_START
timeout /t 2 /nobreak >nul
sc start news-agent >nul 2>&1

:wait_svc
set N=0
:check_loop
sc query news-agent 2>nul | find "RUNNING" >nul 2>&1
if %ERRORLEVEL%==0 goto svc_ok
set /a N+=1
if %N% GEQ 8 goto svc_fail
timeout /t 2 /nobreak >nul
goto check_loop

:svc_ok
echo 服务已启动
if /i not "%~1"=="--silent" pause
exit /b 0

:svc_fail
echo [错误] 服务启动失败，请查看日志:
echo   %PROJECT_DIR%\logs\service.err.log
if /i not "%~1"=="--silent" pause
exit /b 1
