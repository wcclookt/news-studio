@echo off
chcp 936 >nul
rem ========================================
rem  取消开机自启（本机用）
rem ========================================

echo [INFO] 取消开机自启...
schtasks /delete /tn "NewsAgent_AutoStart" /f
if %ERRORLEVEL%==0 (
    echo [OK] 已取消开机自启
) else (
    echo [INFO] 任务不存在或已删除
)
pause