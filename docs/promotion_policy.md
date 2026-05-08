# Promotion Policy

전략은 아래 단계를 차례로 통과해야만 다음 단계로 승격한다. 각 단계는 코드 단의 안전 가드(운용모드 + 환경 플래그 + RiskManager 분기)와 1:1로 매핑된다.

## 단계 매트릭스

| # | 단계 | 코드 모드 | 주문 | 자금 | 구현 상태 | 운영자 가이드 |
|---|---|---|---|---|---|---|
| 0 | Draft | — | 불가 | — | — | 별도 설계 문서 |
| 1 | Backtest | 비동기 (`BacktestEngine`) | 불가 | 가상 | ✓ 구현 (`/api/backtest/run`) | — |
| 2 | Shadow | `LIVE_SHADOW` | 불가 (Risk가 모든 주문 REJECTED) | 없음 | ✓ 구현 (read-only 4종) | [`shadow_mode.md`](shadow_mode.md) |
| 3 | Paper | `PAPER` | 가능 (KIS 모의투자) | 가상 | ✓ 구현 (다층 is_paper 가드) | [`paper_mode.md`](paper_mode.md) |
| 3v | Virtual AI | `VIRTUAL_AI_EXECUTION` (152) | AI 자동 (가상) | 가상 | ✓ 구현 — 라이브 broker 미사용 | [`ai_virtual_execution_report.md`](ai_virtual_execution_report.md) |
| 3f | Virtual Futures | (선물 `MockFuturesBroker` 직접) | 가상 (long/short, leverage, 강제청산) | 가상 | ✓ 구현 — 라이브 broker 미사용 (151) | [`futures_simulation_report.md`](futures_simulation_report.md) |
| 4 | Live Manual | `LIVE_MANUAL_APPROVAL` | 사용자 승인시만 | **실 자금** | ⏳ 절반 — 큐 흐름 ✓, KIS 실주문 라우팅 ✗ | [`live_activation_blockers.md`](live_activation_blockers.md) |
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
  - 수수료·세금·슬리피지 반영 — **`config={execution_model: next_open|conservative, slippage_bps≥5, commission_bps≥5, tax_bps=23}` 명시 필수**. `same_close` 단독 결과는 승인 근거로 사용 금지. 자세한 정책: [`backtest_policy.md`](backtest_policy.md).
  - Profit Factor ≥ 1.2.
  - **(137)** Strategy Scoreboard (`/api/strategies/scoreboard`)에서 해당 strategy의 누적 metrics 검토 — `runs ≥ 100`, `total_pnl > 0`, `win_rate ≥ 0.45`.
  - **(131)** Strategy contract metadata(entry / exit / invalidation / required_regime / risk_profile)가 모두 작성됨 — `base.py` default가 그대로 노출되는 strategy는 승격 불가. `docs/strategies.md` 검토.
  - **승률만으로 승인 금지 (#24)** — `expectancy > 0`, `profit_factor ≥ 1.2`, `max_consecutive_losses ≤ 5`, MDD가 운영 자본의 15% 이내. 시간대별 손익(`hourly_pnl`)에서 손실이 특정 시간대에 집중되면 별도 검토. 자세한 metric 정의: [`backtest_metrics.md`](backtest_metrics.md).
  - **Walk-forward PASS 필요 (#25)** — `POST /api/backtest/walk-forward` 추천이 `PASS`. 한 fold가 전체 양수 수익의 70% 초과 차지 (single_best_fold_pnl_share)하면 '한 번의 대박' 의심으로 승격 보류. holdout 구간 PnL 양수 필수. 자세한 정책: [`walk_forward_policy.md`](walk_forward_policy.md).

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
  - **(135)** `current_regime` 분포 검토 — 운영 시간대의 regime이 strategy `required_regime`과 매칭되는 비율 측정. 매칭 비율이 50% 미만이면 strategy 자체를 재검토하거나 `required_regime`을 보정.
  - **(136)** Signal Quality 분포 — strength/confidence 점수의 평균이 운영 정책에 부합 (예: confidence 평균 ≥ 60).

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
  - **(132)** E2E 테스트 (`backend/tests/test_e2e_approval_order_flow.py`) 모두 PASS — 단일 주문 진입점이 PAPER 모드에서도 invariant 유지.
  - **(133)** Stress 테스트 모든 시나리오 PASS (skip 제외) — 대량/비정상 입력 invariant 검증.
  - **(140)** Idempotency: 같은 `client_order_id`로 두 번째 주문이 들어와도 audit row가 정확히 1건 — frontend가 client_order_id를 매 주문마다 발급하는지 확인.
  - **(138, 139)** PAPER 단계 audit row 표본 검사 — `strategy`, `signal_strength`, `signal_confidence`가 `LiveStrategyEngine` 발신 주문에서 모두 채워지는지.

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
- **추가 invariant** (134~140 도메인이 LIVE 단계에서 강제됨):
  - **(134)** 모든 LIVE 주문은 `trade_reason`을 명시 — `strategy_signal` / `manual` / `stop_loss` / `take_profit` / `signal_invalidation` 중 하나. NULL trade_reason 주문은 사후 분석에서 '왜 들어갔나'를 답할 수 없으므로 LIVE 단계에서는 anti-pattern.
  - **(138)** 모든 strategy-driven LIVE 주문은 `OrderAuditLog.strategy`가 채워짐 — Strategy Scoreboard의 LIVE 통합(137-followup)에 누락 없이 합산되도록.
  - **(139)** Strategy-driven 주문은 `signal_strength` + `signal_confidence`가 영구화 — 사후 분석에서 quality와 PnL 상관관계 추적.
  - **(140)** Frontend는 매 주문마다 unique `client_order_id` (UUID v4 권장) 발급 — onClick double-fire / 네트워크 재시도로 인한 중복 체결 차단.
  - **(135)** Strategy의 `required_regime`과 운영 시간대 `current_regime` 매칭이 50% 이상 — advisory이지만 LIVE 단계에서는 운영자가 `regime_matches_strategy=False`인 신호에 추가 주의.
- **승격 기준**:
  - 1~2개월 소액 운영.
  - 모든 주문이 인간 승인을 거침.
  - 거부/취소 시나리오 모두 검증.
  - 위 invariant 모두 audit log 표본 검사로 통과.

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
  - **(123)** AI 호출 audit (`AiAnalysisLog.mode`)에 `LIVE_AI_ASSIST`로 분류된 호출의 평균 비용/모델 분포 — `docs/strategies.md` Signal Quality 섹션 참조.
  - **(139)** AI 추천이 만든 LIVE 주문은 `signal_strength` / `signal_confidence`에 AI confluence score(004)를 매핑해서 영구화 — 사후 분석에서 AI 신호 강도와 PnL 상관관계 추적.

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

## Audit row invariant 매트릭스 (134~140)

각 단계에서 `OrderAuditLog`에 어떤 컬럼이 *반드시* 채워져야 하는지. NULL이 허용되더라도 LIVE 단계에서는 사후 분석/감사 가능성을 위해 채우는 것이 원칙. 수동 운영자 주문은 frontend UI가 사유를 받아 채워야 한다.

| 컬럼 | Backtest | Shadow | Paper | Live Manual | AI Assist | AI Exec |
|---|---|---|---|---|---|---|
| `mode` (000) | (BacktestRun) | 자동 | 자동 | 자동 | 자동 | 자동 |
| `decision` / `reasons` (000) | (BacktestRun) | 자동 | 자동 | 자동 | 자동 | 자동 |
| `trade_reason` (134) | — | 권장 | 권장 | **필수** | **필수** | **필수** |
| `strategy` (138) | (BacktestRun) | strategy 발신 시 채움 | strategy 발신 시 채움 | **strategy 발신 시 필수** | **AI 발신 시 필수** | **필수** |
| `signal_strength` / `signal_confidence` (139) | — | strategy 발신 시 채움 | strategy 발신 시 채움 | **strategy 발신 시 필수** | **AI quality score 매핑** | **필수** |
| `client_order_id` (140) | — | — | 권장 | **필수 (idempotency)** | **필수** | **필수** |

표의 "필수"는 LIVE 단계에서 운영자가 사후 분석할 때 누락되면 안 된다는 의미. 코드 단에서는 NULL을 허용하지만 운영 절차로 강제한다 (frontend가 trade_reason / client_order_id 입력을 받기 전까지는 주문 button 비활성).

## 환경 플래그 한눈에

| 변수 | Backtest | Shadow | Paper | Live Manual | AI Assist | AI Exec |
|---|---|---|---|---|---|---|
| `DEFAULT_MODE` | (any) | `LIVE_SHADOW` | `PAPER` | `LIVE_MANUAL_APPROVAL` | `LIVE_AI_ASSIST` | `LIVE_AI_EXECUTION` |
| `ENABLE_LIVE_TRADING` | false | false | false | **true** | **true** | **true** |
| `ENABLE_AI_EXECUTION` | false | false | false | false | false | **true** |
| `KIS_IS_PAPER` | — | true* | **true** | **false** | **false** | **false** |
| `KIS_APP_KEY/SECRET` | — | 필요 | 필요 | 필요 | 필요 | 필요 |
| `ENABLE_FILL_POLLING` | false | false | true (권장) | true (권장) | true (권장) | true (권장) |
| `STALE_PRICE_MAX_AGE_SECONDS` | (검사 안 함) | 60 | 60 | 60 (권장 — 더 짧게도 가능) | 60 | 30 (보수적 권장) |
| `ANTHROPIC_API_KEY` | — | — | — | — | 필요 | 필요 |

*Shadow는 read-only라 모의/실전 환경 모두 가능하지만 운영 안전상 모의 권장.

## 변경 이력

이 문서는 코드와 동기화되어야 한다. 운용모드/플래그/안전 가드를 변경하는 PR은 본 문서도 같이 업데이트할 것.

- **141 (2026-05-06)** 134~140 도메인 반영:
  - Backtest 단계에 (137) Strategy Scoreboard / (131) contract metadata 기준 추가.
  - Shadow에 (135) regime 매칭 / (136) signal quality 분포 검토 추가.
  - Paper에 (132) E2E / (133) Stress / (140) idempotency / (138, 139) audit 표본 검사 추가.
  - Live Manual 섹션에 134~140 invariant (`trade_reason`, `strategy`, `signal_strength/confidence`, `client_order_id`, `regime` advisory) 명시.
  - AI Assist에 (123) AiAnalysisLog.mode / (139) AI confluence → signal_strength/confidence 매핑 추가.
  - 새 섹션 "Audit row invariant 매트릭스 (134~140)" — 단계별로 어떤 audit 컬럼이 채워져야 하는지 매트릭스화.
