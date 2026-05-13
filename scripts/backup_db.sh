#!/usr/bin/env bash
#
# 체크리스트 #69: DB 백업 스크립트 (SQLite + PostgreSQL).
#
# 절대 원칙 (CLAUDE.md):
#   1. .env / API key / app secret / 계좌번호 / Telegram token 백업 금지.
#      본 스크립트는 *DB만* 백업한다 — .env / secrets 파일은 절대 손대지 않음.
#   2. DATABASE_URL을 로그에 그대로 출력하지 않는다 (URL에 password 들어있을
#      수 있음). redacted form만 출력.
#   3. 실 broker / 실 KIS API 호출 0건 — 본 스크립트는 DB만 다룬다.
#   4. 백업 파일은 backups/ 디렉터리에만 저장. 운영자가 명시 변경 가능.
#
# 환경 변수:
#   DATABASE_URL              — 필수. sqlite:///path 또는 postgresql://user:pw@host/db
#   BACKUP_DIR                — 백업 출력 디렉터리 (default: backups)
#   BACKUP_RETENTION_DAYS     — N일 지난 파일 자동 삭제 (default: 14, 0=비활성)
#   BACKUP_COMPRESS           — true/false (default: true). false면 압축 X
#   BACKUP_DRY_RUN            — true면 동작만 출력하고 실제 백업 X (default: false)
#
# 사용:
#   DATABASE_URL=sqlite:///./data/auto_trader.db bash scripts/backup_db.sh
#   DATABASE_URL=postgresql://user:pass@host:5432/db bash scripts/backup_db.sh
#   BACKUP_DRY_RUN=true bash scripts/backup_db.sh
#
# 결과:
#   backups/autotrade_backup_YYYYMMDD_HHMMSS.sqlite        (SQLite)
#   backups/autotrade_backup_YYYYMMDD_HHMMSS.sql.gz        (PostgreSQL, gzip)
#
# 스케줄링 예시:
#   Linux cron: 0 3 * * * cd /path/autotrade && bash scripts/backup_db.sh >> backups/cron.log 2>&1
#   Windows Task Scheduler: scripts/backup_db.ps1 사용 권장.

set -euo pipefail

# ----------------------------------------------------------------
# 0. 환경 / 기본값
# ----------------------------------------------------------------

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
COMPRESS="${BACKUP_COMPRESS:-true}"
DRY_RUN="${BACKUP_DRY_RUN:-false}"

DATABASE_URL="${DATABASE_URL:-}"

TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"

# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

log() { printf '[backup_db] %s\n' "$*"; }
err() { printf '[backup_db] ERROR: %s\n' "$*" >&2; }

# DATABASE_URL의 password / token 부분을 가리고 안전한 형태로 반환.
#   예: postgresql://user:secret@host:5432/db  →  postgresql://user:***@host:5432/db
#   예: sqlite:///./data/auto_trader.db        →  sqlite:///./data/auto_trader.db (변경 없음)
redact_url() {
  local url="$1"
  printf '%s' "$url" | sed -E 's|(://[^:/@]+):[^@]+@|\1:***@|'
}

ensure_dir() {
  local dir="$1"
  if [ ! -d "$dir" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log "DRY_RUN: would create directory $dir"
    else
      mkdir -p "$dir"
    fi
  fi
}

# DATABASE_URL이 .env / 다른 secret 파일이면 즉시 중단. 운영자가 실수로
# `DATABASE_URL=$(cat .env)` 같은 사고를 친 경우 차단.
abort_if_secret_input() {
  local url="$1"
  case "$url" in
    *KIS_APP_KEY=*|*KIS_APP_SECRET=*|*TELEGRAM_BOT_TOKEN=*|*ANTHROPIC_API_KEY=*|*OPENAI_API_KEY=*)
      err "DATABASE_URL appears to contain secret-looking tokens; refusing."
      err "DATABASE_URL must be a plain DB connection string."
      exit 2
      ;;
  esac
}

# ----------------------------------------------------------------
# 1. 입력 검증
# ----------------------------------------------------------------

if [ -z "$DATABASE_URL" ]; then
  err "DATABASE_URL is required."
  err "Examples:"
  err "  DATABASE_URL=sqlite:///./data/auto_trader.db bash scripts/backup_db.sh"
  err "  DATABASE_URL=postgresql://user:pass@host/db bash scripts/backup_db.sh"
  exit 1
fi

abort_if_secret_input "$DATABASE_URL"

REDACTED="$(redact_url "$DATABASE_URL")"
log "starting backup"
log "url:        $REDACTED"
log "out dir:    $BACKUP_DIR"
log "retention:  ${RETENTION_DAYS} days"
log "compress:   $COMPRESS"
log "dry run:    $DRY_RUN"
log "timestamp:  $TIMESTAMP"

ensure_dir "$BACKUP_DIR"

# ----------------------------------------------------------------
# 2. SQLite 분기
# ----------------------------------------------------------------

