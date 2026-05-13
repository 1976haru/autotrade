# Backup & Restore 정책 (체크리스트 #69)

## 1. 목적

장애 발생 시 운영 기록(OrderAuditLog / PendingApproval / AgentDecisionLog /
AuditEvent / VirtualOrder / FuturesOrderAuditLog / Watchlist / BacktestRun /
EmergencyStopEvent 등)을 복구 가능하도록 한다.

본 문서는 **DB만 백업**한다 — `.env` / API key / app secret / 계좌번호 /
Telegram token 등 민감정보는 *어떤 백업 파일에도 포함되지 않는다*.

## 2. 절대 원칙 (CLAUDE.md)

| 원칙 | 강제 위치 |
|---|---|
| `.env` / API key / Secret 백업 금지 | `scripts/backup_db.sh`는 *DB만* 다룬다. `cp .env`, `tar .env` 패턴 0건 (테스트로 lock) |
| DATABASE_URL을 로그에 그대로 노출 금지 | `redact_url()` helper가 `://user:***@host` 형태로 password 가림. log에 raw `$DATABASE_URL` echo 0건 (테스트로 lock) |
| DATABASE_URL이 Secret 같은 패턴 포함 시 즉시 중단 | `abort_if_secret_input()`이 `KIS_APP_KEY=` / `KIS_APP_SECRET=` / `TELEGRAM_BOT_TOKEN=` / `ANTHROPIC_API_KEY=` / `OPENAI_API_KEY=` 검출 시 exit 2 |
| 백업 파일은 git 추적 0건 | `.gitignore`에 `backups/*` + `*.sql.gz` + `*.db.backup` + `*.sqlite.bak` (테스트로 lock) |
| 백업 파일은 Docker 이미지에 포함 안 됨 | `backend/.dockerignore`에 `backups/` + 백업 파일 패턴 |
| 실 broker / 실 KIS API 호출 0건 | 본 스크립트는 DB만 다룬다 |
| LIVE flag 변경 0건 | env 변경 0건 |

## 3. 백업 대상 / 제외

