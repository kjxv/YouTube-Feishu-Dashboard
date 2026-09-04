@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard scheduler scheduled-run latest-video-tracker
exit /b %errorlevel%
