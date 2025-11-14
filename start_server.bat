@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] PowerShell executable not found at "%POWERSHELL_EXE%".
    echo Please install PowerShell 3.0+ or run start_server.ps1 manually.
    goto :pause
)

if not exist "start_server.ps1" (
    echo [ERROR] start_server.ps1 is missing in the project root.
    goto :pause
)

echo.
echo ================= Claude Backend Launcher =================
echo Keep this window open. Closing it stops the backend service.
echo.
echo Streaming logs below:
echo -----------------------------------------------------------
echo.

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File ".\start_server.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

echo.
echo -----------------------------------------------------------
if "%EXITCODE%"=="0" (
    echo Backend stopped normally. You can close this window.
) else (
    echo Startup or runtime failed with exit code %EXITCODE%.
    echo Please copy the log above and send it to the developer.
)

:pause
echo.
pause
exit /b %EXITCODE%
