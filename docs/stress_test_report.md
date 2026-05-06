# Stress Test Report (133, MUST)

자동매매 안전성 invariant가 대량 트래픽 / 비정상 입력 / 누적 시나리오에서도 유지되는지 검증한다.
CLAUDE.md "**손실 방어와 감사 로그 우선**"의 코드 단 강제 검증.

## 실행 위치

- Backend: [`backend/tests/test_stress.py`](../backend/tests/test_stress.py) (`pytest tests/test_stress.py -v`)
- Frontend: [`frontend/src/components/tabs/Approvals.stress.test.jsx`](../frontend/src/components/tabs/Approvals.stress.test.jsx) (`npm test -- --run stress`)

## CI 모드 vs Manual full-run

- **CI 모드** (자동 회귀): `LARGE_N = 100` — 같은 invariant를 보장하면서 < 5s 완료. 매 PR / nightly 회귀에서 자동 실행.
- **Manual full-run**: 운영자가 1000건 검증을 원할 때는 `tests/test_stress.py`의 `LARGE_N` 상수를 1000으로 직접 수정 후 실행. SQLite + StaticPool 기준 ~5–10초 내 통과 확인.

## 검증 시나리오 — Backend

| # | 시나리오 | 검증 invariant | 결과 |
|---|---|---|---|
| 1 | approval N건 큐 생성 (`LIVE_MANUAL_APPROVAL`) | submit 모두 202 + `PendingApproval` row 모두 PENDING + GET `/api/approvals` 응답에 모두 포함 | ✅ N=100 통과 (< 30 ms / 건) |
| 2 | mock order N건 즉시 체결 (`SIMULATION` fast-path) | risk OK이면 즉시 broker FILL + audit `executed=True` / `broker_status=FILLED` | ✅ N=100 통과 (BUY/SELL 교대로 누적 한도 회피) |
| 3 | risk rejection 대량 — `max_order_notional` 초과 | 모두 400 REJECTED + audit `decision=REJECTED` / `executed=False` 기록 (CLAUDE.md "거부도 audit에 남는다") | ✅ N=100 통과 |
| 4 | emergency_stop ON에서 모든 모드 차단 (060) | `PendingApproval` 0건, audit는 모두 `decision=REJECTED` / `executed=False` | ✅ N=100 통과 |
| 5 | stale price 차단 | broker가 시세 못 가져오면 거부 | ⚠️ **MockBroker 미구현 — skip**. 운영 broker(KIS adapter)에 timestamp 기반 stale 검사 추가 후 활성화 예정 |
| 6 | duplicate approval 차단 | 첫 1건만 200, 이후 100건 모두 409 | ✅ 통과 |
| 7 | audit endpoint limit 캡 | default=50, limit=N → N, limit > N → 인서트된 N | ✅ 통과 |

## 검증 시나리오 — Frontend

| # | 시나리오 | 검증 invariant | 결과 |
|---|---|---|---|
| F1 | history 500건 렌더링 | 모든 row DOM 생성 + < 3s (jsdom 환경; 운영 브라우저는 sub-second) | ✅ 통과 |
| F2 | PENDING 200 + history 500 동시 렌더 | 700 row 동시 mount 안정 | ✅ 통과 |

## 미구현 invariant (TODO)

이 영역은 stress test 시 검증할 invariant이지만 현 구현에 해당 기능이 없어 skip된다. 향후 별도 PR에서 활성화.

### 1. Stale price detection
- **현재**: `MockBroker`는 unknown symbol에도 default 50000을 반환해 stale 판정 불가.
- **TODO**: `KisBrokerAdapter.get_market_price()`에 시세 timestamp 기반 stale 검사 (예: 마지막 시세가 N초 이상 지났으면 raise) 추가. RiskManager가 그 raise를 catch해 거부.

### 2. Duplicate order detection (idempotency)
- **현재**: 같은 client_order_id로 중복 제출하면 broker에 두 번 전달 (idempotency key 미구현).
- **TODO**: `OrderRequest`에 `idempotency_key` (UUID) 추가, `OrderAuditLog`에서 같은 key로 이미 executed=True인 row가 있으면 거부.

### 3. 큐 적체 임계 자동 알림
- **현재**: PENDING 큐가 1000+ 적체되어도 시스템은 계속 받음. dashboard의 stale ratio (111)는 history만 추적.
- **TODO**: queue depth가 임계 초과 시 RiskManager가 신규 제출 거부 또는 알림 emit.

## 운영자 운영 가이드

### 사고 분석 흐름
1. `/api/audit/orders?limit=200`에서 최근 audit 검토 (모든 거부도 기록됨)
2. 같은 시간대의 emergency_stop history 확인 (`/api/risk/emergency-stop/history`)
3. PENDING 큐 stale ratio (Approvals 탭 또는 Dashboard banner — 111/116 참조)
4. AI 호출 timeline (audit 탭 AI sub-tab — 094/108 참조)

### 회귀 시 첫 액션
1. `pytest tests/test_e2e_approval_order_flow.py -v` — 단일 주문 흐름 invariant
2. `pytest tests/test_stress.py -v` — 대량 시나리오 invariant
3. `npm test -- --run stress` — 대량 렌더 invariant
4. 모두 통과해야 자동매매 흐름 안전.

## 관련 문서
- [`docs/risk_policy.md`](risk_policy.md) — RiskManager 평가 순서 + 결정 매트릭스
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 LIVE 승격
- [`docs/strategies.md`](strategies.md) — 전략 contract 명세 (131)
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 / 다층 안전 가드
