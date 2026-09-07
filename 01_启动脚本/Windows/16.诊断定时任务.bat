@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "05_部署与运维\windows\Manage-YfdScheduledTask.ps1" -Action Diagnose
set "YFD_EXIT=%errorlevel%"
echo.
echo 请把从 INSTALLED 开始到日志末尾的输出截图或复制出来。
pause
exit /b %YFD_EXIT%
