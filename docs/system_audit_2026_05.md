# 자동매매 시스템 감사 보고서 — 2026-05

> **목적**: 새로운 매매기법을 추가하지 않고, *현재 코드에 실제 존재* 하는
> 자동매매 / 전략 / 백테스트 / 모의투자 / 위험관리 / AI 판단 기능을
> 체계적으로 정리한 단일 진실 (single source of truth).
>
> **원칙**: 본 문서의 모든 항목은 실제 코드 / 설정 / 테스트에서 검증된 사실
> 만 포함한다. 추측 / 의견 / 개선 제안 0건.

## 0. 핵심 결론

- **매매기법 6종 — 새로 추가 0건**: 기존 코드의 6개 전략만 존재. 가짜 /
  경쟁사 전략명 0건 (정적 grep 가드로 lock).
- **LIVE 거래 비활성**: 6개 전략 모두 `live_trading_available = False`.
  KIS adapter 의 `place_order(is_paper=False)` 는 `NotImplementedError`.
- **다층 안전 가드**: RiskManager + OrderGuard + PositionLimitRule +
  EmergencyStopRule + CorrelationGuard + LossLimitRule.
- **단일 주문 진입점**: 모든 주문은 `route_order` → RiskManager →
  PermissionGate → OrderExecutor 흐름.
- **감사 추적**: 모든 주문/판단/이벤트가 `OrderAuditLog` / `ShadowTrade` /
  `AgentDecisionLog` / `EmergencyStopEvent` 에 영구 기록.

---

## 1. 매매기법 6종 (코드에서 실제 확인)

`backend/app/strategies/concrete/__init__.py::STRATEGY_REGISTRY` 가 단일
진실 — 6개 키와 클래스 매핑 외 *어떤 전략도 없다*.

### 1.1 sma_crossover

