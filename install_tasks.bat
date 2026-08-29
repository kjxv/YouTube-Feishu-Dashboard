@echo off
setlocal
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%"
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul 2>&1
cd /d "%~dp0"

set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if /I "%~1"=="--dry-run" goto RUN_TASK_INSTALLER
if /I "%~1"=="--elevated" goto RUN_TASK_INSTALLER

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator permission for Windows Task Scheduler...
    "%POWERSHELL_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs"
    exit /b %ERRORLEVEL%
)

:RUN_TASK_INSTALLER
if /I "%~1"=="--elevated" shift

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] The project .venv folder was not found.
    echo Please double-click install.bat first. It will create .venv for this PC.
    pause
    exit /b 1
)

"%PYTHON_EXE%" install_tasks.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
