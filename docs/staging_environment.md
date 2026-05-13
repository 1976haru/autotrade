# Staging 환경 (체크리스트 #67)

## 1. 목적

운영 서버와 *완전히 별개*의 staging 환경을 만들어 실험 코드가 실거래
서버에 바로 배포되지 않도록 한다. staging에서는 **신규 기능 smoke 테스트
+ 모의 / Paper / Shadow 검증**만 가능하며 **LIVE 모드는 기본 비활성**이다.

본 환경은 *PR 머지 후 → main 자동 build / deploy → 실거래*로 직결되지
않도록 하는 가장 단순한 안전 격리 단계다.

## 2. 절대 원칙 (CLAUDE.md)

| 원칙 | 강제 위치 |
|---|---|
| 실 broker live order 호출 0건 | `ENABLE_LIVE_TRADING=false`를 `docker-compose.staging.yml`에 *하드코딩*. backend Dockerfile에도 default false. |
| `ENABLE_LIVE_TRADING=true` 설정 금지 (staging) | compose에서 string "false"로 명시. .env.staging.example에도 안내. |
| `ENABLE_AI_EXECUTION=true` 설정 금지 (staging) | 동일하게 compose에 "false" 하드코딩 |
| `ENABLE_FUTURES_LIVE_TRADING=true` 설정 금지 (staging) | 동일 |
| 실 API key / Secret / 계좌번호를 compose에 *직접 적지 않음* | `.env.staging`(gitignore)에서만 주입. `.env.staging.example`은 빈 placeholder |
| `.env.staging`은 git에 커밋하지 않음 | `.gitignore`에 `.env.*` + `!.env.staging.example` 처리됨 |
| frontend에 Secret 저장 0건 | `VITE_*` build args는 *공개 가능 값*만 허용. Token / 계좌번호는 build args에 없음. |
| 운영 배포 자동화는 본 PR 범위 밖 | docs / Dockerfile / compose / smoke script만. CI/CD 통합은 후속 PR |

## 3. 서비스 구성

`docker-compose.staging.yml`이 정의:

| 서비스 | 컨테이너 | host 포트 | 역할 |
|---|---|---|---|
| `backend-staging` | `autotrader-staging-backend` | **18000** → 8000 | FastAPI uvicorn (LIVE flag 강제 false) |
| `frontend-staging` | `autotrader-staging-frontend` | **15173** → 5173 | vite preview (SPA) |
| `postgres-staging` | `autotrader-staging-postgres` | **15432** → 5432 | Postgres 16 (운영 sqlite → PG 미리 검증) |
| `redis-staging` | `autotrader-staging-redis` | **16379** → 6379 | rate-limit / dedupe 후속용 인프라 자리 |

**포트 ‑ 운영 default(8000 / 5173)와 의도적으로 다른 1xxxx 대역**:
- backend `:18000`
- frontend `:15173`
- postgres `:15432`
- redis `:16379`

→ 운영 컨테이너와 같은 호스트에서 동시 실행 가능. 운영자가 "지금 보는 게
staging인지 production인지" 포트로 즉시 식별.

## 4. 실행 가이드

### 4.1 사전 요구

- Docker Desktop / Docker Engine (`docker compose` v2+)
- 호스트 포트 `18000 / 15173 / 15432 / 16379` 사용 가능

### 4.2 환경 변수 준비

```bash
cd C:/trade/autotrade   # 또는 프로젝트 root
cp .env.staging.example .env.staging
# .env.staging만 편집. 실제 KIS / Anthropic / Telegram 키는 입력 *금지*.
# 필요 시 STAGING_PG_PASSWORD만 강한 값으로 변경.
```

