# Futures Strategy Contract (#49)

본 문서는 [`FuturesStrategyBase`](../backend/app/futures/strategies/base.py)와 초기 mock 전략들의 contract를 정의한다. 주식 [`Strategy`](../backend/app/strategies/base.py)(#28)와 **분리된 별개 계층** — 선물은 양방향 포지션, *계약 수* 기반 sizing, 증거금, 레버리지, 만기, 롤오버, 틱가치 같은 차원이 신호 시그니처에 반영되어야 하기 때문.

본 PR(#49)은 **skeleton + mock 전략만** 추가한다. 실제 운영용 전략 / live 어댑터 연동은 별도 PR이며, 본 PR 시점 mock 전략은 *모두 1계약 이하*만 권장한다.

## 1. 주식 Strategy와의 분리

| 항목 | 주식 (`app.strategies`) | 선물 (`app.futures.strategies`, 본 모듈) |
|---|---|---|
| ABC | `Strategy` / `StrategyBase` (#28) | `FuturesStrategyBase` |
| Signal | `StrategySignal(action: BUY/SELL/EXIT/WATCH/NO_SIGNAL)` | `FuturesSignal(action: OPEN_LONG/OPEN_SHORT/CLOSE_*/HEDGE/ROLLOVER/REDUCE_SIZE/WATCH/NO_SIGNAL)` |
| Sizing | `SizingHint(quantity = 주식 수)` | `FuturesContractSizingHint(contracts = 계약 수, max 1)` |
| Exit | `ExitPlan(% 기반)` | `FuturesExitPlan(% + ticks + liquidation_buffer_pct)` |
| 만기/롤오버 | 없음 | `FuturesRolloverPlan(close+open advisory plan)` |
| 양방향 | 단방향 (BUY/SELL) | **양방향 (LONG/SHORT 진입 + 명시 청산)** |
| RiskManager | `app.risk.risk_manager.RiskManager` (#34) | `app.futures.risk.FuturesRiskManager` (#151) + `FuturesMarginRule` (#48) |

**MRO 분리**: `FuturesStrategyBase`는 주식 `Strategy`를 *상속하지 않는다*. `tests/test_futures_strategies.py::test_futures_strategy_base_does_not_inherit_stock_strategy`가 invariant lock — 한쪽이 다른 쪽 메서드를 우회 호출하는 경로 영구 차단.

## 2. 핵심 dataclass

### `FuturesSignalAction` enum

| 값 | 의미 |
|---|---|
| `OPEN_LONG` | 신규 LONG 진입 후보 |
| `OPEN_SHORT` | 신규 SHORT 진입 후보 |
| `CLOSE_LONG` | 보유 LONG 청산 후보 |
| `CLOSE_SHORT` | 보유 SHORT 청산 후보 |
| `HEDGE` | 헤지 진입 후보 (equity 노출 보정) |
| `ROLLOVER` | 만기 임박 close + open advisory |
| `REDUCE_SIZE` | 위험도 증가 — 계약 수 축소 |
| `WATCH` | 모니터링 (조건 근접) |
| `NO_SIGNAL` | 무신호 |

어떤 값도 broker 주문 *결정*을 의미하지 않는다 — Strategy는 *추천*만 한다.

### `FuturesContractSizingHint`

| 필드 | 의미 |
|---|---|
| `contracts` | 권장 계약 수 (정수). **본 PR mock phase: ≤ 1 강제** (dataclass 가드) |
| `risk_pct_of_equity` | equity 대비 위험 한도 (%) |
| `max_leverage_hint` | Strategy가 권장하는 leverage. 정책/시장 한도와 작은 값 효력 |
| `reduce_only` | 청산 의도. True면 close-only 처리 |
| `note` | 사람이 읽을 한 줄 사유 |

`contracts > 1`은 **ValueError** — `contracts must be <= 1 in #49 mock phase`. 운영자가 의도적으로 초과하려면 별도 옵트인 PR + margin reconciliation 필수.

### `FuturesExitPlan`

| 필드 | 의미 |
|---|---|
| `take_profit_pct` / `stop_loss_pct` | 진입가 기준 % (LONG/SHORT 모두 절대값) |
| `take_profit_ticks` / `stop_loss_ticks` | 호가 단위 기반 |
| `time_exit_bars` | N봉 후 자동 청산 advisory |
| `liquidation_buffer_pct` | `LiquidationRiskRule`(#48) 임계 referencing 운영자 가이드 |
| `invalidation` / `rule_summary` | 사람이 읽을 한 줄 |

### `FuturesRolloverPlan` (advisory only)

| 필드 | 의미 |
|---|---|
| `close_contract` | 청산할 근월물 code |
| `open_contract` | 신규 진입할 차월물 code |
| `days_to_expiry` | 잔존 일수 (calendar day floor — 영업일 캘린더는 별도 PR) |
| `recommended_window` | 권장 롤오버 시간대 |
| `rule_summary` | 사람이 읽을 한 줄 |

**broker 호출을 트리거하지 않는다** — `futures_broker_contract.md`(#47) §8 "자동 롤오버 금지" invariant 상속. plan dataclass일 뿐, 운영자가 close + open을 *수동으로* 결정한다.

### `FuturesSignal`

| 필드 | 의미 |
|---|---|
| `action` | `FuturesSignalAction` |
| `contract` | contract code |
| `contract_sizing` | `FuturesContractSizingHint \| None` |
| `exit_plan` | `FuturesExitPlan \| None` |
| `rollover` | `FuturesRolloverPlan \| None` (만기 임박 시) |
| `explanation` | `FuturesSignalExplanation \| None` |
| **`is_order_intent`** | **항상 False** — dataclass 자체 가드 |

`is_order_intent=True`로 만들면 `__post_init__`에서 ValueError. 호출자는 `FuturesRiskManager` → `AiPermissionGate`(#39) → `FuturesMarginRule`(#48) → (LIVE 어댑터, 본 PR 미존재)를 거쳐야 실제 주문이 만들어진다.

## 3. `FuturesStrategyBase` 메서드

```python
class FuturesStrategyBase(ABC):
    @property @abstractmethod
    def metadata(self) -> FuturesStrategyMetadata: ...

    def validate(self, context: FuturesStrategyContext) -> FuturesValidationResult:
        # default: bars 비어 있지 않은지만 검사
        ...

    @abstractmethod
    def generate_signal(self, context: FuturesStrategyContext) -> FuturesSignal: ...

    def explain(
        self, context: FuturesStrategyContext, signal: FuturesSignal,
    ) -> FuturesSignalExplanation | None:
        # default: signal.explanation 그대로 반환
        ...
```

## 4. 초기 mock 전략 (`mock_strategies.py`)

### 4.1 `FuturesTrendFollowingStrategy`

SMA crossover 기반 단순 추세추종.

| 조건 | 결정 |
|---|---|
| `len(bars) < slow_window` | WATCH (insufficient bars) |
| `SMA(fast) > SMA(slow)` | OPEN_LONG (1 contract) |
| `SMA(fast) < SMA(slow)` | OPEN_SHORT (1 contract) |
| `SMA(fast) == SMA(slow)` | WATCH (flat) |
| **만기 ≤ 5일 + 진입 신호** | WATCH + rollover plan carry (보수) |

기본 파라미터: `fast_window=5, slow_window=20, risk_pct_of_equity=0.5`.

### 4.2 `FuturesVolatilityBreakoutStrategy`

Bollinger-style 변동성 돌파.

| 조건 | 결정 |
|---|---|
| `len(bars) < lookback` | WATCH (insufficient bars) |
| `volatility_pct > max_volatility_pct` | **REDUCE_SIZE** (high vol regime) |
| `close > mean + band_k × stddev` | OPEN_LONG (1 contract) |
| `close < mean - band_k × stddev` | OPEN_SHORT (1 contract) |
| 채널 안 (`lower ≤ close ≤ upper`) | WATCH |
| **만기 ≤ 5일 + 진입 신호** | WATCH + rollover plan carry |

기본 파라미터: `lookback=20, band_k=2.0, max_volatility_pct=5.0, risk_pct_of_equity=0.5`.

### 4.3 `FuturesHedgeStrategy`

Equity 노출 보정 헤지 advisory.

| 조건 | 결정 |
|---|---|
| `\|exposure\| < min_exposure_krw` | NO_SIGNAL (헤지 불필요) |
| `exposure > 0` | HEDGE (SHORT 헤지 advisory) |
| `exposure < 0` | HEDGE (LONG 헤지 advisory) |
| `equity_exposure_krw is None` | NO_SIGNAL |

**실제 hedge 주문을 *직접* 만들지 않는다** — 운영자가 본 advisory를 보고 수동으로 주문을 결정한다. 기본 파라미터: `min_exposure_krw=5_000_000, risk_pct_of_equity=0.5`.

## 5. 절대 invariant (테스트로 강제)

| invariant | 가드 |
|---|---|
| `FuturesStrategyBase`는 주식 `Strategy`를 상속하지 않는다 | `test_futures_strategy_base_does_not_inherit_stock_strategy` |
| 모든 mock 전략은 `FuturesStrategyBase`만 상속 (주식 `Strategy` ⊄) | `test_mock_strategies_are_subclasses_of_futures_base_only` |
| `FuturesSignal.is_order_intent = True` 시 ValueError | `test_signal_rejects_is_order_intent_true` |
| `FuturesContractSizingHint.contracts > 1` 시 ValueError | `test_sizing_hint_rejects_more_than_one_contract` |
| `app.futures.strategies.base`는 broker / executor / route_order import 0건 | `test_strategies_base_does_not_import_broker_or_executor` |
| `mock_strategies.py`도 broker / executor / route_order import 0건 | `test_mock_strategies_does_not_import_broker_or_executor` |
| 자동 롤오버 *주문* 발신 0건 | `_maybe_rollover` 헬퍼는 plan dataclass만 반환 + 정적 grep 가드 |
| `Settings.enable_futures_live_trading=False` default | `test_settings_default_keeps_futures_live_trading_disabled` |
| `Settings.enable_ai_execution=False` default | `test_settings_default_keeps_ai_execution_disabled` |

## 6. 신호 → 주문 흐름 (LIVE 활성화 후, 별도 PR)

```
FuturesStrategyBase.generate_signal(context)
  ↓ FuturesSignal (is_order_intent = False)
FuturesRiskManager.evaluate_virtual_order      (#151 + #48 LeverageLimitRule + FuturesMarginRule + LiquidationRiskRule)
  ↓ APPROVED
AiPermissionGate.evaluate_ai_permission        (#39, requested_by_ai=True인 경우)
  ↓ ALLOWED
(LIVE 어댑터, 본 PR 미존재 — futures_broker_contract.md(#47) §6 + live_activation_blockers.md §3.1)
  ↓
FuturesBrokerAdapter.place_order
  ↓
futures_order_audit_log
```

본 PR 시점에는 **위 흐름의 마지막 두 단계가 실행되지 않는다** — `FuturesRiskManager.evaluate_order` LIVE 경로는 항상 REJECTED, LIVE 어댑터 코드 0건. mock 전략의 신호는 *수동 검토용*이며, 자동 주문 흐름에 연결되지 않는다.

## 7. AI Strategy 연계 (#39, #45)

선물 전략에서 AI를 사용하려면 추가 게이트가 필요:

- `FuturesSignal`이 AI에서 만들어졌다면 `requested_by_ai=True` carry → `AiPermissionGate` (#39) 검사
- LIVE_AI_EXECUTION 단계라면 `AIExecutionGate` (#45) 위에 *futures-specific* 보수적 한도 추가 — `futures_scope.md` §8 / §12 후속 과제 #5
- 본 PR 시점 mock 전략은 모두 결정론적 (AI 사용 0) — AI 연계는 후속 PR

## 8. 실전 활성화 전 필수 조건

mock 전략을 *실 거래용 전략*으로 승격하려면:

1. **주식 MVP 완료** + 선물 모의환경 4주+ 무사고
2. **margin reconciliation** — `FuturesMarginRule.preview`(#48) vs broker API 실제 증거금 차이 측정
3. **liquidation buffer 검증** — `LiquidationRiskRule` 3%/7% threshold 실 시장 적합성
4. **rollover 흐름 검증** — `FuturesRolloverPlan` advisory 기반 운영자 수동 close+open 절차 확립
5. **`FuturesContractSizingHint.contracts > 1`** 별도 옵트인 PR — margin / leverage / liquidation 영향 재평가 필요
6. **AI 사용 시** — `FuturesAIExecutionGate` (별도 PR) 추가 한도 적용
7. **LIVE 어댑터** — `live_activation_blockers.md` §3.1 9-step 모두 통과

## 9. 변경 시 동기화

- `FuturesSignalAction` enum 추가/제거
- `FuturesContractSizingHint` 필드 추가/제거 또는 `contracts` 제한 변경
- 새 mock 전략 추가
- `FuturesRolloverPlan` 자동 실행 정책 변경 → **별도 옵트인 PR + 본 문서 §4 / §5 / `futures_broker_contract.md` §8 갱신**

## 관련 문서

- [`futures_scope.md`](futures_scope.md) — 선물 1차 범위 + 국내/해외선물 비교 (#46)
- [`futures_broker_contract.md`](futures_broker_contract.md) — `FuturesBrokerAdapter` 공식 contract (#47)
- [`futures_margin_risk.md`](futures_margin_risk.md) — Margin/Leverage/Liquidation rules (#48)
- [`futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식 + invariant (#151)
- [`futures_promotion_policy.md`](futures_promotion_policy.md) — **선물 단계별 승격 정책 (#76)** — 전략 승격은 본 정책의 단계별 기준을 따른다 (자동 롤오버 금지, 만기 5일 이내 신규 진입 강등 등)
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 시 변경 매트릭스
- [`strategy_contract.md`](strategy_contract.md) — 주식 Strategy contract (#28, 참고용)
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
