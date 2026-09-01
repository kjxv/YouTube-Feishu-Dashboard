@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu localize-config
echo.
echo 中文化升级完成后，可在飞书中查看中文名称、API来源和用途说明。
pause
