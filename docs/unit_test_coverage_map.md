# Unit Test Coverage Map (체크리스트 #65)

## 1. 목적

돈이 걸릴 수 있는 자동매매 시스템에서 **테스트 없이는 P0 모듈을 완료로 보지
않는다**. 본 문서는 RiskManager / OrderGuard / StrategyBase / BacktestEngine
4개 핵심(P0) 모듈의 테스트 위치 / 커버리지 / 정책을 한 곳에 정리한다.

## 2. P0 모듈 매핑

| 모듈 | 구현 | 단위 테스트 | 테스트 수 |
|---|---|---|---|
| **RiskManager** | `app/risk/risk_manager.py` | `tests/test_risk_manager.py` | **91** |
| **OrderGuard** | `app/risk/order_guard.py` | `tests/test_order_guard.py` | **33** |
| **StrategyBase** | `app/strategies/base.py` | `tests/test_strategy_base_contract.py` | **26** |
| **BacktestEngine** | `app/backtest/engine.py` | `tests/test_backtest_engine.py` + `tests/test_backtest_execution_costs.py` | **25 + 20 = 45** |
| **합계 (P0)** | — | — | **195** |

## 3. RiskManager — `tests/test_risk_manager.py`

검증 매트릭스 (사용자 사양 #65 §2 13개 항목 모두 포함):

| 항목 | 테스트 |
|---|---|
| max_order_notional 이하 통과 | `test_simulation_small_order_is_approved` |
| max_order_notional 초과 거절 | `test_rejects_order_over_notional_limit`, `test_lowered_notional_threshold_rejects_orders_that_default_would_approve` |
| daily loss limit 도달 시 BUY 거절 | `test_daily_loss_limit_rejects_new_orders` |
| emergency stop ON 시 신규 주문 거절 | `test_emergency_stop_rejects_otherwise_valid_order` + 4 mode별 short-circuit |
| stale price 거절 | `test_stale_price_rejects_with_explicit_reason` + 6 stale edge cases |
| AI execution disabled — AI order 거절 | `test_live_ai_execution_blocked_when_ai_flag_off` |
| min_ai_confidence 미달 거절 | `test_ai_confidence_below_threshold_rejected` + 6 confidence tests |
| enforce_ai_reasoning + reasoning 없으면 거절 | `test_ai_order_without_meta_rejected` + 7 reasoning tests |
| max_positions 초과 거절 | `test_max_positions_blocks_new_symbol` |
| symbol exposure 초과 거절 | `test_symbol_exposure_limit`, `test_symbol_exposure_pct_enforced` |
| total exposure 초과 거절 | `test_total_exposure_absolute_cap_enforced`, `test_total_exposure_pct_cap_enforced` |
| SELL/청산 정책 분리 (BUY와 다름) | **`test_sell_bypasses_max_positions_when_buy_blocked`**, **`test_sell_bypasses_max_order_notional_via_size_check_when_under_position`**, `test_total_exposure_only_buy_side_checked`, `test_symbol_exposure_pct_only_buy_side`, **`test_check_order_with_block_new_buy_blocks_buy_but_lets_sell_through`** |
| LIVE flag false 상태 live order 거절 | `test_manual_approval_rejects_when_live_trading_disabled`, `test_ai_assist_rejects_when_live_trading_disabled`, **`test_check_order_live_trading_disabled_maps_to_blocked`** |

#65에서 추가된 7건 (`**` 표시) — `check_order` 표준 진입점 + SELL 우회 경로
+ BLOCKED decision 의미 강화.

기타 검증 영역:
- `policy_from_settings` propagation (모든 가드 13건)
- AI rate-limit / global rate-limit / max_orders_per_day
- symbol_whitelist / market_hours / disable_ai_orders
- emergency_stop 우선순위 (AI kill switch보다 우선)
- AI confidence 임계 결합 검증

## 4. OrderGuard — `tests/test_order_guard.py`

#38 OrderGuard 구현 + #65 보강 — 33 tests across 7 test classes:

| Test class | 검증 영역 |
|---|---|
| `TestFingerprint` (10 tests) | fingerprint stability + price bucket + market-order ignores price + symbol/side/strategy/mode/chain 영향 + secret 미포함 |
| `TestIdempotency` (5 tests) | client_order_id 같음 = RETRY_REPLAY (안전), 다름 + 같은 fingerprint = DUPLICATE (차단) |
| `TestCooldown` (7 tests) | symbol / (strategy, symbol) / post-exit / AI extra |
| `TestPendingGuard` (4 tests) | PENDING BUY가 같은 방향 차단, SELL은 차단 안 함 |
| `TestDefaultConfigPassthrough` | 모든 window=0 → 항상 ALLOW (기존 호환) |
| `TestRouteOrderIntegration` (2 tests) | route_order에서 OrderGuard 통과 / 차단 |
| `TestSafety` (2 tests) | broker import 0건, DB write 0건 |
| `TestSixtyFiveGaps` (#65 추가, 2 tests) | REJECTED audit도 보수적 DUPLICATE 분류, 복합 가드 활성 시 ALLOW 안 됨 invariant |

## 5. StrategyBase — `tests/test_strategy_base_contract.py`

26 tests. Strategy abstract class 4개 메서드(`generate_signal` / `calculate_size`
/ `exit_rule` / `explain_signal`)의 default 구현 + invariant(`is_order_intent=
False`) lock.

#65 추가 5건:
- `test_to_legacy_signal_maps_all_five_action_values` — SignalAction 5개
  (BUY/SELL/EXIT/WATCH/NO_SIGNAL) 모두 legacy Signal(BUY/SELL/HOLD)로 매핑
- `test_strategy_context_carries_all_optional_fields` — regime / watchlist /
  account_equity / extra 모두 전달 가능 + validate_context OK
- `test_exit_plan_carries_time_exit_and_invalidation` — ExitPlan 모든 필드 carry
- `test_sizing_hint_with_reduce_only_flag` — reduce_only=True 시그널
- `test_from_legacy_signal_hold_yields_no_signal` — HOLD ↔ NO_SIGNAL

기존 21건은 dataclass 직렬화 + concrete 전략 호환 + Strategy 모듈 import
invariant 검증.

## 6. BacktestEngine — `tests/test_backtest_engine.py` + `tests/test_backtest_execution_costs.py`

### `test_backtest_engine.py` (25 tests)

- 21 baseline: legacy 경로(config=None) BUY/SELL/round-trip/force-close +
  PnL/Sharpe/avg win·loss/profit_factor metric 계산
- 4 #65 추가: `test_engine_rejects_non_positive_initial_cash`,
  `test_engine_rejects_non_positive_quantity`,
  `test_empty_bars_yields_zero_trades_and_full_cash`,
  `test_summarize_metrics_smoke`

### `test_backtest_execution_costs.py` (20 tests)

config 경로(execution_model / slippage / commission / tax / exit_on_last_bar)
변형 + gross vs net PnL + route 통합.

## 7. P0 정책

| 정책 | 강제 |
|---|---|
| **P0 모듈은 테스트 없이는 완료로 보지 않는다** | RiskManager / OrderGuard / StrategyBase / BacktestEngine 4개 모듈. 구현 직후 같은 PR에서 단위 테스트가 PASS해야 머지 가능 |
| **실거래 / LIVE 관련 코드는 테스트 없이 merge 금지** | `is_paper=False` 코드 경로, `ENABLE_LIVE_TRADING=true` 분기, AI execution gate 분기 모두 테스트 필수. CLAUDE.md '안전 가드를 코드 단에서 강제' |
| **외부 API 의존 테스트는 mock / fake 사용** | 실 KIS / 키움 / Anthropic API 호출 금지. MockBrokerAdapter / `_FakeAiClient` / `urlopen` 모킹 사용 |
| **stress / slow 테스트는 nightly / manual로 분리** | `Approvals.stress.test.jsx` 같은 heavy 테스트는 일반 CI에서 flaky 가능 — 별도 워크플로 (`*-ci-nightly.yml`) |
| **테스트 deterministic** | 시계 / 난수 / 네트워크 의존 0. 시간은 `datetime.now()` 직접 호출 대신 인자 주입 또는 freezegun 사용 (현재 코드는 안정성 위해 wall-clock 사용 비중 ↓) |
| **fixture는 mock 우선** | `conftest.py`가 DB / broker / AI client / market data를 in-memory mock로 override. `client` fixture가 TestClient를 캐시 — DB-touching 라우트 테스트도 격리 가능 |

## 8. 미완료 / 후속 backlog

| 항목 | 현 상태 | 후속 |
|---|---|---|
| 실 KIS LIVE adapter | `NotImplementedError` stub만, 테스트 없음 (의도적) | LIVE 활성화 PR에서 contract 테스트 추가 — 별도 옵트인 |
| 선물 LIVE 평가 | `FuturesRiskManager.evaluate_order` 항상 REJECTED — 분기 자체 lock 테스트 있음 | LIVE 활성화 PR 시 실제 시나리오 테스트 추가 |
| LLM 실 호출 통합 테스트 | `AiClient.analyze`는 `_FakeAiClient` 모킹만 사용 | 비용 발생 — manual / opt-in marker로 분리 |
| Approvals.stress flake | 200~500 row 렌더링 + hard timeout — 일반 CI에서 가끔 flake | 별도 nightly로 이동 또는 timeout 완화 |
| Coverage 정량 측정 | 본 PR은 *테스트 존재 / 시나리오 매핑* 중심. % 측정 미사용 | `pytest --cov` 도입은 후속 (CI 통합 시) |
| Property-based testing | 현재 모두 example-based | RiskManager의 한도 / OrderGuard fingerprint에 hypothesis 도입 검토 |

## 9. 테스트 실행 명령

```bash
# P0 모듈만 빠르게
cd backend
pytest tests/test_risk_manager.py tests/test_order_guard.py \
       tests/test_backtest_engine.py tests/test_strategy_base_contract.py -q

# 전체 backend
pytest -q

# 특정 invariant 추적 (예: SELL escape)
pytest tests/test_risk_manager.py -k "sell_bypasses" -v

# 정적 lint
ruff check app tests
```

## 10. 절대 invariant (변경 금지)

1. RiskManager / OrderGuard / StrategyBase / BacktestEngine 4개 모듈은 *각각*
   전용 테스트 파일을 갖는다. 통합 흐름 테스트(`test_e2e_*`)는 별도로 존재해도
   *단위 테스트를 대체하지 않는다*.
2. 본 4개 모듈의 **default 정책** (RiskPolicy default / OrderGuardConfig
   default / Strategy ABC default impl / BacktestEngine legacy 모드)은 모두
   테스트로 lock — 변경하려면 같은 PR에서 테스트 갱신 필요.
3. 실 broker / 실 KIS / 실 Anthropic / 실 Telegram API 호출은 *어떤 단위
   테스트에서도 발생하지 않는다*. 외부 호출은 mock / dry_run / NoOpChannel
   사용.
4. SELL/청산 방향은 BUY 한도 가드(max_positions, max_symbol_exposure,
   max_total_exposure, BLOCK_NEW_BUY)를 통과한다 — `test_sell_bypasses_*`
   시리즈로 lock.

## 11. 관련 PR / 체크리스트

- #28 StrategyBase contract 강화
- #34 RiskManager 표준 진입점
- #35 PositionLimitRule
- #36 Loss limit rules
- #37 3-Level Kill Switch
- #38 OrderGuard
- #65 Unit Test Coverage (본 PR — gap 보강 + 정책 문서)
