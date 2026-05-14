# Agent Trader v1 — Windows installer (EXE/MSI) end-to-end build (#90).
#
# 흐름:
#   1. frontend npm install + build
#   2. backend PyInstaller sidecar build (scripts/build_backend_sidecar.ps1 위임)
#   3. cargo tauri build → src-tauri/target/release/bundle/(msi|nsis)/...
#   4. 산출물 경로 + 크기 + SHA256 출력
#   5. docs/desktop_exe_status.md 갱신용 JSON 결과 stdout
#
# 절대 원칙 (CLAUDE.md):
#   - backend/.env 를 bundle 에 *굽지 않음* — tauri.conf.json resources 비어 있음
#   - KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 출력 0건
#   - ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 수정 0건
#
# 사용:
#   pwsh scripts/build_windows_installer.ps1
#   pwsh scripts/build_windows_installer.ps1 -SkipBackend  # backend sidecar 재빌드 생략
#   pwsh scripts/build_windows_installer.ps1 -SkipFrontend # frontend 재빌드 생략

param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "  Agent Trader v1 — Windows installer build (#90)" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Resolve-Path (Join-Path $scriptDir "..")
$srcTauri  = Join-Path $repoRoot "src-tauri"
$frontend  = Join-Path $repoRoot "frontend"

if (-not (Test-Path $srcTauri)) {
    Write-Host "[ERR] src-tauri 가 없습니다: $srcTauri" -ForegroundColor Red
    exit 1
}

# ---------- 1) toolchain check ----------
Write-Host "[1/5] toolchain check..." -ForegroundColor Cyan
$toolchainOk = $true

function _CheckCmd($name, $required) {
    try {
        $null = Get-Command $name -ErrorAction Stop
        Write-Host "      [OK]  $name" -ForegroundColor Green
        return $true
    } catch {
        if ($required) {
            Write-Host "      [ERR] $name not installed (required)" -ForegroundColor Red
        } else {
            Write-Host "      [..]  $name not installed (optional)" -ForegroundColor Yellow
        }
        return $false
    }
}

$hasNode   = _CheckCmd "node"   $true
$hasNpm    = _CheckCmd "npm"    $true
$hasPython = _CheckCmd "python" $true
$hasCargo  = _CheckCmd "cargo"  $true
$hasRustc  = _CheckCmd "rustc"  $true

if (-not ($hasNode -and $hasNpm -and $hasPython -and $hasCargo -and $hasRustc)) {
    Write-Host ""
    Write-Host "[ERR] 필수 toolchain 누락 — 다음 설치 후 재시도:" -ForegroundColor Red
    Write-Host "      Node 20+      : https://nodejs.org/en/download/" -ForegroundColor Yellow
    Write-Host "      Python 3.12+  : https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "      Rust toolchain: winget install Rustlang.Rustup" -ForegroundColor Yellow
    Write-Host "      Tauri CLI     : cargo install tauri-cli --version `"^2`" --locked" -ForegroundColor Yellow
    exit 2
}

# ---------- 2) frontend ----------
if (-not $SkipFrontend) {
    Write-Host "[2/5] frontend install + build..." -ForegroundColor Cyan
    Push-Location $frontend
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[INFO] npm ci failed; falling back to npm install..." -ForegroundColor Yellow
            & npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } catch {
        Write-Host "[ERR] $_" -ForegroundColor Red
        Pop-Location
        exit 3
    }
    Pop-Location
} else {
    Write-Host "[2/5] (skip frontend — pre-existing dist 사용)" -ForegroundColor Gray
}

# ---------- 3) backend sidecar ----------
if (-not $SkipBackend) {
    Write-Host "[3/5] backend PyInstaller sidecar build..." -ForegroundColor Cyan
    $sidecarScript = Join-Path $scriptDir "build_backend_sidecar.ps1"
    if (-not (Test-Path $sidecarScript)) {
        Write-Host "[ERR] 누락: $sidecarScript" -ForegroundColor Red
        exit 4
    }
    & $sidecarScript -Clean
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERR] backend sidecar build failed (exit=$LASTEXITCODE)" -ForegroundColor Red
        exit 4
    }
} else {
    Write-Host "[3/5] (skip backend — pre-existing src-tauri/binaries 사용)" -ForegroundColor Gray
}

# ---------- 4) tauri build ----------
Write-Host "[4/5] cargo tauri build (release)..." -ForegroundColor Cyan
Push-Location $srcTauri
try {
    # tauri-cli 가 cargo subcommand 인지 검사.
    $tauriOk = $false
    try {
        & cargo tauri --version | Out-Null
        if ($LASTEXITCODE -eq 0) { $tauriOk = $true }
    } catch { $tauriOk = $false }

    if (-not $tauriOk) {
        Write-Host "[INFO] cargo tauri 가 없습니다. tauri-cli 설치 시도..." -ForegroundColor Yellow
        & cargo install tauri-cli --version "^2" --locked
        if ($LASTEXITCODE -ne 0) { throw "cargo install tauri-cli failed" }
    }

    & cargo tauri build
    if ($LASTEXITCODE -ne 0) { throw "cargo tauri build failed (exit=$LASTEXITCODE)" }
} catch {
    Write-Host "[ERR] $_" -ForegroundColor Red
    Pop-Location
    exit 5
}
Pop-Location

# ---------- 5) collect artifacts ----------
Write-Host "[5/5] collecting artifacts..." -ForegroundColor Cyan

$bundleRoot = Join-Path $srcTauri "target\release\bundle"
$artifacts  = @()

foreach ($pattern in @("*.msi", "*.exe")) {
    if (Test-Path $bundleRoot) {
        $found = Get-ChildItem -Recurse -Path $bundleRoot -Filter $pattern -ErrorAction SilentlyContinue
        foreach ($f in $found) {
            $hash = (Get-FileHash -Algorithm SHA256 -Path $f.FullName).Hash
            $artifacts += [PSCustomObject]@{
                path       = $f.FullName
                size_bytes = $f.Length
                created    = $f.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
                sha256     = $hash
                kind       = $f.Extension.TrimStart(".").ToLower()
            }
        }
    }
}

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "  결과" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
if ($artifacts.Count -eq 0) {
    Write-Host "[WARN] installer artifact 가 생성되지 않았습니다." -ForegroundColor Yellow
    Write-Host "       기대 위치: $bundleRoot" -ForegroundColor Yellow
} else {
    foreach ($a in $artifacts) {
        Write-Host ""
        Write-Host "  kind   : $($a.kind)" -ForegroundColor Green
        Write-Host "  path   : $($a.path)"
        Write-Host "  size   : $($a.size_bytes) bytes"
        Write-Host "  created: $($a.created)"
        Write-Host "  sha256 : $($a.sha256)"
    }
    Write-Host ""
    Write-Host "다음 단계:" -ForegroundColor Yellow
    Write-Host "  1) docs/desktop_exe_status.md 의 산출물 표 갱신" -ForegroundColor Yellow
    Write-Host "  2) GitHub Release 에 .msi / setup.exe 첨부 (latest.json 은 후속 PR)" -ForegroundColor Yellow
    Write-Host "  3) 베타테스터에게 docs/exe_oneclick_installation.md 안내" -ForegroundColor Yellow
}

# JSON for docs automation.
$jsonOut = @{
    artifacts = $artifacts
    built_at  = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
} | ConvertTo-Json -Depth 5
Write-Host ""
Write-Host "----- JSON output -----"
Write-Host $jsonOut
Write-Host "-----------------------"
