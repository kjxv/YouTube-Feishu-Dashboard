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
    echo [%date% %time%] ERROR: Project .venv not found. Run install.bat first.>>"logs\backfill.log"
    echo.
    echo [ERROR] The project .venv folder was not found.
    echo Please double-click install.bat first. It will create .venv for this PC.
    echo.
    pause
    exit /b 1
)

echo ====================================================
echo YouTube - Lark FULL HISTORY BACKFILL
echo This retrieves history from channel creation to now.
echo It can take a long time. Progress is saved after each video.
echo If interrupted, run this file again to continue.
echo Log: logs\backfill.log
echo ====================================================
echo.

"%PYTHON_EXE%" run_sync.py backfill
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo BACKFILL completed successfully.
) else (
    echo BACKFILL stopped with exit code %EXIT_CODE%.
    echo Run this file again after reviewing the log.
)
echo You can close this window after reviewing the result.
echo.
pause
exit /b %EXIT_CODE%
