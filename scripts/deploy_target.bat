@echo off
chcp 936 >nul
rem ========================================
rem  目标机完整部署脚本
rem 从零开始部署到目标机
rem ========================================

echo ========================================
echo  目标机完整部署
echo ========================================
echo.

echo [1/8] 检查 Python 环境...
where python 2>nul | findstr /v /i "WindowsApps" > "%TEMP%\pyfind.txt"
set /p PYTHON=<"%TEMP%\pyfind.txt"
del "%TEMP%\pyfind.txt" 2>nul
if not defined PYTHON (
    echo [ERROR] 未找到 Python，请先安装 Python 3.11 并勾选 Add to PATH
    pause
    exit /b 1
)
echo [OK] Python: %PYTHON%

echo.
echo [2/8] 安装 Python 依赖...
%PYTHON% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
)
echo [OK] 依赖安装完成

echo.
echo [3/8] 安装 nssm 服务管理工具...
if not exist "%~dp0nssm.exe" (
    echo [WARN] 未找到 nssm.exe，请确保 scripts 目录下有 nssm.exe
)
echo [OK] nssm 就绪

echo.
echo [4/8] 安装/启动 Steam++...
echo [INFO] 请手动安装 Steam++ (Watt Toolkit) 到 D:\app\Steam++
echo [INFO] 安装后请勾选 GitHub 和 Steam 加速
echo [INFO] 确认代理端口: 26561 (HTTP)
pause

echo.
echo [5/8] 安装/启动 NapCat...
echo [INFO] 请运行: D:\MYSTRICPROJECT\scripts\start_napcat.bat
echo [INFO] 扫码登录 QQ 小号
pause

echo.
echo [4/8] 写入配置文件...
if not exist "D:\MYSTRICPROJECT\config.json" (
    copy "D:\MYSTRICPROJECT\config.example.json" "D:\MYSTRICPROJECT\config.json"
    echo [INFO] 已从模板创建 config.json，请编辑填入密钥
    pause
)
echo [OK] config.json 就绪

echo.
echo [6/8] 安装系统服务...
call install_service.bat
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 服务安装失败
    pause
    exit /b 1
)
echo [OK] 服务已安装并启动

echo.
echo [7/8] 设置开机自启...
call setup_autostart.bat

echo.
echo [8/8] 添加 QQ 推送号...
python scripts\manage_qq.py

echo.
echo ========================================
echo 部署完成！
echo ========================================
echo.
echo 后续管理:
echo   启动全套:     start_all.bat
echo   停止全套:     stop_all.bat
echo   实时推送:     realtime.bat
echo   日报测试:     daily.bat
echo   QQ管理:       manage_qq.bat
echo   查看日志:     view_logs.bat
echo   修复检查:     fix_target.bat
echo.
pause