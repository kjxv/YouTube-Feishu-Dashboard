@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu enable-channel-48h-fields
if errorlevel 1 goto :error
echo.
echo 频道视频主表的48小时字段与共享映射已检查完成。
pause
exit /b 0

:error
echo.
echo 操作失败。没有自动覆盖类型不一致的字段，请保留本窗口内容以便检查。
pause
exit /b 1
