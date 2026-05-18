# Step 4-08 — Paper Position Sizing (Risk cap 기반 가상 수량 계산)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 sizer 는 *advisory* — Paper 단계의 가상 수량 산정만, 실 broker 호출 0건.

## 1. 목적

4-07 `bridge_explanation_to_paper_decisions()` 는 AI Agent 추천을
`PaperDecision` (BUY/SELL/HOLD/EXIT/NO_OP) 으로 변환할 때 기본적으로
*고정 1주* (`virtual_trade_size=1`) 를 사용했다. 4-08 은 그 자리를 **위험
한도 기반 가상 수량** 으로 채워, BUY/SELL/EXIT 의 *가상 체결* 이 실거래
직전 단계에서 RiskManager(#34) / PositionLimitRule(#35) 과 동일한 직관을
따르도록 만든다.

**실 broker / OrderExecutor / route_order 호출 0건** —
`SizingResult.is_order_signal=False` / `auto_apply_allowed=False` /
`is_live_authorization=False` 양 끝 lock.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/auto_paper/position_sizer.py` | `PositionSizingPolicy` + `SizingInput` + `SizingResult` + `compute_position_size()` |
| `backend/app/agents/paper_decision_bridge.py` | 4-07 bridge — 4-08 시 `sizing_policy` / `price_lookup` / `account_equity` / `confidence_lookup` 인자 추가 |
| `backend/tests/test_auto_paper_position_sizing.py` | 54 tests — 정책 검증 / 입력 검증 / 결과 invariant / cap 우선순위 / multiplier / 정적 가드 |
| `backend/tests/test_paper_decision_bridge.py::TestBridgePositionSizing` | 7 tests — bridge 통합 |
| `docs/paper_position_sizing.md` | 본 정책 |

## 3. 핵심 공식

```
position_risk_krw   = account_equity × max_risk_per_trade_pct   (default 1%)
base_quantity_raw   = position_risk_krw / (price × stop_loss_pct)
size_multiplier     = confidence_factor × risk_flag_factor × regime_factor
final_quantity      = floor( min( base_quantity_raw × size_multiplier ,
                                  max_position_pct × equity / price ,
                                  max_position_krw / price ) )
```

### Multiplier 표

| 차원 | 입력 | factor |
|---|---|---|
| confidence | ≥ 0.9 | 1.00 |
| | ≥ 0.7 | 0.85 |
| | ≥ 0.5 | 0.60 |
| | ≥ threshold (0.40) | 0.50 |
| | < threshold | quantity=0 (BLOCKED_LOW_CONFIDENCE) |
| risk_flag_count | 0 | 1.00 |
| | 1 | 0.70 |
| | 2 | 0.40 |
| | ≥ 3 (= `max_risk_flags`) | quantity=0 (BLOCKED_RISK_FLAGS) |
| market_regime | `TREND_UP` | 1.00 |
| | `SIDEWAYS` | 0.80 |
| | `TREND_DOWN` / `HIGH_VOLATILITY` / `CHOPPY` | 0.50 |
| | `LOW_LIQUIDITY` | 0.30 |
| | `UNKNOWN` | 0 (BLOCKED_UNKNOWN) |

## 4. Cap 우선순위 (위에서 아래로 — 첫 trigger 가 즉시 quantity=0 또는 cap 적용)

```
1. loop_state == EMERGENCY_STOP        → BLOCKED_EMERGENCY (영구)
2. market_regime == UNKNOWN            → BLOCKED_UNKNOWN
3. confidence < min_confidence         → BLOCKED_LOW_CONFIDENCE
4. risk_flag_count ≥ max_risk_flags    → BLOCKED_RISK_FLAGS
5. price ≤ 0 or NaN / Inf              → INSUFFICIENT_DATA
6. account_equity ≤ 0 or NaN / Inf     → INSUFFICIENT_DATA
7. risk-based base 계산
8. multiplier 결합
9. min(sized_raw, max_position_pct cap, max_position_krw cap)
10. floor() — min_unit_quantity 미달 시 INSUFFICIENT_DATA
```

## 5. 기본 `PositionSizingPolicy` (운영자 조정 가능)

| 필드 | default | 의미 |
|---|---|---|
| `max_risk_per_trade_pct` | 0.01 | 1회 거래 손실 한도 (계좌 자본 대비) |
| `default_stop_loss_pct` | 0.03 | 종목별 stop_loss 미제공 시 사용 |
| `max_position_pct` | 0.20 | 1 종목 최대 비중 (계좌 자본 대비) |
| `max_position_krw` | 5,000,000 | 1 종목 최대 KRW 노출 |
| `min_confidence_threshold` | 0.40 | confidence 미달 시 quantity=0 |
| `max_risk_flags` | 3 | risk_flag 수 ≥ 이 값 → quantity=0 |
| `min_unit_quantity` | 1 | 정수 가상 주식 최소 단위 |

## 6. Bridge 연결 (4-07)

`bridge_explanation_to_paper_decisions(..., sizing_policy=...)` 가 None 이
아닐 때만 4-08 sizing 이 적용된다 (backwards compat 보장).

**입력 추가 인자**:

| 인자 | 타입 | 의미 |
|---|---|---|
| `sizing_policy` | `PositionSizingPolicy \| None` | None 이면 legacy `virtual_trade_size` 사용 |
| `price_lookup` | `dict[(strategy, symbol), float]` | 가상 체결가 — 미제공 시 INSUFFICIENT_DATA |
| `account_equity` | `float \| None` | 계좌 자본 — 0/None 이면 INSUFFICIENT_DATA |
| `confidence_lookup` | `dict[(strategy, symbol), float]` | AI confidence — 미제공 시 0.5 |

**동작**:

- BUY/SELL/EXIT direction 에 대해 `compute_position_size()` 호출.
- `sizing_result.quantity == 0` → direction 을 HOLD 로 강등 + `block_reasons`
  에 사유 append.
- `sizing_result.quantity > 0` → `virtual_trade_size` 를 sizing 결과로 대체.
- `PaperDecision.metadata` 에 `sizing_verdict` / `sizing_quantity` carry.
- `BridgeReport.metadata.sizing_applied=True` + `sizing_results=[...]` carry.

## 7. 절대 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `SizingResult.is_order_signal=False` | `__post_init__` ValueError |
| `SizingResult.auto_apply_allowed=False` | 위 |
| `SizingResult.is_live_authorization=False` | 위 |
| `SizingResult.quantity >= 0` | `__post_init__` ValueError |
| `EMERGENCY_STOP → quantity=0` | `test_emergency_stop_blocks_all` |
| `UNKNOWN regime → quantity=0` | `test_unknown_regime_blocks` |
| `confidence < threshold → quantity=0` | `test_low_confidence_blocks` |
| `risk_flag_count ≥ max → quantity=0` | `test_too_many_risk_flags_blocks` |
| `price ≤ 0 → quantity=0` | `test_invalid_price_blocks` |
| `account_equity ≤ 0 → quantity=0` | `test_invalid_equity_blocks` |
| `confidence high > confidence low` | `test_high_confidence_larger_than_mid` |
| `risk_flag 증가 → quantity 감소` | `test_risk_flags_reduces_quantity` |
| `stop_loss 증가 → quantity 감소` | `test_stop_loss_larger_reduces_quantity` |
| `TREND_UP > TREND_DOWN` 수량 | `test_regime_trend_up_larger_than_trend_down` |
| `LOW_LIQUIDITY 감소` | `test_low_liquidity_reduces` |
| `HIGH_VOLATILITY 감소` | `test_high_volatility_reduces` |
| `CHOPPY 감소` | `test_choppy_reduces` |
| `max_position_pct cap` | `test_max_position_pct_cap_engaged` |
| `max_position_krw cap` | `test_max_position_krw_cap_engaged` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| 외부 HTTP / AI SDK import 0건 | 위 |
| `OrderRequest` / `OrderExecutor` call 0건 | `test_no_forbidden_calls` |
| DB write surface 0건 | `test_no_db_write_surface` |
| `settings.enable_*` mutation 0건 | `test_no_settings_mutation` |
| secret 필드 0건 (`api_key` / `secret` / `account_number` 등) | `test_no_secret_fields_in_input_or_result` |
| bridge metadata `sizing_results` carry | `test_sizing_results_in_metadata` |
| bridge `sizing_policy=None` backwards compat | `test_backwards_compat_no_policy_uses_fixed_size` |
| bridge `EMERGENCY_STOP` + sizing 차단 | `test_emergency_stop_with_policy_still_blocks_all` |

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 grep)
- ✅ KIS 주문 API / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ 실제 매수 / 매도 / Place Order 0건 — *결정론적 수량 계산기*
- ✅ 안전 flag default 변경 0건 (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `KIS_IS_PAPER` / `ENABLE_FUTURES_LIVE_TRADING` 그대로)
- ✅ DB write 0건 — 순수 함수
- ✅ secret 필드 0건 (API key / 계좌번호 carry 0개)
- ✅ AI Agent broker/executor 직접 호출 0건

## 9. 후속 PR 권고

- 운영자 UI — `PositionSizingPolicy` 를 frontend 카드에서 조정 (현재는 backend
  default 만, JSON 으로 호출자가 전달)
- API endpoint — `POST /api/auto-paper/position-sizer/preview` (read-only)
- 실 시장 데이터 기반 가격/equity 자동 carry — 현재는 caller (예: `auto_paper_loop`)
  가 명시 전달
- 다중 종목 동시 sizing — 현재 호출 횟수 = entry 수, 운영자가 portfolio
  단위 한도 검증 후 N+1 entry 차단 (correlation guard #95 / position_limits
  #35 와 연동)
- stop_loss 동적 추정 — 종목별 ATR / regime 기반 stop_loss_pct 자동 계산
