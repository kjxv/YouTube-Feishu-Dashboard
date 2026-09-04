@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

if not exist "runtime\venv\Scripts\python.exe" (
  echo 正在创建 Python 虚拟环境...
  py -3.11 -m venv runtime\venv
  if errorlevel 1 goto :error
)

echo 正在安装或更新项目依赖...
"runtime\venv\Scripts\python.exe" -m pip install --upgrade .
if errorlevel 1 goto :error

"runtime\venv\Scripts\python.exe" -m youtube_feishu_dashboard setup --interactive
if errorlevel 1 goto :error

echo.
echo 首次配置基础步骤已完成。请按屏幕提示继续 YouTube 授权和健康检查。
pause
exit /b 0

:error
echo.
echo 操作失败。请保留本窗口内容，并查看 00_项目文档\首次使用说明.md。
pause
exit /b 1
