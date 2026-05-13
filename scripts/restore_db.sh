#!/usr/bin/env bash
#
# 체크리스트 #69: DB 복구 스크립트 (SQLite + PostgreSQL).
#
# 절대 원칙 (CLAUDE.md):
#   1. 본 스크립트는 *DB만* 복구한다 — .env / API key / 계좌번호 복구 0건.
#   2. DATABASE_URL을 로그에 redacted form만 출력.
#   3. 운영자 확인 없이 *현재 DB를 덮어쓰지 않는다* — 명시 동의 또는 --yes 필요.
#   4. 현재 DB를 덮어쓰기 전에 *자동 보호 백업* (autotrade_pre_restore_*) 생성.
#   5. 실 broker 호출 0건.
#
# 사용:
#   bash scripts/restore_db.sh backups/autotrade_backup_20260601_030000.sqlite
#   bash scripts/restore_db.sh --yes backups/autotrade_backup_20260601_030000.sql.gz
#   RESTORE_DRY_RUN=true bash scripts/restore_db.sh backups/foo.sqlite

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATABASE_URL="${DATABASE_URL:-}"
DRY_RUN="${RESTORE_DRY_RUN:-false}"

CONFIRM_FLAG=false
BACKUP_FILE=""

# ---- arg parse ----
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) CONFIRM_FLAG=true; shift ;;
    -h|--help)
      cat <<EOF
사용: $0 [--yes] <backup_file>
환경: DATABASE_URL 필수.
설명: SQLite는 파일 교체, PostgreSQL은 psql restore.
    현재 DB는 덮어쓰기 전 *자동 보호 백업*에 보관됩니다.
EOF
      exit 0
      ;;
    --) shift; BACKUP_FILE="${1:-}"; break ;;
    -*)
      echo "unknown option: $1" >&2
      exit 2
      ;;
    *)
      if [ -z "$BACKUP_FILE" ]; then
        BACKUP_FILE="$1"
      else
        echo "extra argument: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

log() { printf '[restore_db] %s\n' "$*"; }
err() { printf '[restore_db] ERROR: %s\n' "$*" >&2; }
redact_url() {
  local url="$1"
  printf '%s' "$url" | sed -E 's|(://[^:/@]+):[^@]+@|\1:***@|'
}

if [ -z "$DATABASE_URL" ]; then
  err "DATABASE_URL is required."
  exit 1
fi
if [ -z "$BACKUP_FILE" ]; then
  err "backup file path required."
  err "usage: $0 [--yes] <backup_file>"
  exit 1
fi
if [ ! -f "$BACKUP_FILE" ]; then
  err "backup file not found: $BACKUP_FILE"
  exit 3
fi

REDACTED="$(redact_url "$DATABASE_URL")"
log "target url: $REDACTED"
log "backup:     $BACKUP_FILE"
log "dry run:    $DRY_RUN"

# ---- confirm ----
if [ "$CONFIRM_FLAG" != "true" ] && [ "$DRY_RUN" != "true" ]; then
  cat <<EOF

⚠ 현재 DATABASE_URL의 DB를 덮어씁니다.

  대상 DB: $REDACTED
  복구 원: $BACKUP_FILE

  계속하려면 'OVERWRITE'를 입력하세요 (대소문자 구분):
EOF
  read -r REPLY
  if [ "$REPLY" != "OVERWRITE" ]; then
    log "aborted by operator"
    exit 0
  fi
fi

PRE_TS="$(date -u +%Y%m%d_%H%M%S)"

# ---- SQLite 분기 ----

restore_sqlite() {
  local raw="${DATABASE_URL#sqlite:///}"
  if [ "$raw" = "$DATABASE_URL" ]; then
    raw="${DATABASE_URL#sqlite://}"
  fi
  local db_path="$raw"
  case "$db_path" in
    /*) : ;;
    *)
      if [ -f "$PROJECT_ROOT/backend/$db_path" ]; then
        db_path="$PROJECT_ROOT/backend/$db_path"
      elif [ -f "$PROJECT_ROOT/$db_path" ]; then
        db_path="$PROJECT_ROOT/$db_path"
      fi
      ;;
  esac

  # 보호 백업
  if [ -f "$db_path" ]; then
    local protect="${db_path}.pre_restore_${PRE_TS}.bak"
    log "saving current DB to protective copy: $protect"
    if [ "$DRY_RUN" = "true" ]; then
      log "DRY_RUN: would copy $db_path -> $protect"
    else
      cp "$db_path" "$protect"
    fi
  else
    log "current DB not found at $db_path — proceeding with fresh restore"
  fi

  log "restoring from: $BACKUP_FILE"
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would copy $BACKUP_FILE -> $db_path"
    return 0
  fi
  cp "$BACKUP_FILE" "$db_path"
  log "restore ok"
}

# ---- PostgreSQL 분기 ----

restore_postgres() {
  if ! command -v psql >/dev/null 2>&1; then
    err "psql not installed."
    err "Install postgresql-client and re-run."
    exit 5
  fi

  # 보호 백업 — pg_dump
  local protect_file="$PROJECT_ROOT/backups/autotrade_pre_restore_${PRE_TS}.sql"
  mkdir -p "$PROJECT_ROOT/backups"
  log "saving current DB to protective dump: $protect_file"
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would pg_dump current state"
  else
    if command -v pg_dump >/dev/null 2>&1; then
      pg_dump --no-owner --no-privileges --format=plain \
              --file="$protect_file" "$DATABASE_URL" || {
        err "pre-restore pg_dump failed — aborting to protect operator data"
        exit 6
      }
    else
      err "pg_dump missing — cannot create protective backup, aborting"
      exit 5
    fi
  fi

  log "restoring from: $BACKUP_FILE"
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would psql restore"
    return 0
  fi

  # gz면 decompress 후 stream
  case "$BACKUP_FILE" in
    *.gz)
      if ! command -v gunzip >/dev/null 2>&1; then
        err "gunzip required for .gz backup"
        exit 5
      fi
      gunzip -c "$BACKUP_FILE" | psql "$DATABASE_URL"
      ;;
    *)
      psql "$DATABASE_URL" < "$BACKUP_FILE"
      ;;
  esac
  log "restore ok"
}

case "$DATABASE_URL" in
  sqlite://*)
    restore_sqlite
    ;;
  postgresql://*|postgresql+psycopg2://*|postgresql+psycopg://*|postgres://*)
    restore_postgres
    ;;
  *)
    err "unsupported DATABASE_URL scheme"
    exit 7
    ;;
esac

log "done."
log "post-restore checks (수동):"
log "  1. cd backend && alembic current"
log "  2. uvicorn app.main:app --reload"
log "  3. curl http://127.0.0.1:8000/api/status"
log "  4. 주요 row count 확인 (OrderAuditLog / PendingApproval / AuditEvent 등)"
exit 0
