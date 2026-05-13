# 체크리스트 #69: Windows PowerShell DB 백업 스크립트.
#
# 절대 원칙 (CLAUDE.md):
#   1. .env / API key / app secret / 계좌번호 / Telegram token 백업 금지.
#   2. DATABASE_URL을 로그에 그대로 출력하지 않는다 (password 가림).
#   3. 실 broker / 실 KIS API 호출 0건.
#   4. backups/ 디렉터리에만 저장.
#
# 사용:
#   $env:DATABASE_URL = "sqlite:///./backend/data/auto_trader.db"
#   pwsh -File scripts/backup_db.ps1
#
#   # 또는
#   pwsh -File scripts/backup_db.ps1 -DryRun
#
# Linux/macOS 사용자는 scripts/backup_db.sh (bash) 권장.

[CmdletBinding()]
param(
    [string]$DatabaseUrl   = $env:DATABASE_URL,
    [string]$BackupDir     = $(if ($env:BACKUP_DIR) { $env:BACKUP_DIR }
                              else { Join-Path $PSScriptRoot ".." "backups" }),
    [int]   $RetentionDays = $(if ($env:BACKUP_RETENTION_DAYS) { [int]$env:BACKUP_RETENTION_DAYS }
                               else { 14 }),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Windows console UTF-8 (한국어 출력 안전)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Log($msg)  { Write-Host "[backup_db] $msg" }
function Write-Err($msg)  { Write-Host "[backup_db] ERROR: $msg" -ForegroundColor Red }

function Redact-Url([string]$u) {
    if ($null -eq $u) { return "" }
    # postgresql://user:secret@host  →  postgresql://user:***@host
    return ($u -replace '://([^:/@]+):[^@]+@', '://$1:***@')
}

function Abort-IfSecretInput([string]$u) {
    foreach ($pat in @("KIS_APP_KEY=", "KIS_APP_SECRET=", "TELEGRAM_BOT_TOKEN=",
                       "ANTHROPIC_API_KEY=", "OPENAI_API_KEY=")) {
        if ($u -like "*$pat*") {
            Write-Err "DATABASE_URL appears to contain secret-looking tokens; refusing."
            exit 2
        }
    }
}

# ---------- 입력 검증 ----------

if ([string]::IsNullOrEmpty($DatabaseUrl)) {
    Write-Err "DATABASE_URL is required."
    Write-Err "Example: `$env:DATABASE_URL = 'sqlite:///./backend/data/auto_trader.db'"
    exit 1
}

Abort-IfSecretInput $DatabaseUrl

$timestamp = (Get-Date -AsUTC -Format "yyyyMMdd_HHmmss")
$redacted  = Redact-Url $DatabaseUrl

Write-Log "starting backup"
Write-Log ("url:        " + $redacted)
Write-Log ("out dir:    " + $BackupDir)
Write-Log ("retention:  " + $RetentionDays + " days")
Write-Log ("dry run:    " + $DryRun)
Write-Log ("timestamp:  " + $timestamp)

if (-not (Test-Path $BackupDir)) {
    if ($DryRun) {
        Write-Log ("DRY_RUN: would create directory " + $BackupDir)
    } else {
        New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    }
}

# ---------- SQLite ----------

function Backup-Sqlite($url) {
    # sqlite:///path → path
    $raw = $url -replace '^sqlite:///', ''
    if ($raw -eq $url) {
        $raw = $url -replace '^sqlite://', ''
    }
    $dbPath = $raw

    # 상대 경로 풀기
    if (-not [System.IO.Path]::IsPathRooted($dbPath)) {
        $projectRoot = Join-Path $PSScriptRoot ".." | Resolve-Path
        $candidate1 = Join-Path $projectRoot "backend" $dbPath
        $candidate2 = Join-Path $projectRoot $dbPath
        if      (Test-Path $candidate1) { $dbPath = $candidate1 }
        elseif  (Test-Path $candidate2) { $dbPath = $candidate2 }
    }

    if (-not (Test-Path $dbPath)) {
        Write-Err ("sqlite db file not found: " + $dbPath)
        exit 3
    }

    $outFile = Join-Path $BackupDir ("autotrade_backup_" + $timestamp + ".sqlite")
    Write-Log ("sqlite db:  " + $dbPath)
    Write-Log ("writing:    " + $outFile)

    if ($DryRun) {
        Write-Log "DRY_RUN: skipping actual copy"
        return
    }

    # sqlite3 .backup 우선 — WAL/SHM 안전. PowerShell에서 호출 가능하면 사용.
    $sqlite3 = Get-Command sqlite3 -ErrorAction SilentlyContinue
    $usedSqlite3 = $false
    if ($sqlite3) {
        try {
            & $sqlite3.Source $dbPath ".backup '$outFile'" 2>$null
            if (Test-Path $outFile) { $usedSqlite3 = $true; Write-Log "sqlite3 .backup completed" }
        } catch {
            Write-Log "sqlite3 .backup failed; falling back to file copy"
        }
    }
    if (-not $usedSqlite3) {
        Write-Log "using file copy (sqlite3 .backup unavailable)"
        Copy-Item -Path $dbPath -Destination $outFile -Force
    }

    $size = (Get-Item $outFile).Length
    if ($size -le 0) {
        Write-Err ("backup file is empty: " + $outFile)
        exit 4
    }
    Write-Log ("backup ok:  " + $outFile + " (" + $size + " bytes)")
}

# ---------- PostgreSQL ----------

function Backup-Postgres($url) {
    $pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
    if (-not $pgDump) {
        Write-Err "pg_dump not installed."
        Write-Err "Install PostgreSQL client tools and ensure pg_dump is on PATH."
        exit 5
    }

    $outRaw = Join-Path $BackupDir ("autotrade_backup_" + $timestamp + ".sql")
    Write-Log ("pg_dump out: " + $outRaw)

    if ($DryRun) {
        Write-Log "DRY_RUN: skipping pg_dump"
        return
    }

    & $pgDump.Source --no-owner --no-privileges --format=plain --file=$outRaw $url
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pg_dump failed"
        exit 6
    }

    # PowerShell 7+ has Compress-Archive (zip 형식) — gzip 형식 .sql.gz 호환이
    # 까다로워 본 PR 시점에는 raw .sql 유지. 후속에서 7zip 또는 GzipStream로 교체.
    $size = (Get-Item $outRaw).Length
    Write-Log ("backup ok:  " + $outRaw + " (" + $size + " bytes)")
    Write-Log "note: .sql kept uncompressed on Windows (use 7zip / WSL for .sql.gz)"
}

