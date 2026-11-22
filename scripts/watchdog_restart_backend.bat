@echo off
REM P0 EMERGENCY DISABLE 2026-11-01: blocked until the source-run guard (4918dac) is merged.
REM The new KIS paper ops account must not be traded by this old source backend.
REM Original commands are kept below, unreachable. Backup: %APPDATA%\Autotrade\backups\pre-main-p0-disable-*
echo [P0-DISABLED] %~nx0 is disabled (source backend must not run with the ops account). 1>&2
exit /b 1
REM Watchdog backend clean-restart. ASCII-only for cmd.exe.
REM Free :8000 (kill stale/hung listener) then start the formal launcher, so recovery
REM always binds :8000 cleanly (no double-instance / no fallback to port 8001).
REM No trading logic, no safety-flag changes.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000"') do taskkill /F /PID %%a >nul 2>&1
cd /d "%~dp0..\backend"
python app_desktop_launcher.py