### 백업 대상 (DB row 전체)
- OrderAuditLog — 모든 주문 결정 + 체결 + AI 메타
- PendingApproval — 결재 큐 + attempts
- AgentDecisionLog — 10-agent council 결정
- AuditEvent — 통합 감사 이벤트 (#68)
- VirtualOrder — 가상 주문 라이프사이클
- FuturesOrderAuditLog — 선물 주문 audit
- EmergencyStopEvent — 긴급 정지 이력
- Watchlist / WatchlistItem — 관심 종목
- ThemeSignal — 테마 신호
- BacktestRun — 백테스트 결과
- ShadowTrade / AiAnalysisLog / AgentMemory / MarketBar — 보조 데이터

### 백업 제외 (절대 백업 금지)
- `.env` / `.env.staging` / `.env.example` 등 환경 변수 파일
- `backend/.env` — KIS / Anthropic / Telegram Secret 포함
- 외부 토큰 / refresh token / Telegram bot token
- API Key / App Secret / 계좌번호
- TLS 인증서 / 개인 키 (`*.pem` / `*.key`)
- frontend 빌드 산출물 (`dist/`)

**Secret이 우연히 백업되는 사고를 막기 위해 `backup_db.sh`는 DATABASE_URL에
secret-looking 패턴이 들어 있으면 즉시 exit 2로 중단한다** (테스트로 lock).

## 4. SQLite 백업

기본 환경 (`backend/.env`의 `DATABASE_URL=sqlite:///./data/auto_trader.db`):

```bash
# 표준 실행
bash scripts/backup_db.sh

# 또는 환경 변수 명시
DATABASE_URL=sqlite:///./backend/data/auto_trader.db \
  bash scripts/backup_db.sh

# dry-run (실제 파일 생성 X)
BACKUP_DRY_RUN=true bash scripts/backup_db.sh

# 다른 출력 디렉터리
BACKUP_DIR=/var/backups/autotrade bash scripts/backup_db.sh

# 30일 retention
BACKUP_RETENTION_DAYS=30 bash scripts/backup_db.sh
```

동작:
1. `DATABASE_URL`을 redact한 form만 출력 (password 가림)
2. `sqlite3` 사용 가능하면 `.backup` 명령 (WAL/SHM 일관성 보장)
3. `sqlite3` 미설치면 파일 copy fallback
4. 출력: `backups/autotrade_backup_YYYYMMDD_HHMMSS.sqlite`
5. 크기 0 검증
6. `BACKUP_RETENTION_DAYS`보다 오래된 파일 자동 삭제

### Windows PowerShell

```powershell
$env:DATABASE_URL = "sqlite:///./backend/data/auto_trader.db"
pwsh -File scripts/backup_db.ps1

# dry-run
pwsh -File scripts/backup_db.ps1 -DryRun
```

git-bash가 있으면 `backup_db.sh`도 동작 (`.sh` 권장 — WSL / Linux 운영 환경과
동일).

## 5. PostgreSQL 백업

staging / 운영에서 Postgres 사용 시 (`docker-compose.staging.yml` 기준):

```bash
# DATABASE_URL을 staging 인스턴스로 — 운영자가 .env.staging 또는 shell env로
DATABASE_URL='postgresql://user:secret@localhost:15432/autotrader_staging' \
  bash scripts/backup_db.sh
```

요구사항:
- `pg_dump` 설치됨 (`postgresql-client` 패키지)
- gzip 사용 가능
- 출력: `backups/autotrade_backup_YYYYMMDD_HHMMSS.sql.gz`

password가 URL에 포함되어 있지만 *로그에는 redacted form만* 노출. `pg_dump`는
URL을 직접 받아 connection 처리.

## 6. 복구 절차

### 6.1 SQLite 복구

```bash
# 1) backend 서버 중지
#    (운영자가 ctrl-c 또는 systemctl stop)

# 2) 복구 실행 — 현재 DB는 자동으로 *.pre_restore_*.bak로 보호 백업됨
DATABASE_URL=sqlite:///./backend/data/auto_trader.db \
  bash scripts/restore_db.sh backups/autotrade_backup_20260601_030000.sqlite

# 또는 --yes로 prompt 건너뛰기 (스크립트 자동화 시에만)
DATABASE_URL=sqlite:///./backend/data/auto_trader.db \
  bash scripts/restore_db.sh --yes backups/autotrade_backup_20260601_030000.sqlite

# 3) Alembic migration 상태 확인
cd backend
alembic current

# 4) backend 재시작
uvicorn app.main:app --reload

# 5) 상태 확인
curl http://127.0.0.1:8000/api/status

# 6) 주요 테이블 row count
sqlite3 backend/data/auto_trader.db \
  "SELECT 'OrderAuditLog', COUNT(*) FROM order_audit_log UNION ALL
   SELECT 'PendingApproval', COUNT(*) FROM pending_approval UNION ALL
   SELECT 'AuditEvent', COUNT(*) FROM audit_event UNION ALL
   SELECT 'VirtualOrder', COUNT(*) FROM virtual_order;"
```

**복구 전 보호 백업 자동 생성**: `restore_db.sh`는 현재 DB를 덮어쓰기 전에
`<db_path>.pre_restore_<timestamp>.bak`로 자동 백업한다. 운영자가 실수로 잘못된
파일을 골라도 즉시 되돌릴 수 있다.

### 6.2 PostgreSQL 복구

```bash
# 1) backend 중지
docker compose -f docker-compose.staging.yml stop backend-staging

# 2) 새 DB 생성 (또는 기존 DB 그대로 — pg_dump plain 형식은 DROP/CREATE 포함)
#    psql -d postgres -c "DROP DATABASE autotrader_staging; CREATE DATABASE ..."
#    (운영자 정책에 따라)

# 3) 복구
DATABASE_URL='postgresql://user:secret@localhost:15432/autotrader_staging' \
  bash scripts/restore_db.sh backups/autotrade_backup_20260601_030000.sql.gz
#    → 자동 pre-restore pg_dump (backups/autotrade_pre_restore_*.sql)

# 4) Alembic
cd backend
alembic current

# 5) backend 재시작
docker compose -f docker-compose.staging.yml start backend-staging
```

## 7. 백업 보관 정책

| 정책 | 기본값 | 변경 |
|---|---|---|
| 일별 retention | 14일 | `BACKUP_RETENTION_DAYS=N` |
| 주별 / 월별 분리 | (후속) | — |
| 외부 저장소 sync | (후속) | — |
| 압축 | gzip (PG) | `BACKUP_COMPRESS=false`로 끄기 가능 |

본 PR은 *로컬 디스크 백업*만 지원. 외부 (S3 / rsync / object storage) sync는
후속.

## 8. 백업 검증

매 백업 후 다음을 운영자가 직접 확인:

1. **파일 존재 + 크기 > 0** — script가 자체 검증 (exit 4)
2. **`file <backup>`** — SQLite 3 / SQL ASCII text 확인
3. **smoke restore** — staging 환경에 복구 후 `/api/status` 200
4. **주요 테이블 row count** — 백업 전/후 일치
5. **Secret 미포함** — `grep -E "(PST|sk-|Bearer |password=)" <backup>` 0건
   (자동화는 후속)

## 9. 스케줄링 예시

### Linux cron (운영자 PC 또는 staging host)

```cron
# 매일 03:00 KST → 18:00 UTC = 03:00 KST. KST 직접 표기 권장 시 TZ= 사용.
0 3 * * * cd /path/to/autotrade && \
    DATABASE_URL=sqlite:///./backend/data/auto_trader.db \
    bash scripts/backup_db.sh >> backups/cron.log 2>&1
```

### Windows Task Scheduler (PowerShell)

```powershell
# Task Action:
# Program/script: pwsh.exe
# Arguments:     -NoProfile -File "C:\trade\autotrade\scripts\backup_db.ps1"
# Start in:      C:\trade\autotrade
# Environment 변수로 DATABASE_URL 설정 (또는 -DatabaseUrl 인자 전달)
```

### Docker compose (staging)

backend-staging 컨테이너가 떠 있는 상태에서 host에서 직접 실행:

```bash
DATABASE_URL='postgresql://autotrader:staging_only_placeholder@localhost:15432/autotrader_staging' \
  bash scripts/backup_db.sh
```

## 10. 운영 주의

1. **백업은 *성공*보다 *복구 검증*이 중요** — staging 환경에서 정기적으로
   restore smoke 실행 권장
2. **Secret 백업 0건** — `.env` / `.env.staging` / 토큰 / 계좌번호는 본
   스크립트로 절대 백업되지 않음 (테스트로 lock)
3. **실거래 전에는 매일 백업 + 매주 복구 smoke 필수** —
   `docs/promotion_policy.md` 8개 옵트인 조건 중 하나
4. **백업 파일 *자체*가 민감 데이터** — OrderAuditLog 등에 trading 정보가
   들어 있어 백업 파일도 신뢰 환경에만 보관. 외부 클라우드에 평문 업로드
   금지 — 암호화 후 보관 권장 (후속)

## 11. 후속 backlog

- 백업 파일 무결성 자동 검증 (`sqlite3 PRAGMA integrity_check` /
  `pg_restore --list`)
- 백업 파일 암호화 (age / GPG) — 외부 저장소 upload 전 필수
- 외부 저장소 sync (S3 / Backblaze / rsync to NAS)
- 주별 / 월별 retention 분리 (현재는 일별 retention만)
- 백업 결과 알림 (`docs/notification_policy.md` #64와 통합)
- 자동 restore smoke (staging에 매일 자동 복구 후 row count diff)
- Postgres `pg_dump -Fc` (custom format) 옵션 — pg_restore에서 더 빠른 부분 복구
- DB 마이그레이션 시점의 *pre-migration* 자동 백업

## 12. 절대 invariant (변경 금지)

1. `scripts/backup_db.sh` / `.ps1` / `restore_db.sh`는 `.env` / API key /
   Secret을 *복사하지 않는다* (소스 정적 검사로 lock).
2. DATABASE_URL을 로그에 그대로 echo 0건 — `redact_url` 경유만.
3. DATABASE_URL이 Secret-like 패턴 포함 시 즉시 exit 2.
4. 백업 파일은 git에 *0건* 추적됨 (`.gitignore` allowlist 패턴).
5. 백업 파일은 Docker 이미지에 *0건* 포함됨 (`.dockerignore`).
6. 본 스크립트는 broker / KIS / Anthropic / Telegram API 호출 0건.
7. 복구는 현재 DB를 덮어쓰기 전 *자동 보호 백업* 생성.
8. 복구는 운영자 확인 (`OVERWRITE` 또는 `--yes`) 필수.

## 13. 관련 PR / 체크리스트

- #17 Database schema audit
- #67 Staging environment (Postgres 도입)
- #68 Audit Event facade — audit_event 테이블도 백업 대상
- `docs/deployment_checklist.md` 12단계 — 실거래 전 백업 필수
- `docs/promotion_policy.md` — 8개 옵트인 조건 중 백업/복구 검증
- `docs/local_security_policy.md` — Secret hygiene