# ---------- 분기 ----------

if ($DatabaseUrl -like "sqlite://*") {
    Backup-Sqlite $DatabaseUrl
} elseif ($DatabaseUrl -like "postgresql://*"     -or
          $DatabaseUrl -like "postgresql+psycopg2://*" -or
          $DatabaseUrl -like "postgresql+psycopg://*" -or
          $DatabaseUrl -like "postgres://*") {
    Backup-Postgres $DatabaseUrl
} else {
    Write-Err "unsupported DATABASE_URL scheme"
    exit 7
}

# ---------- Retention ----------

if ($RetentionDays -gt 0 -and (Test-Path $BackupDir)) {
    Write-Log ("applying retention: keep " + $RetentionDays + " days")
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $candidates = Get-ChildItem -LiteralPath $BackupDir -File `
        -Include "autotrade_backup_*.sqlite", "autotrade_backup_*.sql", "autotrade_backup_*.sql.gz" `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff }
    if ($DryRun) {
        if ($candidates) {
            Write-Log "DRY_RUN: would delete:"
            $candidates | ForEach-Object { Write-Log ("  " + $_.FullName) }
        } else {
            Write-Log "DRY_RUN: no files older than retention"
        }
    } else {
        if ($candidates) {
            foreach ($f in $candidates) {
                Remove-Item -Path $f.FullName -Force
                Write-Log ("deleted: " + $f.FullName)
            }
        } else {
            Write-Log "no files older than retention"
        }
    }
}

Write-Log "done."
exit 0
