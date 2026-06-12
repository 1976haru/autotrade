@echo off
REM Watchdog backend clean-restart (2026-06-12).
REM 정식 런처를 띄우기 *전에* :8000 을 점유한 stale/hung 프로세스를 강제 종료해
REM 이중 기동/좀비/폴백포트(8001) 회피 — 항상 :8000 으로 깨끗이 복구한다.
REM cwd 는 watchdog 이 backend\ 로 설정(Popen cwd) — 본 배치는 종목/주문 로직 0건.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000"') do taskkill /F /PID %%a >nul 2>&1
cd /d "%~dp0..\backend"
python app_desktop_launcher.py
