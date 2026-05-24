# EXE Preflight Smoke Test (#63 / 8-01)

EXE 빌드 전후 기본 작동 여부를 **한 번에** 확인하는 read-only smoke test.
health / config 안전 flag / KIS credentials / DB / auto loop / Agent Council /
Decision Episode / build·version / update status 를 점검해 **PASS / WARN / FAIL**
로 분류한다. **본 검사는 주문을 발생시키지 않는다** (read-only GET 전용).

## 구성

- **CLI**: `scripts/exe_smoke_test.py` — `GET /api/system/preflight`(+`/health`)
  를 urllib 로 호출, 응답을 client 측에서 한 번 더 secret 스캔 후 리포트 출력.
- **Backend endpoint**: `GET /api/system/preflight` (`app/system/preflight.py`)
  — 내부 read-only 소스(settings / db_is_ready / KIS readiness / auto loop /
  Agent Council registry / Decision Episode count / build_info)를 모아 평가.
- **UI**: Settings 탭 `PreflightSmokeCard` — 요약(PASS/WARN/FAIL count) + 항목별
  상태 + 주요 FAIL.

## 점검 항목

backend_health · backend_api_reachable(CLI) · sidecar_status · db_status ·
safety_flags · default_mode · is_live_authorization · kis_credentials ·
kis_paper_readiness · kis_paper_auto_env · broker_order_type · auto_loop ·
agent_council · decision_episode_api · no_trade_reason · build_info ·
update_status · secret_scan(CLI).

## 판정 기준

**FAIL (치명)**: backend 도달 불가 / DB FAIL / `ENABLE_LIVE_TRADING=true` /
`ENABLE_AI_EXECUTION=true` / `ENABLE_FUTURES_LIVE_TRADING=true` /
`KIS_IS_PAPER=false` / `is_live_authorization=true` / secret·계좌 원문 탐지 /
`broker_order_type` 가 paper-safe(KIS_PAPER/MOCK) 아님 / readiness 가 안전
위반으로 BLOCKED.

**WARN (주의, 운영 가능)**: KIS 자격 미구성(Mock 가능) / MARKET_CLOSED /
NO_MARKET_DATA / build_info 일부 unknown / update status 확인 불가 / 최근
Decision Episode 없음 / loop 장 시간 외 상태.

**PASS**: 안전 flag 정상 · readiness READY · secret 노출 없음 ·
status/build-info 정상.

> 장이 닫힌 날 `MARKET_CLOSED` 는 **FAIL 이 아니라 WARN** (운영상 정상).

## exit code (CLI)

| code | 의미 |
|---|---|
| 0 | PASS 또는 WARN (운영 가능) |
| 1 | FAIL 항목 있음 (안전 위반 / DB 실패 / secret 노출 등) |
| 2 | backend 도달 불가 (sidecar 미기동) |

## EXE 실행 후 확인 절차

1. EXE 를 실행하고 backend sidecar 가 뜰 때까지 기다린다.
2. CLI 로 점검:
   ```
   python scripts/exe_smoke_test.py --base-url http://127.0.0.1:8000
   python scripts/exe_smoke_test.py --json reports/preflight.json
   ```
   또는 Settings 탭의 "EXE Preflight Smoke Test" 카드 확인.
3. RESULT 가 **PASS** 또는 **WARN** 이면 기본 작동 정상.
4. **FAIL** 이면 해당 항목 메시지 확인:
   - `safety_flags` FAIL → `backend/.env` 의 ENABLE_* / KIS_IS_PAPER 점검.
   - `db_status` FAIL → DB 마이그레이션 / 파일 권한 점검.
   - `secret_scan` FAIL → 응답에 자격정보가 섞임(비정상) — 즉시 점검.
5. `kis_credentials` WARN → `backend/.env` 에 KIS 자격 입력(없어도 Mock 실행 가능).
6. 장이 닫힌 날 `auto_loop` / no-trade 가 `MARKET_CLOSED` / `NO_MARKET_DATA`
   WARN 인 것은 정상.
7. 화면·출력에 API key / Secret / 계좌번호 원문이 **없는지** 확인.

## 안전 invariant (테스트로 lock)

- read-only GET 만 호출 — 주문 / POST / broker / OrderExecutor / route_order
  호출 0건.
- DB read-only SELECT 만 (write 0건).
- Secret / API key / 계좌번호 *원문* 출력 0건 (boolean / count / 이름만,
  탐지 시 마스킹 + FAIL).
- `is_live_authorization=false` / `contains_secret=false` 불변.
- UI: 매수/매도/실거래/Place Order 버튼·입력 form 0개.

## 관련 파일

- `scripts/exe_smoke_test.py` (CLI)
- `backend/app/system/preflight.py` (평가 로직)
- `backend/app/api/routes_system.py` (`GET /api/system/preflight`)
- `backend/tests/test_exe_smoke_script.py`
- `frontend/src/components/common/PreflightSmokeCard.jsx` (+`.test.jsx`)
- `frontend/src/components/tabs/Settings.jsx` (mount),
  `frontend/src/services/backend/client.js`
