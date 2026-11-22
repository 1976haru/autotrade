@echo off
REM P0 EMERGENCY DISABLE 2026-11-01: blocked until the source-run guard (4918dac) is merged.
REM The new KIS paper ops account must not be traded by this old source backend.
REM Original commands are kept below, unreachable. Backup: %APPDATA%\Autotrade\backups\pre-main-p0-disable-*
echo [P0-DISABLED] %~nx0 is disabled (source backend must not run with the ops account). 1>&2
exit /b 1
REM AgentTraderWatchdog entry point (run by Task Scheduler). ASCII-only for cmd.exe.
REM Starts watchdog.py: polls :8000 health, restarts backend via formal launcher only.
REM Bot start: none (watchdog manages the backend process only). Safety flags: untouched.
cd /d "%~dp0.."
python "%~dp0watchdog.py" --backend-script "%~dp0watchdog_restart_backend.bat" --health-url http://127.0.0.1:8000/api/health/full --check-interval 15 --max-restarts 20 --log "%APPDATA%\Autotrade\logs\watchdog.jsonl"
