# Agent Trader v1 — KIS Paper one-click test launcher (#89, PowerShell)
#
# 이 스크립트는 한투 모의투자 테스트용입니다.
# 실제 돈이 나가지 않습니다. KIS_IS_PAPER=true 가 강제됩니다.
#
# 안전:
#   - Secret 입력을 받지 않습니다. backend/.env 에서만 설정.
#   - ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 을 *수정하지 않습니다*.
#   - 키 / Secret 을 생성하거나 출력하지 않습니다.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "Agent Trader v1 — KIS 모의투자 테스트 시작" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "안내: 이 스크립트는 한투 모의투자 전용입니다." -ForegroundColor Yellow
Write-Host "      실제 돈이 나가지 않습니다." -ForegroundColor Yellow
Write-Host ""

# 1. 프로젝트 경로 검증.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Resolve-Path (Join-Path $scriptDir "..")
$mainPy    = Join-Path $repoRoot "backend\app\main.py"

if (-not (Test-Path $mainPy)) {
    Write-Host "[오류] backend 폴더를 찾을 수 없습니다." -ForegroundColor Red
    Write-Host "       예상 경로: $mainPy" -ForegroundColor Red
    Read-Host "엔터를 누르면 종료됩니다"
    exit 1
}

Set-Location $repoRoot

# 2. Python 확인.
try {
    $null = Get-Command python -ErrorAction Stop
} catch {
    Write-Host "[오류] Python 이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host "       https://www.python.org/downloads/ 에서 Python 3.12+ 설치 후 재시도." -ForegroundColor Red
    Read-Host "엔터를 누르면 종료됩니다"
    exit 1
}

# 3. backend/.env 존재 안내 (값은 읽지 않음).
$envPath = Join-Path $repoRoot "backend\.env"
if (-not (Test-Path $envPath)) {
    Write-Host "[안내] backend\.env 가 없습니다." -ForegroundColor Yellow
    Write-Host "       backend\.env.example 을 복사한 후 KIS 모의투자 키를 채우세요." -ForegroundColor Yellow
    Write-Host "       - KIS_APP_KEY" -ForegroundColor Yellow
    Write-Host "       - KIS_APP_SECRET" -ForegroundColor Yellow
    Write-Host "       - KIS_ACCOUNT_NO" -ForegroundColor Yellow
    Write-Host "       - KIS_IS_PAPER=true (반드시 true)" -ForegroundColor Yellow
    Write-Host "       - ENABLE_LIVE_TRADING=false (반드시 false)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "       키가 없어도 *Mock 모드 테스트* 는 가능합니다." -ForegroundColor Yellow
}

# 4. 의존성 설치 확인.
Write-Host "[1/3] Python 의존성 확인 중..." -ForegroundColor Cyan
Push-Location (Join-Path $repoRoot "backend")
try {
    & python -m pip install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "pip install 실패"
    }
} catch {
    Write-Host "[오류] 의존성 설치 실패: $_" -ForegroundColor Red
    Pop-Location
    Read-Host "엔터를 누르면 종료됩니다"
    exit 2
}

# 5. backend 시작.
Write-Host "[2/3] Backend 서버 시작 (http://127.0.0.1:8000)" -ForegroundColor Cyan
Write-Host "       Ctrl+C 로 종료할 수 있습니다." -ForegroundColor Gray
Write-Host ""
Write-Host "[3/3] 브라우저에서 접속:" -ForegroundColor Cyan
Write-Host "       - 로컬:      http://localhost:5173" -ForegroundColor Gray
Write-Host "       - PC IP:     http://<PC_IP>:5173" -ForegroundColor Gray
Write-Host "       - Tailscale: docs/tailscale_smartphone_access.md 참고" -ForegroundColor Gray
Write-Host ""
Write-Host "접속 후 *대시보드* 의 ""한투 모의투자 AI 자동매매 테스트"" 카드에서" -ForegroundColor Yellow
Write-Host """준비상태 확인"" → ""한투 모의 빠른 점검 시작"" 순서로 진행하세요." -ForegroundColor Yellow
Write-Host ""

try {
    & python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} catch {
    Write-Host "[오류] backend 시작 실패: $_" -ForegroundColor Red
    Write-Host "       포트 8000 충돌 또는 의존성 누락 가능." -ForegroundColor Red
    Pop-Location
    Read-Host "엔터를 누르면 종료됩니다"
    exit 3
}
Pop-Location