### 4.3 빌드 + 기동

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging up --build -d
docker compose -f docker-compose.staging.yml --env-file .env.staging ps
```

### 4.4 smoke test

```bash
python scripts/check_staging_smoke.py
# 외부 staging 서버를 검사하려면:
python scripts/check_staging_smoke.py --backend http://staging.host:18000 --frontend http://staging.host:15173
```

smoke script가 검증하는 항목:
1. `GET /api/status` 200 + 응답 JSON 파싱
2. `enable_live_trading` / `enable_ai_execution` / `enable_futures_live_trading`
   모두 *False* (staging invariant)
3. `default_mode`가 `SIMULATION` 또는 `PAPER` (LIVE_* 금지)
4. `GET /docs` 200 reachable
5. `/api/status` 응답에 token / chat_id / app_secret 문자열 미포함
6. frontend `GET /` 200 + SPA root div 존재

### 4.5 종료

```bash
docker compose -f docker-compose.staging.yml down
# 데이터까지 삭제:
docker compose -f docker-compose.staging.yml down --volumes
```

## 5. Mode / Safety 매트릭스

| 환경 변수 | staging default | 변경 가능? | 비고 |
|---|---|---|---|
| `APP_ENV` | `staging` | ✓ | log / metric 분기용 |
| `DEFAULT_MODE` | `SIMULATION` | ✓ `PAPER`까지 | LIVE_* 금지 |
| `ENABLE_LIVE_TRADING` | `false` | ✗ | staging 정책 |
| `ENABLE_AI_EXECUTION` | `false` | ✗ | staging 정책 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | ✗ | staging 정책 |
| `KIS_IS_PAPER` | `true` | ✗ | KIS 모의투자만 |
| `NOTIFICATIONS_ENABLED` | `false` | ⚠ | 후속에서 검토 |
| `MARKET_DATA_PROVIDER` | `mock` | ✓ | mock → yfinance 전환 가능 |

## 6. 검증 시나리오

staging에서 머지 전에 확인해야 할 흐름:

1. **봇 / Agent**: `/api/auto-trader/run-once` mock 데이터로 BUY/SELL/HOLD 분기 정상
2. **Daily report agent**: `/api/agents/daily-report/preview` markdown 정상
3. **Risk guards**: notional / position / loss limit / freshness가 staging에
   서도 동일하게 REJECTED
4. **Approval queue**: `LIVE_MANUAL_APPROVAL` 모드로 변경 후 결재 흐름 정상
   (broker는 mock이라 실 주문 0건)
5. **Paper mode**: `DEFAULT_MODE=PAPER` + `KIS_IS_PAPER=true`로 변경 후 mock
   KIS 응답으로 paper 흐름 smoke
6. **Risk Auditor / Strategy Researcher agents**: read-only 분석 API 정상
7. **Notification dry_run**: `/api/notifications/test`가 noop_channel skip 반환

## 7. 운영과 staging의 차이

| 항목 | 운영(미정) | staging |
|---|---|---|
| 목적 | 실거래 (옵트인 시) | smoke test / mock 검증만 |
| LIVE flag | 운영자 명시 옵트인 시에만 true | 항상 false |
| broker | KIS LIVE (별도 PR) | MockBroker / KIS Paper |
| DB | Postgres (운영 인스턴스) | Postgres (격리 인스턴스, reset 가능) |
| Secret | 운영용 vault (별도) | 빈 placeholder default — 실 키 금지 |
| 자동 배포 | (별도 PR) | 본 PR 범위 밖 |
| 성능 / SLA | 운영 수준 | 검증 수준 (worker 1개, 단일 redis) |
| 외부 노출 | (정책 미정 — 절대 원칙은 사설/Tailscale) | 로컬 호스트 또는 사설망. 공개 인터넷 노출 금지 |

## 8. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| 포트 충돌 (`18000`/`15173`) | 운영/dev 서버가 같은 포트 사용 중. `lsof -i :18000` / `netstat -ano | findstr 18000`으로 점유 프로세스 확인 후 종료 또는 compose 포트 변경 |
| backend 컨테이너 healthcheck 실패 | DB 마이그레이션 실패 / `DATABASE_URL` 오타. `docker compose -f docker-compose.staging.yml logs backend-staging` 확인 |
| frontend가 backend에 도달 못 함 | 빌드 시점 `VITE_BACKEND_URL`이 `http://localhost:18000`로 inline됨 — 외부 staging 호스트에서 띄우려면 build args의 `STAGING_FRONTEND_BACKEND_URL` 변경 |
| CORS 차단 | backend `CORS_ORIGINS` 환경변수에 frontend host 추가 |
| Redis 연결 실패 | redis-staging healthcheck 통과 후 backend 기동 — compose `depends_on` 조건이 처리하지만 첫 부팅 시 늘어질 수 있음 |
| migration 실패 | lifespan에서 `alembic upgrade head`가 자동 실행. 실패 시 backend 컨테이너 재기동 또는 PG 볼륨 reset (`down --volumes`) |
| `.env.staging`이 git에 보임 | `.gitignore` 확인. `.env.*` + `!.env.staging.example` allowlist 외에는 추적 X |

