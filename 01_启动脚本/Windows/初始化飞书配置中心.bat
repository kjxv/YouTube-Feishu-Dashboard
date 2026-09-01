@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu bootstrap-config
echo.
echo 若显示成功，四张公共配置表的 Table ID 已写入 .env。
pause
