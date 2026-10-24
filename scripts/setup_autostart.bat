@echo off
chcp 936 >nul
rem ========================================
rem  设置开机自启（目标机用）
rem ========================================

echo [INFO] 设置开机自启...
schtasks /create /tn "NewsAgent_AutoStart" /tr "cmd /c sc start news-agent" /sc onstart /ru SYSTEM /rl highest /f
if %ERRORLEVEL%==0 (
    echo [OK] 开机自启已设置
) else (
    echo [FAIL] 设置失败，请以管理员权限运行
)
pause