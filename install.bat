@echo off
setlocal EnableExtensions

set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%"
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul 2>&1
cd /d "%~dp0"

set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] Windows PowerShell was not found at:
    echo %POWERSHELL_EXE%
    echo.
    echo Please repair Windows system components before continuing.
    pause
    exit /b 1
)

echo ====================================================
echo YouTube - Lark Dashboard V2 installer
echo ====================================================
echo.
"%POWERSHELL_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Installer completed successfully.
) else (
    echo [ERROR] Installer stopped with exit code %EXIT_CODE%.
    echo Review the message above before trying again.
)
echo.
pause
exit /b %EXIT_CODE%
