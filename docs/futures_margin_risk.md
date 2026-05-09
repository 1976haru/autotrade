# Futures Margin Risk Policy (#48)

본 문서는 [`FuturesRiskManager`](../backend/app/futures/risk.py)(#151)의 inline 가드들을 *명시적 Rule*로 분리한 [`app.futures.margin_rules`](../backend/app/futures/margin_rules.py)의 정책·임계·invariant를 정의한다. 주식 [`PositionLimitRule`](../backend/app/risk/position_limits.py)(#35) 패턴을 선물 영역에 적용 — 한도/임계가 한 곳에 모이고, audit / API / UI가 동일 helper를 재사용한다.

본 PR은 새 가상/시뮬레이션 검사를 추가할 뿐, **실거래 활성화 / 자동 강제청산 주문 / 새 broker 호출**을 추가하지 *않는다*. 절대 원칙 6 + [`futures_scope.md`](futures_scope.md)(#46) + [`futures_broker_contract.md`](futures_broker_contract.md)(#47)와 일관.

## 1. 세 가지 Rule

| Rule | 책임 | 산식 |
|---|---|---|
| `LeverageLimitRule` | 주문 leverage가 *정책 한도*와 *contract 시장 한도* 둘 다 통과 | `leverage ≤ min(policy.max_leverage, contract.leverage_max)` |
| `FuturesMarginRule` | initial margin 충당 + `margin_used + initial ≤ max_margin_used` + maintenance margin buffer (advisory WARN) | `notional/leverage` (initial), `notional × maintenance_margin_pct/100` |
| `LiquidationRiskRule` | mark price와 liquidation price 거리(%) | `distance_pct = abs(mark - liq) / mark × 100` — 3% / 7% threshold |

각 Rule의 `check(...)` 메서드가 `MarginRuleResult(decision, reasons, warnings, metrics)`를 반환:

| `decision` | 의미 |
|---|---|
| `PASS` | 통과 |
| `WARN` | 통과지만 운영자 주의 — `warnings`에 사유 누적 (예: maintenance buffer thin, liquidation distance 3-7%) |
| `BLOCK` | 거부 — `reasons`에 사유 누적. `FuturesRiskManager`가 `REJECTED`로 변환 |

## 2. 결정 매트릭스 (LiquidationRiskRule)

| `distance_to_liquidation_pct` | 결정 | reason / warning |
|---|---|---|
| ≤ 3% (`liquidation_critical_pct`) | **BLOCK** → REJECTED | `"liquidation distance X.XX% <= critical threshold 3.0%"` |
| 3% < d ≤ 7% (`liquidation_warning_pct`) | **WARN** → REDUCE_SIZE 권고 (호출자 결정) | `"liquidation distance X.XX% in warning band"` |
| > 7% | **PASS** | (reason 누적 없음) |

본 임계는 [`FuturesRiskPolicy`](../backend/app/futures/risk.py)의 `liquidation_critical_pct` / `liquidation_warning_pct` 필드 default 값. **향후 환경/시장 변동성에 따라 조정 가능** — env 변수로 노출하거나 운영자 정책 override는 별도 PR.

### 결정 매트릭스 (FuturesMarginRule)

| 조건 | 결정 | 누적 |
|---|---|---|
| `mark_price ≤ 0` | BLOCK | `"mark_price must be positive"` |
| `margin_available < initial_margin` | BLOCK | `"margin_available {x} < required {y}"` |
| `margin_used + initial > max_margin_used` | BLOCK | `"margin_used {x} exceeds max_margin_used {y}"` |
| `margin_available_after < maintenance_margin` (initial은 충당 가능) | WARN | `"maintenance margin buffer thin: available_after {x} < maintenance {y}"` |
| 나머지 | PASS | — |

### 결정 매트릭스 (LeverageLimitRule)

| 조건 | 결정 | 누적 |
|---|---|---|
| `leverage ≤ 0` 또는 비유한값 | BLOCK | `"leverage must be positive"` |
| `leverage > effective_max` (= `min(policy_max, contract_max)`) | BLOCK | `"leverage X exceeds max_leverage Y"` 또는 `"leverage X exceeds contract leverage_max Y"` |
| 나머지 | PASS | — |

## 3. 임계 default + 운영자 가이드

| 필드 | default | 의미 | 조정 가이드 |
|---|---|---|---|
| `FuturesRiskPolicy.max_leverage` | 10.0 | 정책 한도 | KOSPI200 평균 5x. 운영자가 별도 PR로 보수적으로 낮출 수 있다. |
| `FuturesRiskPolicy.max_margin_used` | 1,000,000 | 절대값 한도 (KRW) | 운용 자본의 10% 이하 권장 |
| `FuturesRiskPolicy.max_contracts` | 1 | 신규 진입 후 총 보유 | 모의/시뮬에서 충분히 검증된 후 단계적 증가 |
| `FuturesRiskPolicy.max_daily_loss` | 200,000 | 일일 realized 손실 (양수) | 운용 자본의 2% 이하 권장 |
| `FuturesRiskPolicy.maintenance_margin_pct` | 10.0% | notional 대비 유지증거금 | KRX 실제 비율은 contract 별로 상이 — broker API 응답으로 reconciliation 권장 |
| `FuturesRiskPolicy.liquidation_critical_pct` | 3.0% | distance ≤ 이면 REJECTED | 변동성 큰 시장이면 5% 등으로 늘릴 수 있다 |
| `FuturesRiskPolicy.liquidation_warning_pct` | 7.0% | 3% < d ≤ 이면 WARN | 운영자 가이드 — 일중 변동성 vs 임계 |

## 4. 증거금 부족 시 차단 흐름

```
caller(strategy / 운영자)
  ↓
FuturesRiskManager.evaluate_virtual_order
  ↓
LeverageLimitRule.check       — leverage > effective_max → reasons += BLOCK
FuturesMarginRule.check       — margin_available < initial → reasons += BLOCK
                              — margin_used_after > max_margin_used → reasons += BLOCK
                              — maintenance buffer thin → warnings += WARN
LiquidationRiskRule.check     — distance ≤ 3% → reasons += BLOCK
                              — 3% < d ≤ 7% → warnings += WARN
                              — opposite-side close 의도 → skip
contract count > max_contracts → reasons += BLOCK
mark_price ≤ 0                → reasons += BLOCK
daily_realized_pnl breach      → reasons += BLOCK
  ↓
reasons 1+ → REJECTED
warnings만 있으면 APPROVED + warnings carry
```

reason 누적 substring("leverage", "max_leverage", "margin_available", "max_margin_used", "contracts", "daily futures loss")은 #151 PR 시점부터 호환되어야 하며, 본 #48 리팩터에서도 그대로 보존 — `tests/test_futures_margin_rules.py::test_evaluate_virtual_order_keeps_existing_reason_substrings`로 lock.

## 5. 추가 포지션 / Liquidation 위험 악화 처리

`LiquidationRiskRule.check`는 *블렌드 진입가* 기준으로 신규 주문 후 distance를 계산:

```
existing_qty   = sum(p.quantity for p in same_side_positions)
new_qty        = order.quantity
total_qty      = existing_qty + new_qty
existing_notional = sum(p.entry_price * p.quantity for p in same_side)
blended_entry  = (existing_notional + mark_price * new_qty) / total_qty
liq_price      = compute_liquidation_price(side, blended_entry, leverage, mm_pct)
distance_pct   = abs(mark_price - liq_price) / mark_price * 100
```

- **반대 side 포지션이 있으면 (close 의도)** → 본 Rule은 PASS (skip). reason: 청산 주문에 liquidation 차단을 적용하면 항상 막혀 빠져나갈 길이 없음. 청산 의도는 caller가 결정하는 흐름이며, 본 Rule은 *추가 진입* 시의 위험만 평가.
- **신규 진입 (existing 없음)** → blended = mark_price, distance ≈ liquidation buffer (leverage / maintenance margin에 따라 결정).

## 6. 자동 강제청산 *주문* 금지

본 Rule들은 강제청산 *위험*을 **계산만** 한다. 실제 강제청산 주문을 broker에 보내지 *않는다*:

- `app.futures.margin_rules`는 `force_liquidate_if_needed(` 또는 `.force_liquidate(` 함수 호출 0건 (정적 grep 가드: `test_margin_rules_does_not_emit_force_liquidate_orders`)
- `MockFuturesBroker.force_liquidate_if_needed`(#151) 역시 가상 환경 전용 — broker 외부 endpoint에 도달하지 않는다
- LIVE 어댑터(별도 PR)가 추가될 때도 자동 강제청산 주문은 *별도 옵트인* — 본 Rule들의 BLOCK 결정만으로 청산 주문이 자동 발생하지 않는다 (`futures_scope.md` §4 / §8 invariant 상속)

## 7. FuturesRiskManager 연계

`FuturesRiskManager.evaluate_virtual_order`는 본 PR에서 다음과 같이 변경:

- 기존 inline 가드를 `_build_rules()`로 추출, 세 Rule이 각자 책임 영역만 평가
- `FuturesRiskCheckResult`에 `warnings: list[str]`과 `metrics: dict` 필드 추가 (default 빈 값 — 기존 callers 호환)
- 새 인자 `contract_leverage_max: float | None = None` 추가 — caller가 contract spec의 시장 한도를 주입할 수 있다
- `live evaluate_order`는 변경 없음 — 여전히 항상 REJECTED (`enable_futures_live_trading=False` default + live 평가 로직 미구현)

기존 reason substring은 그대로 보존 — `test_evaluate_virtual_order_keeps_existing_reason_substrings`가 lock한다.

## 8. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/futures/margin/preview` | POST | 세 Rule을 read-only로 호출, 종합 결정 + reasons/warnings/metrics 반환. **broker 호출 0건, audit row 0건, DB 변경 0건** |

응답 형식:

```json
{
  "leverage":    { "decision": "PASS|WARN|BLOCK", "reasons": [...], "warnings": [...], "metrics": {...} },
  "margin":      { "decision": "PASS|WARN|BLOCK", "reasons": [...], "warnings": [...], "metrics": {...} },
  "liquidation": { "decision": "PASS|WARN|BLOCK", "reasons": [...], "warnings": [...], "metrics": {...} },
  "overall":     "PASS|WARN|BLOCK",
  "notice":      "선물 마진/레버리지/강제청산 위험을 read-only로 사전 평가합니다. ..."
}
```

`metrics`에 carry되는 핵심 키:
- `effective_max` (leverage), `policy_max`, `contract_max`
- `initial_margin`, `maintenance_margin`, `margin_used_after`, `headroom_pct`
- `liquidation_price`, `distance_pct`, `blended_entry_price`

`test_api_margin_preview_does_not_create_audit_or_orders`가 audit / approval / futures_audit row가 생성되지 않는지 invariant lock.

## 9. UI

[`frontend/src/components/tabs/FuturesMarginRiskCard.jsx`](../frontend/src/components/tabs/FuturesMarginRiskCard.jsx) — Futures 탭에 마운트.

- 입력: contract / side / 계약수 / mark price / leverage / margin used / margin available
- "📊 마진/위험 사전 평가" 버튼 → POST `/api/futures/margin/preview`
- 응답 표시:
  - **종합 결정** banner (PASS/WARN/BLOCK 색상)
  - 세 Rule의 결정 + reasons/warnings (− 빨강 / ⚠ 황색)
- "read-only / 실제 주문 아님" 배지 + "broker 호출 0건, audit 기록 0건" disclaimer

본 카드는 *주문 실행* UI가 아니다 — 시뮬 전용. 실제 선물 주문 활성화는 별도 PR.

## 10. 안전 invariant (테스트로 강제)

| invariant | 가드 |
|---|---|
| `app.futures.margin_rules`는 broker / OrderExecutor / route_order import 0건 | `test_margin_rules_module_does_not_import_broker_or_executor` |
| 자동 강제청산 *주문* 발신 코드 0건 | `test_margin_rules_does_not_emit_force_liquidate_orders` |
| 기본 정책의 `liquidation_critical_pct=3.0` / `liquidation_warning_pct=7.0` | `test_futures_risk_policy_default_keeps_live_trading_disabled` (확장) |
| 기존 reason substring 보존 (#151 호환) | `test_evaluate_virtual_order_keeps_existing_reason_substrings` |
| `evaluate_order` LIVE 경로 항상 REJECTED 유지 | `test_live_evaluate_order_still_rejects_*` |
| `Settings.enable_futures_live_trading=False` default | `test_settings_default_keeps_futures_live_trading_disabled` |
| `/api/futures/margin/preview`가 audit / approval / futures_audit row 생성 X | `test_api_margin_preview_does_not_create_audit_or_orders` |

## 11. 변경 시 동기화

다음 변경은 본 문서 + 관련 문서를 함께 업데이트해야 한다:

- `MarginRuleDecision` enum 추가/제거
- `LeverageLimitRule` / `FuturesMarginRule` / `LiquidationRiskRule` 필드 추가/제거
- `liquidation_critical_pct` / `liquidation_warning_pct` default 변경
- `FuturesMarginRule.maintenance_margin_pct` default 변경
- `/api/futures/margin/preview` 응답 schema 변경
- 자동 강제청산 정책 변경 — *별도 옵트인 PR* + 본 문서 §6 갱신

## 관련 문서

- [`futures_scope.md`](futures_scope.md) — 선물 1차 범위 + 국내/해외선물 비교 (#46)
- [`futures_broker_contract.md`](futures_broker_contract.md) — `FuturesBrokerAdapter` 공식 contract (#47)
- [`futures_strategy_contract.md`](futures_strategy_contract.md) — `FuturesStrategyBase` + mock 전략 3종 (#49)
- [`futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식 + invariant (#151)
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 변경 매트릭스 (선물 §3 + §3.1 9-step)
- [`risk_policy.md`](risk_policy.md) — 주식 RiskManager 평가 매트릭스 (참고용 — 선물은 별도)
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