## 9. 실거래 전 체크 (staging만으로는 충분하지 않음)

staging PASS는 *최소 진입 조건*이다. 실거래 활성화 전에는 다음이 추가로
필요하다 (cross-ref [`docs/deployment_checklist.md`](deployment_checklist.md)
+ [`docs/live_activation_blockers.md`](live_activation_blockers.md)):

1. **Shadow Mode** (`LIVE_SHADOW`) 운영 ≥ 1주 — 실 시세 read-only + would-have
   기록 검증
2. **Paper Mode** (`PAPER` + KIS 모의투자) 운영 ≥ 4주 — 실 broker 모의 환경
   체결 품질 reconciliation
3. **8개 promotion gate** ([`docs/promotion_policy.md`](promotion_policy.md))
   모두 PASS
4. **운영자 명시 옵트인 PR** — `ENABLE_LIVE_TRADING=true` 활성화는 별도 PR로만

## 10. 후속 backlog

| 항목 | 후속 |
|---|---|
| CI/CD 자동 배포 (PR 머지 → staging build → 자동 smoke) | 별도 워크플로 PR. 본 PR은 *수동 실행* 가이드만 |
| nginx / Caddy reverse proxy + TLS | staging 외부 노출이 필요해질 때 |
| 운영용 docker-compose (별도 LIVE flag opt-in) | 본 PR 범위 밖 |
| Tailscale / Wireguard 사설망 접속 가이드 | [`docs/local_security_policy.md`](local_security_policy.md) 연계 |
| Postgres migration tooling (alembic auto on container start) | 본 PR은 lifespan에 `apply_migrations()` 의존. 운영은 별도 검토 |
| 로그 집계 (Loki / OpenTelemetry) | 본 PR은 stdout만 |
| 컨테이너 image registry push | 본 PR은 *로컬 build* 전제 — registry 통합은 후속 |

## 11. 절대 invariant (변경 금지)

1. `docker-compose.staging.yml`의 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION`
   / `ENABLE_FUTURES_LIVE_TRADING`은 `"false"` 문자열로 *하드코딩*. 운영자가
   override 시도해도 staging 환경에서는 false 유지.
2. `.env.staging`은 *git 추적 0*. 본 파일에 실 API 키 / Secret을 입력해도
   커밋 가능성이 없다 (`.gitignore` allowlist 패턴).
3. `.env.staging.example`은 *빈 placeholder만*. 실 키 입력 시 PR 거부.
4. staging 포트는 운영 default와 *반드시 다르다* (1xxxx 대역) — 운영자가
   포트로 환경을 즉시 구분.
5. backend Dockerfile은 `.env*`를 `.dockerignore`로 격리 — 이미지에 Secret이
   구워지지 *않는다*.
6. smoke script는 staging 컨테이너만 호출. 실 broker / 외부 API 호출 0건.

## 12. 관련 PR / 체크리스트

- #67 Staging Environment (본 PR)
- #63 PWA 설치 — staging UI는 PWA 설치 가능
- #64 Notifications — staging에서 NOTIFICATIONS_ENABLED=false default
- #66 Integration Tests — staging 전 머지 게이트
- `docs/deployment_checklist.md` 12단계 — staging은 그 중 7~8단계
- `docs/promotion_policy.md` — LIVE 승격 8개 조건
- `docs/live_activation_blockers.md` — LIVE 활성 전 blocker 체크리스트