backup_sqlite() {
  # DATABASE_URL 형식: sqlite:///path  (3 slash = relative or absolute)
  # 추출: sqlite:/// → 빈 prefix 제거
  local raw="${DATABASE_URL#sqlite:///}"
  # sqlite://path 도 허용 (2 slash) — 일반적이지 않지만 fallback
  if [ "$raw" = "$DATABASE_URL" ]; then
    raw="${DATABASE_URL#sqlite://}"
  fi
  local db_path="$raw"

  # 상대 경로면 PROJECT_ROOT의 backend/에서 풀어준다 — auto_trader는 backend/
  # 에서 실행되므로 backend/data/auto_trader.db가 표준.
  case "$db_path" in
    /*) : ;;  # absolute, 그대로
    *)
      if [ -f "$PROJECT_ROOT/backend/$db_path" ]; then
        db_path="$PROJECT_ROOT/backend/$db_path"
      elif [ -f "$PROJECT_ROOT/$db_path" ]; then
        db_path="$PROJECT_ROOT/$db_path"
      else
        # 그대로 시도 — 실패 시 명확한 에러
        :
      fi
      ;;
  esac

  if [ ! -f "$db_path" ]; then
    err "sqlite db file not found: $db_path"
    exit 3
  fi

  local out="$BACKUP_DIR/autotrade_backup_${TIMESTAMP}.sqlite"
  log "sqlite db:  $db_path"
  log "writing:    $out"

  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: skipping actual backup"
    return 0
  fi

  # 우선 sqlite3 .backup 시도 — WAL/SHM 안전. 실패 시 file copy fallback.
  if command -v sqlite3 >/dev/null 2>&1; then
    if sqlite3 "$db_path" ".backup '$out'" 2>/dev/null; then
      log "sqlite3 .backup completed"
    else
      log "sqlite3 .backup failed; falling back to file copy"
      cp "$db_path" "$out"
    fi
  else
    log "sqlite3 not installed; using file copy fallback"
    cp "$db_path" "$out"
  fi

  # WAL/SHM이 존재하면 *경고만* — sqlite3 .backup이 이미 일관성 보장.
  for ext in -wal -shm; do
    if [ -f "${db_path}${ext}" ]; then
      log "note: ${ext} file detected — sqlite3 .backup ensures consistency"
    fi
  done

  # 크기 0 검증
  if [ ! -s "$out" ]; then
    err "backup file is empty: $out"
    exit 4
  fi
  local size
  size=$(wc -c < "$out" | tr -d ' ')
  log "backup ok:  $out (${size} bytes)"
}

# ----------------------------------------------------------------
# 3. PostgreSQL 분기
# ----------------------------------------------------------------

backup_postgres() {
  if ! command -v pg_dump >/dev/null 2>&1; then
    err "pg_dump not installed."
    err "Install postgresql-client (apt: postgresql-client / brew: libpq) and re-run."
    exit 5
  fi

  local out_raw="$BACKUP_DIR/autotrade_backup_${TIMESTAMP}.sql"
  local out_gz="$out_raw.gz"
  log "pg_dump out: $out_raw"

  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: skipping pg_dump"
    return 0
  fi

  # password는 URL의 일부 — pg_dump가 그대로 읽음. log엔 redacted URL만.
  # -Fc(custom) 대신 plain SQL — 후속 inspect / diff 편의성 우선.
  if ! pg_dump --no-owner --no-privileges --format=plain --file="$out_raw" "$DATABASE_URL"; then
    err "pg_dump failed"
    exit 6
  fi

  if [ "$COMPRESS" = "true" ]; then
    if command -v gzip >/dev/null 2>&1; then
      gzip -f "$out_raw"
      local size
      size=$(wc -c < "$out_gz" | tr -d ' ')
      log "backup ok:  $out_gz (${size} bytes)"
    else
      log "gzip not installed; leaving plain .sql"
      log "backup ok:  $out_raw"
    fi
  else
    local size
    size=$(wc -c < "$out_raw" | tr -d ' ')
    log "backup ok:  $out_raw (${size} bytes)"
  fi
}

# ----------------------------------------------------------------
# 4. 분기 — DATABASE_URL prefix로
# ----------------------------------------------------------------

case "$DATABASE_URL" in
  sqlite://*)
    backup_sqlite
    ;;
  postgresql://*|postgresql+psycopg2://*|postgresql+psycopg://*|postgres://*)
    backup_postgres
    ;;
  *)
    err "unsupported DATABASE_URL scheme. Use sqlite:/// or postgresql://"
    exit 7
    ;;
esac

# ----------------------------------------------------------------
# 5. Retention — 오래된 백업 자동 삭제
# ----------------------------------------------------------------

if [ "$RETENTION_DAYS" -gt 0 ] && [ -d "$BACKUP_DIR" ]; then
  log "applying retention: keep ${RETENTION_DAYS} days"
  if [ "$DRY_RUN" = "true" ]; then
    candidates=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
      \( -name 'autotrade_backup_*.sqlite' \
         -o -name 'autotrade_backup_*.sql' \
         -o -name 'autotrade_backup_*.sql.gz' \) \
      -mtime +"$RETENTION_DAYS" 2>/dev/null || true)
    if [ -n "$candidates" ]; then
      log "DRY_RUN: would delete:"
      printf '%s\n' "$candidates"
    else
      log "DRY_RUN: no files older than ${RETENTION_DAYS} days"
    fi
  else
    deleted=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
      \( -name 'autotrade_backup_*.sqlite' \
         -o -name 'autotrade_backup_*.sql' \
         -o -name 'autotrade_backup_*.sql.gz' \) \
      -mtime +"$RETENTION_DAYS" -print -delete 2>/dev/null || true)
    if [ -n "$deleted" ]; then
      log "deleted (older than ${RETENTION_DAYS} days):"
      printf '%s\n' "$deleted"
    else
      log "no files older than ${RETENTION_DAYS} days"
    fi
  fi
fi

log "done."
exit 0