| 항목 | 값 |
|---|---|
| 파일 | `backend/app/strategies/concrete/sma_crossover.py` |
| 클래스 | `SmaCrossoverStrategy` |
| **내부 ID** | `sma_crossover` |
| **초보자명** (#81) | **단기/장기 이동평균 교차** |
| 위험도 | MEDIUM |
| 권장 모드 | PAPER_RECOMMENDED |
| 진입 | 단기 SMA 가 장기 SMA 를 *상향 돌파* 한 봉 종가 |
| 청산 | 단기 SMA 가 장기 SMA 를 *하향 돌파* 한 봉 종가 |
| Invalidation | regime 차단 / stale data |
| Required Regime | trending |
| Risk Profile | `position_size_pct=5, stop_loss_pct=2, max_concurrent=1` |
| 핵심 파라미터 | `short=5, long=20` |
| 백테스트 | ✅ |
| 모의투자 | ✅ |
| 실거래 | ❌ (LIVE 미구현) |

### 1.2 rsi_reversion

| 항목 | 값 |
|---|---|
| 파일 | `backend/app/strategies/concrete/rsi_reversion.py` |
| 클래스 | `RsiReversionStrategy` |
| **내부 ID** | `rsi_reversion` |
| **초보자명** (#81) | **RSI 과매도/과매수 회복** |
| 위험도 | MEDIUM |
| 권장 모드 | PAPER_RECOMMENDED |
| 진입 | RSI 가 *과매도 임계 아래* 에서 회복하는 첫 봉 |
| 청산 | RSI 가 *과매수 임계 위* 에서 임계 아래로 하락 |
| Invalidation | regime 차단 / stale data |
| Required Regime | ranging |
| Risk Profile | `position_size_pct=3, stop_loss_pct=2, max_concurrent=2` |
| 핵심 파라미터 | `period=14, oversold=30, overbought=70` |
| 백테스트 | ✅ |
| 모의투자 | ✅ |
| 실거래 | ❌ |

### 1.3 vwap_strategy

| 항목 | 값 |
|---|---|
| 파일 | `backend/app/strategies/concrete/vwap_strategy.py` |
| 클래스 | `VWAPStrategy` |
| **내부 ID** | `vwap_strategy` |
| **초보자명** (#81) | **VWAP 평균 회귀** |
| 위험도 | MEDIUM |
| 권장 모드 | PAPER_RECOMMENDED |
| 진입 | VWAP 아래에서 위로 reclaim + 거래량 증가 |
| 청산 | VWAP 하향 이탈 / 시간 청산 (typical_hold ≈ 20분) |
| Invalidation | open cooldown 미통과 / vwap_deviation_cap_pct 초과 |
| Required Regime | (regime 필터 별도) |
| Risk Profile | `position_size_pct=5, stop_loss_pct=2` |
| 핵심 파라미터 | `open_cooldown_bars, vwap_deviation_cap_pct, min_volume_share, typical_hold_minutes=20` |
| 백테스트 | ✅ |
| 모의투자 | ✅ |
| 실거래 | ❌ |

### 1.4 orb_vwap

| 항목 | 값 |
|---|---|
| 파일 | `backend/app/strategies/concrete/orb_vwap.py` |
| 클래스 | `OrbVwapStrategy` |
| **내부 ID** | `orb_vwap` |
| **초보자명** (#81) | **ORB + VWAP 돌파** |
| 위험도 | HIGH |
| 권장 모드 | PAPER_RECOMMENDED |
| 진입 | ORB 상단 *돌파* + 세션 VWAP 위에서 마감하는 첫 봉 |
| 청산 | VWAP 하향 이탈 / ORB 하단 재진입 |
| Invalidation | 일중 1회 진입 제한 / opening cooldown |
| Required Regime | trending_up |
| Risk Profile | `position_size_pct=5, stop_loss_pct=1.5, max_concurrent=2` |
| 핵심 파라미터 | `orb_bars=6` |
| 백테스트 | ✅ |
| 모의투자 | ✅ |
| 실거래 | ❌ |

### 1.5 volume_breakout

| 항목 | 값 |
|---|---|
| 파일 | `backend/app/strategies/concrete/volume_breakout.py` |
| 클래스 | `VolumeBreakoutStrategy` |
| **내부 ID** | `volume_breakout` |
| **초보자명** (#81) | **거래량 급증 돌파** |
| 위험도 | HIGH |
| 권장 모드 | PAPER_RECOMMENDED |
| 진입 | 거래대금 ≥ N×lookback평균 + 최근 고점 *돌파* + VWAP 상단 |
| 청산 | 거래량 급감 / stop_loss / take_profit / trailing / time_exit |
| Invalidation | stale data, blocked regime, 과도한 runup, VWAP 격차 |
| Required Regime | trending_up (block: trending_down, high_vol) |
| Risk Profile | `position_size_pct=4, stop_loss_pct=2, take_profit_pct=4, trailing_pct=1.5, time_exit_bars=30` |
| 핵심 파라미터 | `min_bars_required=25, volume_multiplier=2.0, breakout_lookback_bars=20, max_vwap_distance_pct=3.0, max_intraday_runup_pct=8.0` |
| 백테스트 | ✅ |
| 모의투자 | ✅ |
| 실거래 | ❌ |

### 1.6 pullback_rebreak

| 항목 | 값 |
|---|---|
| 파일 | `backend/app/strategies/concrete/pullback_rebreak.py` |
| 클래스 | `PullbackRebreakStrategy` |
| **내부 ID** | `pullback_rebreak` |
| **초보자명** (#81) | **눌림목 재돌파** |
| 위험도 | HIGH |
| 권장 모드 | PAPER_RECOMMENDED |
| 진입 | 상승 임펄스 → 거래량 눌림 → 직전 고점 *재돌파* |
| 청산 | 거래량 급감 / 재돌파 실패 / stop_loss / time_exit |
| Invalidation | impulse 부족 / pullback 깊이 부족 |
| Required Regime | (regime 필터 별도) |
| Risk Profile | `position_size_pct=5, stop_loss_pct=2` |
| 핵심 파라미터 | `impulse_lookback, impulse_min_bars, pullback_fade_threshold, rebreak_lookback` |
| 백테스트 | ✅ |
| 모의투자 | ✅ |
| 실거래 | ❌ |

---

## 2. 전략 Registry 메타데이터 (#81 — 기존 구현)

`backend/app/strategies/registry_metadata.py` 가 6개 전략 위의 *얇은 메타*
레이어를 정의:

| 필드 | 의미 |
|---|---|
| `strategy_id` | 내부 ID (`sma_crossover` 등) |
| `display_name` | 한글 표시명 (UI 노출) |
| `beginner_name` | 한 줄 초보자용 설명 |
| `description` | 상세 설명 |
| `risk_level` | LOW / MEDIUM / HIGH |
| `recommended_mode` | PAPER_RECOMMENDED / LIVE_AFTER_VALIDATION / LIVE_CAUTION |
| `supported_modes` | `["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"]` (모두 동일) |
| `backtest_available` | True (6개 전략 모두) |
| `paper_trading_available` | True (6개 전략 모두) |
| `live_trading_available` | **False 영구** (KIS live 미구현) |
| `notes` | 운영자 주의사항 |

> 본 메타는 *기존 전략을 감싸는* 레이어다. **매매 로직 0줄 변경**. UI 표시명만
> 초보자 친화적으로 바꾸며, internal ID 는 `data-internal-id` 속성과 본문에
> *항상 함께* 노출 (운영자 / 로그 / audit 매핑 보존).

API: `GET /api/strategies/beginner-registry`
정적 grep 가드 (`backend/tests/test_strategy_registry_metadata.py`):
- 가짜 hype 단어 0건 (`골든브릿지`, `100% 승률`, `guaranteed`, `magic strategy`, …)
- STRATEGY_REGISTRY ↔ _BEGINNER_METADATA 1:1 매핑
- broker / OrderExecutor / route_order import 0건
- DB write 0건

---

## 3. 운영 모드 (`backend/app/core/modes.py::OperationMode`)

| 모드 | 정의 | 실주문 발생 | 본 베타 단계 가용 |
|---|---|---|---|
| `SIMULATION` | 모의 데이터 + MockBroker | ❌ | ✅ default |
| `PAPER` | 실 시세 + KIS 모의투자 (가상 자금) | ❌ (가상) | ✅ |
| `LIVE_SHADOW` | 실 계좌 / 시세 read-only, 주문 금지 | ❌ (REJECTED + ShadowTrade 기록) | ✅ |
| `LIVE_MANUAL_APPROVAL` | 사용자 승인 후 실거래 | ✅ (KIS LIVE place_order 활성화 필요) | ⏳ 후속 |
| `LIVE_AI_ASSIST` | AI 권고 + 사용자 승인 | ✅ | ⏳ 후속 |
| `LIVE_AI_EXECUTION` | AI 자동 실행 | ✅ | 🛑 영구 비활성 (#75) |
| `VIRTUAL_AI_EXECUTION` | 가상 환경 + AI 자동 체결 | ❌ | ✅ |

**기본값**: `DEFAULT_MODE=SIMULATION` (`backend/.env.example`).
**모드 전환은 backend `.env` 편집 + 재시작 필요** — frontend 에서 임의로 변경
불가.

---

## 4. 위험관리 (RiskManager + Rules / Guards)

### 4.1 단일 평가 진입점

`backend/app/risk/risk_manager.py::RiskManager.check_order(order, context)`
— 모든 호출자의 표준 메서드. `evaluate_order` 는 backwards compat alias.

### 4.2 다층 가드 체인 (호출 순서)

| 가드 | 파일 | 책임 |
|---|---|---|
| **OrderGuard** | `app/risk/order_guard.py::OrderGuard` (#38) | 중복 주문 / 쿨타임 / 미체결 같은 방향 차단 (RiskManager *전* `route_order` 단계) |
| **EmergencyStopRule** | `app/risk/emergency_stop.py` (#37) | 3-level kill switch — LEVEL_1/2/3 별 차단 |
| **PositionLimitRule** | `app/risk/position_limits.py::PositionLimitRule` (#35) | 1회 주문 / 종목별 / 총 노출 / 보유 종목 수 |
| **DailyLossLimitRule** | `app/risk/loss_limits.py::DailyLossLimitRule` | 일일 realized PnL 임계 |
| **WeeklyLossLimitRule** | `app/risk/loss_limits.py::WeeklyLossLimitRule` | 주간 realized PnL |
| **ConsecutiveLossRule** | `app/risk/loss_limits.py::ConsecutiveLossRule` | 연속 손실 거래 임계 |
| **CorrelationGuardRule** | `app/risk/correlation_guard.py::CorrelationGuardRule` (#78) | sector / theme 익스포저 — 신규 BUY 집중도만 제한 (SELL/EXIT은 통과) |
| **StalePriceRule** | RiskManager step 1.5 (`STALE_PRICE_MAX_AGE_SECONDS=60`) | 시세 timestamp 가 N초 초과 oldness 면 hard-reject |

### 4.3 차단 사유 logging

모든 거부 / 경고 / 승인은 `OrderAuditLog.reasons` (list) 에 *문자열로* 기록.
사용자 친화 메시지는 frontend 가 `friendlyErrorMessage()` 로 변환.

---

## 5. 주문 실행 구조

### 5.1 단일 진입점

`backend/app/execution/order_router.py::route_order` — 모든 주문 경로
(HTTP `/api/broker/orders`, `LiveStrategyEngine.submit_tick`,
`PermissionGate.approve`) 가 통과한다.

```
caller → route_order → RiskManager.check_order → audit row
                                ↓
                  REJECTED / NEEDS_APPROVAL / APPROVED
                                ↓
              ┌────────────────┼─────────────────┐
              ↓                ↓                 ↓
        400 reject      PermissionGate     OrderExecutor.execute
                          submit              → broker.place_order
                          (queue)             (단일 진입점, #40)
```

### 5.2 Broker Adapters (`backend/app/brokers/`)

| 클래스 | 파일 | 상태 |
|---|---|---|
| `BrokerAdapter` (ABC) | `base.py` | place_order / get_price / get_balance / get_positions / get_order_status / cancel_order |
| `MockBroker` | `mock_broker.py` | 테스트 / SIMULATION 용 in-memory |
| `KisBrokerAdapter` | `kis.py` | `place_order(is_paper=True)` 만 구현. `is_paper=False` 는 `NotImplementedError` (LIVE 활성화는 별도 옵트인 PR) |
| `FuturesBrokerAdapter` (ABC) | `futures_base.py` | 선물 전용 — 주식 BrokerAdapter 상속 *안 함* (#47) |

### 5.3 VirtualBroker / Paper

본 프로젝트는 사용자 요청서의 `VirtualBrokerAdapter` 라는 별도 클래스 *대신*
`MockBroker` + `KIS_IS_PAPER=true` 조합으로 paper 모드를 구현:
- SIMULATION → `MockBroker` 직접 사용
- PAPER → `KisBrokerAdapter` + `KIS_IS_PAPER=true` (모의투자 API)
- `app/execution/paper_trader.py::PaperTrader` 가 `assert_paper_broker()` 로
  검증 — `is_live_broker(broker)` 이면 즉시 `NotPaperBrokerError` (#42).

### 5.4 OrderExecutor — broker.place_order 의 *유일한* 호출자 (#40)

`app/execution/order_executor.py::OrderExecutor.execute` 만이
`broker.place_order()` 를 호출. 16개 API 라우트 + 12개 전략/필터/agent/explainability/risk/permission
모듈에 `broker.place_order(` 호출 0건 (정적 grep 테스트로 강제).

---

## 6. 자산 / 계좌 데이터

backend 가 broker → `get_balance` / `get_positions` 를 호출해 frontend 에 carry.
**Paper / Live 계좌는 broker 인스턴스 분리 + factory 가드로 분리**:
- `app/brokers/factory.py::get_broker()` — PAPER 모드 + `KIS_IS_PAPER=false`
  이면 *시작 거부* (CLAUDE.md "다층 안전 가드" 표).

자산현황 카드 컴포넌트 (frontend): Dashboard 의 `StatusSummaryCard`,
`AssetSummaryCard`, `PositionsCard` 등이 backend `/api/broker/balance` /
`/api/broker/positions` 를 read-only로 호출.

---

## 7. AI 판단 설명 / DecisionLog

### 7.1 기존 모델: `AgentDecisionLog` (`backend/app/db/models.py`)

| 컬럼 | 의미 | 사용자 요청서 DecisionLog 매핑 |
|---|---|---|
| `id` | PK | — |
| `created_at` | UTC 시각 | `timestamp` |
| `agent_name` | Agent 식별자 | `strategyId` (전략 + agent 모두 가능) |
| `symbol` | 종목 | `symbol` |
| `mode` | 운영 모드 | (mode carry) |
| `decision` | BUY/SELL/HOLD/APPROVE/REJECT/WARN/INFO | `decision` |
| `confidence` | 0-100 | `confidence` |
| `reasons` | list of str | `reasons` (왜 매수/매도/관망/차단/강도 저하) |
| `meta` | dict | `risks`, `userFriendlySummary` carry |
| `chain_id` | 결정 체인 추적 | `relatedOrderId` 대용 (audit row 연결) |

> 사용자 요청서의 `DecisionLog` 가 요구하는 모든 필드를 `AgentDecisionLog` 의
> 기존 컬럼 + `meta` dict carry 로 *이미 표현 가능*. 신규 모델 추가 없음.

### 7.2 OrderAuditLog 와 연결

`OrderAuditLog.ai_decision_meta` (JSON dict) 가 AI 의사결정 메타를 carry —
`AgentDecisionLog.chain_id` 와 cross-link 가능.

### 7.3 본 시스템의 Agent 15종

| Agent | 파일 | 역할 (#51 분류) |
|---|---|---|
| `MarketObserverAgent` | `market_observer.py` (#52) | OBSERVER — 시장 환경 snapshot |
| `NewsTrendAgent` | `news_trend_agent.py` (#53) | OBSERVER — 뉴스/테마 후보 필터 |
| `RiskAuditorAgent` | `risk_auditor.py` (#54) | RISK_AUDITOR — 장중 안전 감독 |
| `StrategyResearcherAgent` | `strategy_researcher.py` (#55) | STRATEGY_RESEARCHER — 전략 개선 제안 |
| `ExecutionRecommenderAgent` | `execution_recommender.py` (#56) | EXECUTION_RECOMMENDER — 매수/매도 제안 (직접 주문 X) |
| `DailyReportAgent` | `daily_report_agent.py` (#57) | REPORT_WRITER — 일일 리포트 markdown |
| `StrategySelectionAgent` | `strategy_selection_agent.py` (#85) | STRATEGY_RESEARCHER — 4개 단타 전략 조합 선택 |
| `AgentMemory` | `agent_memory.py` | 검색 가능한 학습 저장소 (주문 신호 X) |
| `MarketRegime` classifier | `market_regime.py` | OBSERVER — regime 분류 |
| `SignalQuality` evaluator | `signal_quality.py` | ANALYST — 신호 품질 점수 |
| `OperatingLoop` | `operating_loop.py` | 운영 루프 stage 관리 |

모든 Agent: `AgentOutput.is_order_intent = False` / `can_execute_order = False`
불변 — Agent 가 broker 를 직접 호출하지 않는다 (#51 base 가드).

---

## 8. 백테스트 (`backend/app/backtest/`)

| 파일 | 역할 |
|---|---|
| `engine.py::BacktestEngine` | 백테스트 실행 — bar 단위 strategy 호출 |
| `metrics.py` | 수익률 / 샤프 / MDD / 승률 / R:R 등 |
| `types.py` | `Bar`, `Signal`, `BacktestResult` 등 |
| `loaders.py` | CSV / yfinance 로더 |
| `walk_forward_runner.py` | walk-forward 분석 (#25) |
| `monte_carlo.py` | Monte Carlo 시뮬레이션 (#26) |
| DB 모델 `BacktestRun` | 실행 결과 영구화 |

**6개 전략 모두 backtest 가능** (`registry_metadata.backtest_available()` 가
모두 True 반환). `BacktestEngine.run()` 결과:
- `total_pnl`, `final_cash`, `win_count`, `loss_count`, `max_drawdown`,
  `trades_json`, `equity_curve`

**mock 사용 시 표시**: backend `MARKET_DATA_PROVIDER=mock` (default) — yfinance
provider 로 교체 가능. mock 결과는 운영자가 mock임을 인지하도록 carry.

---

## 9. 매매 기록 / Logs

| 모델 | 무엇을 기록 |
|---|---|
| **OrderAuditLog** | 모든 주문 (APPROVED / REJECTED / NEEDS_APPROVAL / REDUCED / BLOCKED) + reasons + broker_order_id / filled_quantity 등 |
| **ShadowTrade** | LIVE_SHADOW 모드의 *would-have* 추정 기록 — `actual_broker_order_sent` 항상 False invariant |
| **AgentDecisionLog** | Agent 의사결정 + chain_id |
| **EmergencyStopEvent** | 긴급정지 토글 (enabled, decided_by, level, reason_code) |
| **PendingApproval** | 결재 대기 큐 (status, attempts, decided_at) |
| **BacktestRun** | 백테스트 실행 결과 |
| **AuditEvent** | 일반 시스템 이벤트 (mode 변경 등) |
| **LossReasonLog** (#79) | 손실 *추정* 원인 태그 — review 가능, 확정 원인 아님 |

조회 API: `GET /api/audit/orders`, `/api/audit/agent-decisions`,
`/api/audit/events`, `/api/shadow/trades`, `/api/risk/emergency-stop/history`,
`/api/approvals`, `/api/backtest/runs`, `/api/analytics/loss-tags/recent`.

---

## 10. 알림 (`backend/app/notifications/`)

| 채널 | 파일 | 기본 동작 |
|---|---|---|
| `NoOpChannel` | `channels.py` | 비활성 (default — `NOTIFICATIONS_ENABLED=false`) |
| `TelegramChannel` | `channels.py` | Telegram 알림 — stdlib `urllib` 만 사용 (외부 의존성 0개), timeout 5초, retry 1회 |

설정:
- `NOTIFICATIONS_ENABLED` (default false)
- `NOTIFICATIONS_MIN_SEVERITY` (default INFO)
- `NOTIFICATIONS_DEDUPE_WINDOW_SECONDS` (default 60)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

token 은 **backend `.env` 만** — git / docs / frontend 0건.

---

## 11. 데이터 저장 / Storage Abstraction

| 측면 | 현재 구현 |
|---|---|
| **DB** | SQLAlchemy 2.0, `sqlite:///./data/auto_trader.db` (default), PostgreSQL 권장 (운영) |
| **Migrations** | Alembic 22개 (`backend/alembic/versions/0001..0022`) |
| **Session** | `app/db/session.py::SessionLocal` + `get_db()` FastAPI dependency |
| **Abstraction** | 모델은 모두 `app/db/models.py` 단일 모듈 — SQLAlchemy ORM. SQLite ↔ PostgreSQL 전환은 `DATABASE_URL` 만 변경 |
| **Frontend storage** | `localStorage` — channel preference / lastSeenVersion / lastChecked 만. **Secret 0건** (CLAUDE.md 절대 원칙 4) |

---

## 12. Frontend UI 표시명 변경 내역 (#81 ~ #83)

운영자 가독성 + 로그 매핑 보존을 위해 모든 UI 위치에서
*`displayName + (internal_id)` 함께* 노출.

### 적용된 UI 위치 (10곳)

| 컴포넌트 | 위치 |
|---|---|
| `OrderAuditRow` strategy 배지 | `AuditLog.jsx` |
| `BacktestStrategyMiniTable` 셀 | `AuditLog.jsx` |
| `BacktestExtremesSummary` best/worst | `AuditLog.jsx` |
| `ScoreboardCard` 누적 성과 행 | `LiveEngine.jsx` |
| `StatusCard` "전략" 필드 | `LiveEngine.jsx` |
| `AgentStatsCard` per-strategy 행 | `AgentStatsCard.jsx` |
| `_OrderSummary` AI hero 줄 | `Approvals.jsx` |
| `ApprovalProposalSummary` chip | `ApprovalQueue.jsx` |
| `ApproveConfirmSummary` 줄 | `ApprovalQueue.jsx` |
| `_MemoryRow` / `_MemoryDetail` | `AgentMemoryCard.jsx` |
| `_ProposalRow` 전략 필드 | `ExecutionRecommenderCard.jsx` |
| `StrategySelectionCard` candidates / blocked | `StrategySelectionCard.jsx` (#85) |

공통 helper: `frontend/src/utils/strategyNames.js`
- `formatStrategyName(id, lookup)` → `"displayName (internal_id)"`
- `strategyDisplayShort(id, lookup)` → `"displayName"` only
- `useStrategyDisplayNames()` hook — module-level 캐시, graceful fallback

각 row 에 `data-internal-id="<strategy_id>"` HTML 속성 carry → 테스트 selector /
audit 자동화에서 internal id 로 매핑 가능.

---

## 13. 안전 Flag (현재 default)

| 환경변수 | 기본 | 효과 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | RiskManager 분기, broker 라우팅 |
| `ENABLE_LIVE_TRADING` | `false` | LIVE_* 모드에서 실거래 차단 |
| `ENABLE_AI_EXECUTION` | `false` | LIVE_AI_EXECUTION 에서 AI 자동 실행 차단 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | 선물 모듈 거래 차단 (영구) |
| `KIS_IS_PAPER` | `true` | KisClient host + tr_id, place_order 가드 |
| `MARKET_DATA_PROVIDER` | `mock` | 시장 데이터 소스 |
| `ENABLE_FILL_POLLING` | `false` | 백그라운드 체결 갱신 |
| `STALE_PRICE_MAX_AGE_SECONDS` | `60` | RiskManager stale 임계 |

본 감사 시점: **모든 위험 flag default false** — LIVE 거래 / AI 자동 실행 /
선물 실거래 0건.

---

## 14. 가짜 / 경쟁사 전략명 제거 — 적용 내역

**현재 코드에서 제거된 전략명 목록**: *없음*. 본 프로젝트는 처음부터 가짜
전략명을 도입한 적이 *없다* — `test_strategy_registry_metadata.py` 가
다음 banned substrings 를 정적 grep 으로 차단:

```
"골든브릿지", "트라이앵글 전설", "다이아 전략", "퀀텀 점프", "황금알",
"초신성", "월급쟁이 비밀", "100% 승률",
"guaranteed", "magic strategy", "secret formula", "100% win"
```

이 invariant 는 본 감사 PR 의 새 테스트 `test_system_audit_invariants.py`
에서도 재검증된다.

---

## 15. 사용자 요청서 #15 invariant — 충족 여부

| 요구사항 | 현재 충족 위치 |
|---|---|
| 존재하지 않는 전략명이 UI에 표시되지 않는다 | `test_strategy_registry_metadata.py` (정적 grep, 6개 전략 외 0건) + `test_system_audit_invariants.py` |
| paper 모드에서는 실제 주문이 나가지 않는다 | `KisBrokerAdapter.place_order(is_paper=False)` `NotImplementedError`; `PaperTrader.assert_paper_broker()`; `test_paper_trader.py` |
| API 키 없으면 LIVE 모드 전환 차단 | `app/brokers/factory.py::get_broker()` 시작 거부 + RiskManager + `ENABLE_LIVE_TRADING=false` 가드 |
| 긴급정지 상태에서 신규 주문 차단 | `EmergencyStopRule` (#37); `test_emergency_stop_kill_switch.py` |
| 전략 enable/disable 정상 동작 | `LiveStrategyEngine` (`backend/app/strategies/live_engine.py`); 관련 테스트 |
| 기존 전략의 설정값 보존 | `STRATEGY_REGISTRY` + `describe_strategy()` 가 `__init__` 시그니처 그대로 노출 |
| AI 판단 로그가 전략 판단과 연결됨 | `AgentDecisionLog.symbol` + `chain_id`; `OrderAuditLog.ai_decision_meta` |
| 위험관리 차단 사유가 로그로 남는다 | `OrderAuditLog.reasons` (list); `test_risk_manager.py` |
| 백테스트 없는 전략은 UI 에서 "백테스트 가능" 표시 안 됨 | `registry_metadata.backtest_available()` 가 strategy 별로 carry, frontend `StrategyRegistryCard` 가 표시 |

---

## 16. 신규 추가 / 변경 내역 (본 감사 PR)

본 PR 은 *감사 보고서 + invariant 테스트* 만 추가하며 **매매 로직 / 안전
flag / API Key / Secret / .env / broker / Strategy / RiskManager 코드 0건
변경**.

### 16.1 추가 파일
- `docs/system_audit_2026_05.md` (본 문서)
- `backend/tests/test_system_audit_invariants.py` (사용자 요청서 #15 의 9개
  invariant 를 한 파일에서 통합 검증)

### 16.2 수정 파일
- `CLAUDE.md` — 본 감사 문서 인덱싱 (변경 시 동기화 정책)

### 16.3 추가된 데이터 모델
*없음.* 사용자 요청서의 `DecisionLog` / `OrderLog` 는 기존
`AgentDecisionLog` / `OrderAuditLog` 가 *이미* 모든 요구 필드를 표현한다 — §7,
§9 참고.

### 16.4 추가된 테스트 결과
- `test_system_audit_invariants.py`: 9개 통합 invariant — 6개 전략 존재 /
  가짜 전략명 0건 / 안전 flag default / Agent 가 broker 직접 호출 0건 /
  LIVE place_order NotImplementedError / paper 가드 / 긴급정지 enum 존재 /
  audit log 컬럼 / live_trading_available 모두 False

---

## 17. 최종 산출물 매핑 (사용자 요청서 #16)

| 요청 | 본 문서 위치 |
|---|---|
| 1. 실제 확인된 매매기법 목록 | §1 (6종) |
| 2. 제거한 예시 전략명 목록 | §14 (없음 — 처음부터 도입 0건, invariant 로 lock) |
| 3. 각 전략의 파일 위치 | §1 |
| 4. 각 전략의 현재 연결 상태 | §1 / §2 |
| 5. 모의투자 연결 여부 | §1 (6개 모두 ✅) / §5.3 |
| 6. 백테스트 연결 여부 | §1 (6개 모두 ✅) / §8 |
| 7. 위험관리 연결 여부 | §1 risk_profile / §4 (다층 가드) |
| 8. UI 표시명 변경 내역 | §12 (10곳) |
| 9. 추가된 데이터 모델 | §16.3 (없음 — 기존 모델로 충분) |
| 10. 추가된 테스트 결과 | §16.4 |

---

## 18. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙 + 안전 flag + 다층 가드
- [`docs/strategy_registry.md`](strategy_registry.md) — #81 메타데이터 정책
- [`docs/strategies.md`](strategies.md) — 전략 카탈로그 (있다면)
- [`docs/risk_policy.md`](risk_policy.md) — 위험관리 평가 순서
- [`docs/promotion_policy.md`](promotion_policy.md) — 모드별 승격 정책
- [`docs/agent_architecture.md`](agent_architecture.md) — #51 6역할 Agent
- [`docs/strategy_signal_aggregator.md`](strategy_signal_aggregator.md) — #84
- [`docs/strategy_selection_agent.md`](strategy_selection_agent.md) — #85
