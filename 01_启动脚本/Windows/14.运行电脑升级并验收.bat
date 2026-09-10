@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

if not exist "runtime\venv\Scripts\python.exe" (
  echo 没有找到本机运行环境。请先运行 1.首次配置.bat。
  goto :error
)

echo [1/7] 正在安装当前项目版本...
"runtime\venv\Scripts\python.exe" -m pip install --upgrade .
if errorlevel 1 goto :error

echo [2/7] 正在检查本机配置、数据库和在线连接...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard doctor --online
if errorlevel 1 goto :error

echo [3/7] 正在创建或补齐系统运行状态表...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu enable-runtime-status
if errorlevel 1 goto :error

echo [4/7] 正在确认视频追踪1至72小时节点字段和共享映射...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu enable-latest-milestone-fields
if errorlevel 1 goto :error

echo [5/7] 正在确认频道48小时字段和共享映射...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu enable-channel-48h-fields
if errorlevel 1 goto :error

echo [6/7] 正在只读检查最新视频三张业务表...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard modules validate-sync latest_video_tracker
if errorlevel 1 goto :error

echo [7/7] 正在只读检查频道三张业务表...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard modules validate-sync channel_history
if errorlevel 1 goto :error

echo.
echo Upgrade and read-only validation completed.
echo Channel sync was not started, and the channel module was not enabled.
pause
exit /b 0

:error
echo.
echo Upgrade or validation failed. The channel module was not enabled.
pause
exit /b 1
