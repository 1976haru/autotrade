@echo off
REM Agent Trader v1 — Windows installer build (#90, cmd wrapper).

setlocal

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%build_windows_installer.ps1

if not exist "%PS1%" (
    echo [ERR] %PS1% not found.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set RC=%ERRORLEVEL%
if not %RC%==0 (
    echo.
    echo build failed (exit code %RC%).
    pause
    exit /b %RC%
)

echo.
echo installer build complete.
pause
endlocal
