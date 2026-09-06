@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

echo [1/3] 正在进行频道三表只读检查...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard modules validate-sync channel_history
if errorlevel 1 goto :error_before_enable

echo [2/3] 正在开启频道每日统计...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu set-channel-history-enabled true
if errorlevel 1 goto :error_before_enable

echo [3/3] 正在执行首次频道数据同步...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard scheduler run-once channel-history-daily
if errorlevel 1 goto :rollback

echo.
echo Channel history is enabled and the first sync succeeded.
echo Check the three Feishu tables, then resume the unified scheduled task.
pause
exit /b 0

:rollback
echo.
echo First sync failed. Restoring the channel-history switch to disabled...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu set-channel-history-enabled false
echo Keep this window open for troubleshooting.
pause
exit /b 1

:error_before_enable
echo.
echo Validation or switch update failed. Channel sync was not run.
pause
exit /b 1
