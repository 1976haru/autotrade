@echo off
REM AgentTraderWatchdog entry point (run by Task Scheduler). ASCII-only for cmd.exe.
REM Starts watchdog.py: polls :8000 health, restarts backend via formal launcher only.
REM Bot start: none (watchdog manages the backend process only). Safety flags: untouched.
cd /d "%~dp0.."
python "%~dp0watchdog.py" --backend-script "%~dp0watchdog_restart_backend.bat" --health-url http://127.0.0.1:8000/api/health/full --check-interval 15 --max-restarts 20 --log "%APPDATA%\Autotrade\logs\watchdog.jsonl"
