@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."

if not "%~1"=="" (
  set "YFD_ACTION=%~1"
  goto prepare_action
)

type "01_启动脚本\Windows\scheduler-menu-zh.txt"
set /p YFD_CHOICE=Choice (0-8):

if "%YFD_CHOICE%"=="0" exit /b 0
if "%YFD_CHOICE%"=="1" set "YFD_ACTION=Install"
if "%YFD_CHOICE%"=="2" set "YFD_ACTION=Status"
if "%YFD_CHOICE%"=="3" set "YFD_ACTION=Pause"
if "%YFD_CHOICE%"=="4" set "YFD_ACTION=Resume"
if "%YFD_CHOICE%"=="5" set "YFD_ACTION=RunNow"
if "%YFD_CHOICE%"=="6" set "YFD_ACTION=Uninstall"
if "%YFD_CHOICE%"=="7" set "YFD_ACTION=Diagnose"
if "%YFD_CHOICE%"=="8" set "YFD_ACTION=Stop"

if not defined YFD_ACTION (
  echo Invalid choice.
  pause
  exit /b 1
)

:prepare_action
if /i "%YFD_ACTION%"=="Status" goto run_action
if /i "%YFD_ACTION%"=="Diagnose" goto run_action

powershell.exe -NoProfile -Command "if (([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo Requesting administrator permission...
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%YFD_ACTION%'"
  if errorlevel 1 (
    echo Administrator permission was not granted.
    pause
    exit /b 1
  )
  exit /b 0
)

:run_action
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "05_部署与运维\windows\Manage-YfdScheduledTask.ps1" -Action %YFD_ACTION%
set "YFD_EXIT=%errorlevel%"
echo.
if "%YFD_EXIT%"=="0" (
  echo Operation finished. LAST_RESULT=0 means success.
) else (
  echo Operation failed. Please keep this window for troubleshooting.
)
pause
exit /b %YFD_EXIT%
