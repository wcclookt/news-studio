@echo off
chcp 936 >nul
rem ========================================
rem  启动 Steam++ (需手动配置代理端口)
rem ========================================

echo [INFO] 请确保 Steam++ (Watt Toolkit) 已安装
echo [INFO] 路径: D:\app\Steam++\Steam++.exe
echo [INFO] 请在 Steam++ 中开启 GitHub 和 Steam 加速
echo [INFO] 代理端口: 26561 (HTTP)
echo.

if exist "D:\app\Steam++\Steam++.exe" (
    echo [INFO] 启动 Steam++...
    start "" "D:\app\Steam++\Steam++.exe"
) else (
    echo [WARN] 未找到 Steam++，请检查路径
)

echo.
pause