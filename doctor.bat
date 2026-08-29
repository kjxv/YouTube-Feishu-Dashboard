@echo off
setlocal EnableExtensions
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%"
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo.
    echo [ERROR] The project Python environment was not found.
    echo Please run install.bat first.
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" doctor.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo Doctor completed successfully.
) else (
    echo Doctor found one or more problems. Review the results above.
)
echo.
pause
exit /b %EXIT_CODE%

