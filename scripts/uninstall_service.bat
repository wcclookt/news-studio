@echo off
chcp 936 >nul
rem ========================================
rem  卸载新闻推送服务
rem ========================================

echo [INFO] 卸载新闻推送服务...
sc stop news-agent >nul 2>&1
timeout /t 3 /nobreak >nul
sc delete news-agent >nul 2>&1

echo [INFO] 服务已卸载
pause