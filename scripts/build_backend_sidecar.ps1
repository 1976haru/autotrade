# Agent Trader v1 — backend PyInstaller sidecar build (#90, PowerShell)
#
# 목적: backend FastAPI app 을 단일 EXE (`autotrade-backend.exe`) 로 빌드.
#       빌드 산출물을 Tauri sidecar 가 기대하는 위치에 복사한다.
#
# 절대 원칙 (CLAUDE.md):
#   - 실제 .env 를 EXE bundle 에 *굽지 않는다* — 본 스크립트는 .env 를
#     PyInstaller `--add-data` 로 추가하지 않는다.
#   - Secret 출력 0건 — KIS / Anthropic / Telegram 키를 stdout 에 출력하지
#     않는다 (이름조차 출력 없음).
#   - 실행할 entrypoint 는 `backend/app_desktop_launcher.py` 만 — broker /
#     OrderExecutor 를 직접 호출하지 *않는* uvicorn wrapper.
#
# 사용:
#   pwsh scripts/build_backend_sidecar.ps1
#   pwsh scripts/build_backend_sidecar.ps1 -Clean   # build/dist 제거 후 빌드
#
# 출력:
#   backend/dist/autotrade-backend.exe
#   src-tauri/binaries/autotrade-backend-x86_64-pc-windows-msvc.exe
#   (sidecar suffix 는 Tauri externalBin 규칙)

param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "  backend sidecar build (#90)" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 경로 검증.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Resolve-Path (Join-Path $scriptDir "..")
$backend   = Join-Path $repoRoot "backend"
$launcher  = Join-Path $backend "app_desktop_launcher.py"
$reqFile   = Join-Path $backend "requirements.txt"

if (-not (Test-Path $launcher)) {
    Write-Host "[ERR] launcher 가 없습니다: $launcher" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $reqFile)) {
    Write-Host "[ERR] requirements.txt 가 없습니다: $reqFile" -ForegroundColor Red
    exit 1
}

# 2. Python 확인.
try {
    $pyVer = (& python --version) 2>&1
    Write-Host "[OK] $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[ERR] Python 이 설치되어 있지 않습니다. https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# 3. clean.
if ($Clean) {
    Write-Host "[1/5] clean build/dist..." -ForegroundColor Cyan
    foreach ($d in @("$backend\build", "$backend\dist", "$backend\autotrade-backend.spec")) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    }
} else {
    Write-Host "[1/5] (skip clean — use -Clean to wipe)" -ForegroundColor Gray
}

# 4. backend 의존성 + pyinstaller.
Write-Host "[2/5] install backend requirements + pyinstaller..." -ForegroundColor Cyan
Push-Location $backend
try {
    & python -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements failed" }
    & python -m pip install --quiet "pyinstaller>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }
} catch {
    Write-Host "[ERR] $_" -ForegroundColor Red
    Pop-Location
    exit 2
}

# 5. PyInstaller 빌드.
Write-Host "[3/5] pyinstaller build (--onefile)..." -ForegroundColor Cyan

# hidden-imports: FastAPI / uvicorn / alembic 의 plugin-style import 들이
# PyInstaller static analyzer 에 잡히지 않아 명시 필요.
$hiddenImports = @(
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "app.main",
    "app.core.config",
    "app.core.modes",
    "app.api.routes_status",
    "app.api.routes_kis_paper",
    "alembic"
)
$hiArgs = @()
foreach ($h in $hiddenImports) {
    $hiArgs += "--hidden-import"
    $hiArgs += $h
}

# data 파일: alembic.ini + alembic/ (DB 마이그레이션용).
# 본 스크립트는 backend/.env 를 *추가하지 않는다* — Secret bundle 0건 정책.
$collectAll = @("--collect-submodules", "app")

# --noconsole 은 *쓰지 않는다* — 디버그 출력 가능하도록 console window 유지.
# 베타테스터에게는 Tauri main window 가 떠 있고 backend console 은 background.
try {
    & python -m PyInstaller --noconfirm --onefile `
        --name "autotrade-backend" `
        --distpath "$backend\dist" `
        --workpath "$backend\build" `
        --specpath "$backend" `
        @hiArgs `
        @collectAll `
        --add-data "$backend\alembic;alembic" `
        --add-data "$backend\alembic.ini;." `
        "$launcher"
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed (exit=$LASTEXITCODE)" }
} catch {
    Write-Host "[ERR] $_" -ForegroundColor Red
    Pop-Location
    exit 3
}
Pop-Location

$builtExe = Join-Path $backend "dist\autotrade-backend.exe"
if (-not (Test-Path $builtExe)) {
    Write-Host "[ERR] 산출물이 없습니다: $builtExe" -ForegroundColor Red
    exit 4
}

# 6. Tauri sidecar 위치로 복사.
Write-Host "[4/5] copy sidecar binary into src-tauri/binaries/..." -ForegroundColor Cyan
$sidecarDir   = Join-Path $repoRoot "src-tauri\binaries"
$null = New-Item -ItemType Directory -Force -Path $sidecarDir

# Tauri externalBin 규칙: <name>-<target-triple>.exe (Windows x64 default).
$sidecarName  = "autotrade-backend-x86_64-pc-windows-msvc.exe"
$sidecarPath  = Join-Path $sidecarDir $sidecarName
Copy-Item -Force -Path $builtExe -Destination $sidecarPath

$exeSize = (Get-Item $sidecarPath).Length
$exeHash = (Get-FileHash -Algorithm SHA256 -Path $sidecarPath).Hash

Write-Host "[5/5] done." -ForegroundColor Green
Write-Host ""
Write-Host "  built : $builtExe"
Write-Host "  copy  : $sidecarPath"
Write-Host "  size  : $exeSize bytes"
Write-Host "  sha256: $exeHash"
Write-Host ""
Write-Host "next: scripts/build_windows_installer.ps1   # tauri build" -ForegroundColor Yellow
