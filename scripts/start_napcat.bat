@echo off
chcp 936 >nul
rem ========================================
rem  启动 NapCat
rem ========================================

echo [INFO] 启动 NapCat...
echo [INFO] 路径: D:\MYSTRICPROJECT\NapCat.Shell.Windows.Node\node.exe
echo [INFO] 请等待启动完成，扫码登录
echo.

cd /d D:\MYSTRICPROJECT\NapCat.Shell.Windows.Node
start "" node.exe index.js

echo [INFO] NapCat 已在新窗口启动
echo [INFO] 请打开新窗口扫码登录
echo [INFO] 登录后日志会显示: [info] [NapCat] 已通知主进程登录成功
echo.
pause