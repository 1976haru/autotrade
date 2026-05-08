# Position Limit Policy (#35)

> 코드: [`backend/app/risk/position_limits.py`](../backend/app/risk/position_limits.py)
> RiskManager 연계: [`backend/app/risk/risk_manager.py`](../backend/app/risk/risk_manager.py)
> 테스트: [`backend/tests/test_position_limits.py`](../backend/tests/test_position_limits.py)

## 1. 목적

> **과도한 집중과 레버리지를 방지한다.**

전략이 BUY 신호를 만들고 RiskManager가 평가할 때, 본 rule이 다음을 동시에
검사:

- 1회 주문 한도 (절대값 + 자본 대비 %)
- 종목별 노출 한도 (절대값 + 자본 대비 %)
- 총 노출 한도 (절대값 + 자본 대비 %)
- 최대 보유 종목 수

한도 초과 시 RiskManager가 REJECTED를 반환 — broker로 가지 않는다 (#34
backstop).

## 2. 제한 항목

| 한도 | RiskPolicy 필드 | 적용 대상 | 0이면 |
|---|---|---|---|
| 1회 주문 명목금액 | `max_order_notional` | BUY/SELL | 검사 비활성 |
| 자본 대비 1회 주문 비율 | `max_position_size_pct` | BUY/SELL | 검사 비활성 |
| 종목당 노출 (절대값) | `max_symbol_exposure` | BUY 시 보유+신규 합 검사 | 검사 비활성 |
| 자본 대비 종목당 노출 | `max_symbol_exposure_pct` | BUY only | 검사 비활성 |
| 총 노출 (절대값) | `max_total_exposure` | BUY only | 검사 비활성 |
| 자본 대비 총 노출 | `max_total_exposure_pct` | BUY only | 검사 비활성 |
| 최대 보유 종목 수 | `max_positions` | BUY + 신규 종목 only | (양수 필수) |

기본값 (`RiskPolicy` default):
- `max_order_notional=1_000_000`
- `max_daily_loss=200_000` (별개 — daily PnL 가드)
- `max_positions=5`
- `max_symbol_exposure=1_500_000`
- 그 외 pct / total / pct는 0 (비활성)

## 3. BUY와 SELL 차이

**핵심 원칙: SELL/청산은 노출 *축소* 의도**. BUY와 동일한 한도 검사는 부적절.

| 한도 | BUY | SELL |
|---|---|---|
| `max_order_notional` | 검사 | 검사 (가격 × 수량 동일) |
| `max_position_size_pct` | 검사 | 검사 |
| `max_positions` | 신규 종목이면 검사 | 항상 통과 |
| `max_symbol_exposure` (절대) | 검사 (보유+신규) | 항상 통과 |
| `max_symbol_exposure_pct` | 검사 | 우회 (검사 자체 안 함) |
| `max_total_exposure` | 검사 | 우회 |
| `max_total_exposure_pct` | 검사 | 우회 |

위 정책은 *기존 evaluate_order 동작*을 그대로 보존한다 — #35 PR에서 inline
로직을 PositionLimitRule로 분리하면서 동일한 분기를 유지.

**공매도 / 선물 / short 정책은 별도** — 본 rule은 *현물 long-only* 가정.
short / inverse leverage 도입 시 SELL 정책을 재정의해야 한다 (backlog).

## 4. Exposure preview

`PositionLimitRule.build_preview(input)`은 주문 평가 직전 노출 / 잔여 capacity
스냅샷을 반환:

```python
@dataclass(frozen=True)
class PositionLimitPreview:
    order_notional:            int
    current_symbol_exposure:   int
    projected_symbol_exposure: int   # BUY: 증가, SELL: 감소
    current_total_exposure:    int
    projected_total_exposure:  int
    current_position_count:    int
    projected_position_count:  int
    will_open_new_position:    bool
    remaining_symbol_capacity:  int | None  # 한도 미설정이면 None
    remaining_total_capacity:   int | None
    remaining_position_slots:   int | None
```

`PositionLimitResult.preview`로 노출. 운영자/Agent/UI가 "이 주문이 들어가면
노출이 어떻게 변하나" 즉시 확인 — 한도 초과 직전 / 직후를 한눈에.

UI에서 활용 권장 위치:
- Approvals 상세 카드 — 승인 전 최종 점검.
- StrategyRisk 탭 — 정책 카드와 함께.
- AISignal 탭 — Agent 결정 옆 capacity surface.

