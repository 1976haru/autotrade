# Promotion Policy

전략은 아래 단계를 차례로 통과해야만 다음 단계로 승격한다. 각 단계는 코드 단의 안전 가드(운용모드 + 환경 플래그 + RiskManager 분기)와 1:1로 매핑된다.

## 단계 매트릭스

| # | 단계 | 코드 모드 | 주문 | 자금 | 구현 상태 | 운영자 가이드 |
|---|---|---|---|---|---|---|
| 0 | Draft | — | 불가 | — | — | 별도 설계 문서 |
| 1 | Backtest | 비동기 (`BacktestEngine`) | 불가 | 가상 | ✓ 구현 (`/api/backtest/run`) | — |
| 2 | Shadow | `LIVE_SHADOW` | 불가 (Risk가 모든 주문 REJECTED) | 없음 | ✓ 구현 (read-only 4종) | [`shadow_mode.md`](shadow_mode.md) |
| 3 | Paper | `PAPER` | 가능 (KIS 모의투자) | 가상 | ✓ 구현 (다층 is_paper 가드) | [`paper_mode.md`](paper_mode.md) |
| 4 | Live Manual | `LIVE_MANUAL_APPROVAL` | 사용자 승인시만 | **실 자금** | ⏳ 절반 — 큐 흐름 ✓, KIS 실주문 라우팅 ✗ | TODO |
| 5 | AI Assist | `LIVE_AI_ASSIST` | 사용자 승인시만 (AI 제안) | **실 자금** | ⏳ 라우트 placeholder, 실 호출 미구현 | TODO |
| 6 | AI Execution | `LIVE_AI_EXECUTION` | AI 자동 (한도 내) | **실 자금** | 🛑 미구현, 기본 비활성화 (CLAUDE.md) | TODO |

## 단계별 안전 계약

### 1. Backtest

- **목표**: 과거 데이터로 전략 검증.
- **가드**:
  - `BacktestEngine`은 broker를 호출하지 않는다.
  - `data_source` 필드로 합성/실 데이터 출처를 audit에 기록.
- **승격 기준**:
  - 거래 100회 이상.
  - 기대값(평균 손익) 양수.
  - 수수료·세금·슬리피지 반영.
  - Profit Factor ≥ 1.2.

### 2. Shadow (`LIVE_SHADOW`)

- **목표**: 실 시세·잔고로 신호 동작 검증.
- **가드**:
  - `RiskManager.evaluate_order`가 `LIVE_SHADOW` 모드 모든 주문을 REJECTED로 반환.
  - `KisBrokerAdapter.place_order`는 `is_paper=False`이면 `NotImplementedError` (defense in depth).
  - `route_order` 단일 진입점으로 HTTP/엔진 모두 동일 가드 통과.
- **환경**: `DEFAULT_MODE=LIVE_SHADOW`, `KIS_IS_PAPER=true` (또는 false — 어차피 주문은 차단).
- **승격 기준**:
  - 4주 이상 무중단 운영.
  - 시세/잔고 폴링 누락 0건.
  - 리스크 평가 결과의 reason 분포가 의도와 일치.

### 3. Paper (`PAPER`)

- **목표**: KIS 모의 계좌로 주문 흐름 (RECEIVED → 체결) 검증.
- **가드** (3중):
  1. `get_broker()`가 `DEFAULT_MODE=PAPER` + `KIS_IS_PAPER=false`면 시작 거부.
  2. `KisClient.place_order`의 tr_id가 `is_paper`로 분리 (`VTTC*` paper, `TTTC*` live).
  3. `KisBrokerAdapter.place_order`가 `is_paper=False`이면 `NotImplementedError`.
- **환경**: `DEFAULT_MODE=PAPER`, `KIS_IS_PAPER=true` (필수), `ENABLE_LIVE_TRADING=false` (안전).
- **승격 기준**:
  - 4주 이상.
  - RiskPolicy 한도 위반 0회.
  - 부분 체결/거부 응답에 audit 정합성 유지.
  - `ENABLE_FILL_POLLING=true`로 체결 자동 갱신 검증.

### 4. Live Manual (`LIVE_MANUAL_APPROVAL`)

- **목표**: 실계좌 + 사용자 승인 큐.
- **현재 구현 상태**:
  - ✓ `RiskManager`가 `NEEDS_APPROVAL` 반환.
  - ✓ `PermissionGate`가 `PendingApproval` 행 생성.
  - ✓ `/api/approvals` GET/approve/reject + 프론트 승인 탭.
  - ✗ broker가 여전히 `MockBroker` (실 KIS 라우팅 미연결).
  - ✗ `KisBrokerAdapter.place_order(is_paper=False)`는 stub.
  - ✗ `cancel_order` stub.
