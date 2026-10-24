@echo off
chcp 936 >nul
rem ========================================
rem  查看最新日志
rem ========================================

powershell -Command "Get-Content D:\MYSTRICPROJECT\logs\news_agent.log -Tail 30 -Wait"