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
echo 频道每日统计已启用，首次同步已成功。
echo 请核对飞书三张频道业务表，确认后再恢复统一计划任务。
pause
exit /b 0

:rollback
echo.
echo 首次同步失败，正在把频道每日统计恢复为关闭状态...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu set-channel-history-enabled false
echo 请保留本窗口内容以便检查。
pause
exit /b 1

:error_before_enable
echo.
echo 只读检查或开关设置失败，没有执行频道数据同步。
pause
exit /b 1
