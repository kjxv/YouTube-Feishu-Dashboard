@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

if not exist "runtime\venv\Scripts\python.exe" (
  echo 没有找到本机运行环境。请先运行 1.首次配置.bat。
  goto :error
)

echo [1/4] 正在安装当前项目版本...
"runtime\venv\Scripts\python.exe" -m pip install --upgrade .
if errorlevel 1 goto :error

echo [2/4] 正在检查本机配置、数据库和在线连接...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard doctor --online
if errorlevel 1 goto :error

echo [3/4] 正在确认48小时字段和共享映射...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard feishu enable-channel-48h-fields
if errorlevel 1 goto :error

echo [4/4] 正在只读检查频道三张业务表...
"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard modules validate-sync channel_history
if errorlevel 1 goto :error

echo.
echo 运行电脑升级和只读验收全部通过。
echo 现在仍未运行频道数据同步，也没有自动开启频道模块。
pause
exit /b 0

:error
echo.
echo 升级或验收失败。频道模块不会由本脚本自动开启，请保留窗口内容以便检查。
pause
exit /b 1
