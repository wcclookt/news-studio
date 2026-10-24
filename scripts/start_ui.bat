@echo off
rem Compatibility entry point; provisioning lives in the root launcher.
call "%~dp0..\start_ui.bat" %*
exit /b %ERRORLEVEL%