본 PR(#35)에는 frontend 컴포넌트는 포함하지 않음 — backlog 후속.

## 5. Futures 분리

본 rule은 **현물 명목금액 기준** (`price × quantity`).

선물은 다음이 추가:
- 명목금액 = `price × multiplier × quantity` (multiplier 종목별 다름).
- **margin 한도** — 자본 대비 사용 가능 margin (`max_margin_used`).
- **레버리지 한도** — `max_leverage`.
- **계약 수 한도** — `max_contracts` (개수 단위).

[`backend/app/futures/risk.py`](../backend/app/futures/risk.py)의
`FuturesRiskPolicy`가 위 한도를 강제. 향후 `FuturesPositionLimitRule`로
분리 가능 (TODO — `app/risk/position_limits.py` 하단 주석 참조).

**FUTURES_LIVE는 현재 비활성** — `ENABLE_FUTURES_LIVE_TRADING=false` (default)
+ `FuturesRiskManager.evaluate_order` 항상 REJECTED. 본 rule이 FuturesRiskPolicy
를 import하지 않는 invariant를 테스트로 강제 (`test_position_limits_module_does_not_import_futures`).

## 6. Agent와의 관계

- `PositionSizingAgent`는 본 rule의 `build_preview` 결과를 참고해 권장 사이즈
  를 결정해야 한다. 단순히 `risk_profile.position_size_pct`만 보면 잔여
  capacity를 무시한 추천이 가능 — preview를 carry하면 "현재 종목 잔여 capacity
  500K → 권장 사이즈 50만원 cap" 식으로 정밀 조정.
- Agent가 높은 conviction을 가져도 한도 초과 시 RiskManager가 REJECT —
  Agent의 결정이 가드를 우회하지 않는다 (CLAUDE.md 절대 원칙 7).
- 본 PR에서 Agent ↔ PositionLimitRule 자동 연계는 도입하지 않음 — Agent
  Council의 `PositionSizingAgent` 옵트인 PR에서 통합 (backlog).

## 7. UI / Approval 연계

- `PendingApproval` 카드에 한도 초과 사유가 reasons로 carry — `decision=
  REJECTED`이면 audit row의 reasons 배열을 그대로 표시.
- AISignal / StrategyRisk 탭은 `RiskCheckResult.passed/reasons`를 PASS/FAIL
  카드로 분리해 보여줄 수 있음 (#33 SignalExplainabilityPanel 활용).
- 운영자가 한도를 운영 환경별로 튜닝하려면 `RiskPolicy.from_settings`로
  env 변수에서 주입 (이미 있는 RiskPolicy 어댑터).

## 8. RiskManager와의 연계 (#35 refactor)

`RiskManager.evaluate_order`는 inline position-limit 로직 대신 본 rule에 위임:

```python
pl_rule = PositionLimitRule(policy_from_risk_policy(self.policy))
pl_input = PositionLimitInput(order, balance, positions, latest_price)
_merge(pl_rule.check_order_notional(pl_input))
_merge(pl_rule.check_equity_relative_order_size(pl_input))
# (daily loss + cash 검사 inline 유지)
_merge(pl_rule.check_max_positions(pl_input))
_merge(pl_rule.check_symbol_exposure(pl_input))
_merge(pl_rule.check_symbol_exposure_pct(pl_input))
_merge(pl_rule.check_total_exposure(pl_input))
_merge(pl_rule.check_total_exposure_pct(pl_input))
```

- 기존 `result.reasons` / `result.passed` 문자열 / 순서 모두 동일 — 26+ 기존
  테스트 무수정 통과.
- single source of truth — 한도 로직이 한 곳에만 존재.
- preview는 `PositionLimitRule.build_preview()`로 별도 호출 가능 (RiskManager
  본체와 독립).

## 9. 향후 과제 (Position Limit backlog)

- **FuturesPositionLimitRule** — 계약 수 / margin / 레버리지 / 명목금액 분리.
- **PositionSizingAgent ↔ PositionLimitRule 자동 통합** — Agent가 build_preview
  결과를 참고해 사이즈 자동 조정.
- **Read-only API endpoint** — `POST /api/risk/position-limit/preview`로
  주문 사전 시뮬. 본 PR에는 미포함 (helper + 직접 호출로 충분).
- **Frontend preview card** — Approvals / StrategyRisk / AISignal 탭에 잔여
  capacity bar.
- **Per-strategy override** — 전략별 더 보수적인 sub-rule 적용.
- **공매도 / inverse / short 정책** — SELL이 노출 *증가*가 되는 경우 별도
  매트릭스.

## 10. 안전 invariant

- broker / RiskManager / PermissionGate / OrderExecutor / route_order 어떤
  함수도 본 rule이 직접 호출하지 않음 (read-only — 테스트 가드).
- `app.futures` import 0건 — 선물 정책은 별도 모듈.
- DB write 없음 — 본 rule은 순수 함수.
- 기존 RiskCheckResult 응답 호환성 유지 — reasons/passed 문자열 변경 0건.
- LIVE flag / API Key / Secret / 계좌번호 변경 0건.
