@echo off
REM Agent Trader v1 — backend PyInstaller sidecar build (#90, cmd wrapper).
REM
REM 더블클릭으로 사용 가능한 진입점. 내부적으로 PowerShell .ps1 호출.
REM
REM 안전: backend\.env 를 bundle 에 *굽지 않습니다*. Secret 출력 0건.

setlocal

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%build_backend_sidecar.ps1

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
echo backend sidecar build complete.
pause
endlocal
