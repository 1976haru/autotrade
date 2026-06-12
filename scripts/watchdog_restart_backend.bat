@echo off
REM Watchdog backend clean-restart. ASCII-only for cmd.exe.
REM Free :8000 (kill stale/hung listener) then start the formal launcher, so recovery
REM always binds :8000 cleanly (no double-instance / no fallback to port 8001).
REM No trading logic, no safety-flag changes.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000"') do taskkill /F /PID %%a >nul 2>&1
cd /d "%~dp0..\backend"
python app_desktop_launcher.py
