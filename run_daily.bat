@echo off
setlocal
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%"
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
cd /d "%~dp0"
if not exist "logs" mkdir "logs"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [%date% %time%] ERROR: Project .venv not found. Run install.bat first.>>"logs\daily.log"
    if /I not "%~1"=="--scheduled" (
        echo.
        echo [ERROR] The project .venv folder was not found.
        echo Please double-click install.bat first. It will create .venv for this PC.
        echo.
        pause
    )
    exit /b 1
)

if /I "%~1"=="--scheduled" (
    "%PYTHON_EXE%" run_sync.py daily --quiet
    exit /b %ERRORLEVEL%
)

echo ====================================================
echo YouTube - Lark DAILY sync
echo The progress below is also saved to logs\daily.log
echo DAILY usually takes longer because it checks every video.
echo ====================================================
echo.

"%PYTHON_EXE%" run_sync.py daily
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo DAILY completed successfully.
) else (
    echo DAILY failed with exit code %EXIT_CODE%.
)
echo You can close this window after reviewing the result.
echo.
pause
exit /b %EXIT_CODE%