- **다음 PR에서 추가될 것**:
  - `get_broker()` 분기 확장: `LIVE_MANUAL_APPROVAL` + `ENABLE_LIVE_TRADING=true` → KIS (`is_paper=False`).
  - `KisBrokerAdapter.place_order` 라이브 분기 활성.
  - `KisBrokerAdapter.cancel_order` 구현 (`order-rvsecncl`).
  - 운영자 가이드 (`live_manual_mode.md`).
- **환경**: `DEFAULT_MODE=LIVE_MANUAL_APPROVAL`, `ENABLE_LIVE_TRADING=true`, `KIS_IS_PAPER=false`, `ENABLE_FILL_POLLING=true`.
- **승격 기준**:
  - 1~2개월 소액 운영.
  - 모든 주문이 인간 승인을 거침.
  - 거부/취소 시나리오 모두 검증.

### 5. AI Assist (`LIVE_AI_ASSIST`)

- **목표**: AI가 후보 제시, 인간이 승인.
- **현재 구현 상태**:
  - ✓ `/api/ai/analyze` 실 Anthropic 호출 + audit (read-only 분석).
  - ✓ `RiskManager`가 `NEEDS_APPROVAL`로 처리.
  - ✗ AI 제안 → 자동 OrderRequest 변환 흐름 미구현.
  - ✗ 프론트엔드 AI 신호 → 승인 큐 자동 push 미구현.
- **다음 PR에서 추가될 것**:
  - AI 응답의 `signal`/`entry`/`target`/`stop`을 OrderRequest로 변환하는 어댑터.
  - 변환 결과를 `route_order(requested_by_ai=True)`로 전달.
  - AI 추천 정확도 audit 분석.
- **승격 기준**:
  - AI 추천 정확도 보고.
  - 거절 사유 분포 분석.
  - 인간 승인율 측정.

### 6. AI Execution (`LIVE_AI_EXECUTION`)

- **목표**: 제한된 조건 하 AI 자동 실행.
- **현재 구현 상태**:
  - ✓ `RiskManager`가 `enable_ai_execution=True`일 때만 통과시키는 가드 보유.
  - 🛑 기본 `ENABLE_AI_EXECUTION=false`. 절대 자동 활성화 금지 (CLAUDE.md).
- **활성화 조건** (필수 모두 충족):
  - `LIVE_MANUAL_APPROVAL` 단계에서 1개월 이상 무사고.
  - `LIVE_AI_ASSIST` 단계에서 AI 추천 정확도 검증 완료.
  - 한도(`max_order_notional`, `max_daily_loss`, `max_positions`) 매우 보수적으로 설정.
  - 별도 운영자 옵트인 + 모니터링 대시보드.

## 금지 기준

- 승률만으로 승격 금지 (Profit Factor / 기대값 동반 검토).
- 특정 하루의 고수익만으로 승격 금지.
- 수수료·세금·슬리피지 미반영 백테스트로 승격 금지.
- 로그가 남지 않는 주문 기능 금지 — 모든 주문은 `OrderAuditLog`에 기록되어야 한다.
- AI 실행 단계 도달 전까지 `ENABLE_AI_EXECUTION=true` 금지.
- 검증 안 된 단계 건너뛰기 금지 (예: Backtest → Live Manual 직행 불가).

## 환경 플래그 한눈에

| 변수 | Backtest | Shadow | Paper | Live Manual | AI Assist | AI Exec |
|---|---|---|---|---|---|---|
| `DEFAULT_MODE` | (any) | `LIVE_SHADOW` | `PAPER` | `LIVE_MANUAL_APPROVAL` | `LIVE_AI_ASSIST` | `LIVE_AI_EXECUTION` |
| `ENABLE_LIVE_TRADING` | false | false | false | **true** | **true** | **true** |
| `ENABLE_AI_EXECUTION` | false | false | false | false | false | **true** |
| `KIS_IS_PAPER` | — | true* | **true** | **false** | **false** | **false** |
| `KIS_APP_KEY/SECRET` | — | 필요 | 필요 | 필요 | 필요 | 필요 |
| `ENABLE_FILL_POLLING` | false | false | true (권장) | true (권장) | true (권장) | true (권장) |
| `ANTHROPIC_API_KEY` | — | — | — | — | 필요 | 필요 |

*Shadow는 read-only라 모의/실전 환경 모두 가능하지만 운영 안전상 모의 권장.

## 변경 이력

이 문서는 코드와 동기화되어야 한다. 운용모드/플래그/안전 가드를 변경하는 PR은 본 문서도 같이 업데이트할 것.
