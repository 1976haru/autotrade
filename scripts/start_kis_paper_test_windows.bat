@echo off
REM ==========================================================
REM Agent Trader v1 — KIS Paper one-click test launcher (#89)
REM
REM 이 스크립트는 한투 모의투자 테스트용입니다.
REM 실제 돈이 나가지 않습니다. KIS_IS_PAPER=true 가 강제됩니다.
REM
REM 사용법: 더블클릭하거나 cmd 에서 실행
REM
REM 안전:
REM  - Secret 입력을 받지 않습니다. backend/.env 에서만 설정.
REM  - ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 을 *수정하지 않습니다*.
REM  - 빌드 산출물 / 키 / Secret 을 생성하거나 출력하지 않습니다.
REM ==========================================================

setlocal enabledelayedexpansion

echo.
echo ===========================================
echo Agent Trader v1 — KIS 모의투자 테스트 시작
echo ===========================================
echo.
echo 안내: 이 스크립트는 한투 모의투자 전용입니다.
echo       실제 돈이 나가지 않습니다.
echo.

REM 1. 프로젝트 경로 검증.
if not exist "%~dp0..\backend\app\main.py" (
    echo [오류] backend 폴더를 찾을 수 없습니다.
    echo        스크립트 위치 또는 프로젝트 구조를 확인하세요.
    echo        예상 경로: %~dp0..\backend\app\main.py
    pause
    exit /b 1
)

cd /d "%~dp0.."

REM 2. Python 확인.
where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python 이 설치되어 있지 않습니다.
    echo        https://www.python.org/downloads/ 에서 Python 3.12+ 설치 후 재시도.
    pause
    exit /b 1
)

REM 3. backend/.env 존재 여부 확인 (값은 읽지 않음).
if not exist "backend\.env" (
    echo [안내] backend\.env 가 없습니다.
    echo        backend\.env.example 을 복사한 후 KIS 모의투자 키를 채우세요.
    echo        - KIS_APP_KEY
    echo        - KIS_APP_SECRET
    echo        - KIS_ACCOUNT_NO
    echo        - KIS_IS_PAPER=true ^(반드시 true^)
    echo        - ENABLE_LIVE_TRADING=false ^(반드시 false^)
    echo.
    echo        키가 없어도 *Mock 모드 테스트* 는 가능합니다.
)

REM 4. 의존성 설치 확인 (idempotent).
echo [1/3] Python 의존성 확인 중...
cd backend
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [오류] 의존성 설치 실패. 네트워크 / pip 권한 확인 필요.
    pause
    exit /b 2
)

REM 5. backend 서버 실행.
echo [2/3] Backend 서버 시작 ^(http://127.0.0.1:8000^)
echo        Ctrl+C 로 종료할 수 있습니다.
echo.
echo [3/3] 브라우저에서 접속:
echo        - 로컬:    http://localhost:5173
echo        - PC IP:   http://^<PC_IP^>:5173
echo        - Tailscale: docs\tailscale_smartphone_access.md 참고
echo.
echo 접속 후 *대시보드* 의 "한투 모의투자 AI 자동매매 테스트" 카드에서
echo "준비상태 확인" → "한투 모의 빠른 점검 시작" 순서로 진행하세요.
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
    echo [오류] backend 시작 실패. 포트 8000 충돌 또는 의존성 누락 가능.
    pause
    exit /b 3
)

endlocal
