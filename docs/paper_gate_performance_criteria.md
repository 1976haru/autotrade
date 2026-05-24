# Paper Gate 성과 기준 (28일/100건) (#44 / 5-04)

> 실전 전환 *검토* 전에 Paper/KIS 모의 성과가 충분히 쌓이고 품질 기준을
> 만족하는지 판정합니다. **하나라도 미달이면 live promotion 은 차단**되며,
> 모든 기준을 충족해도 **자동으로 실전 주문으로 전환되지 않습니다**(auto live
> promotion 금지) — 결과는 "검토 가능성(READY_FOR_LIVE_REVIEW)" 또는
> "차단(BLOCKED / INSUFFICIENT_SAMPLE)" 만 표시합니다.

이 기준은 #72 Paper Gate([`docs/paper_gate_policy.md`](paper_gate_policy.md))
보다 *더 엄격한* 성과 집합이며, 충족 시 `can_review_live_canary=True` 를 산출해
Canary Gate([`docs/live_canary_gate.md`](live_canary_gate.md), #43)의 입력이 됩니다.

## 목적

KIS 모의/Paper 에서 충분한 표본과 품질이 확인되기 전에는 실전 전환을 *검토조차*
하지 않도록 차단하는 안전장치입니다.

## 최소 표본 (둘 다 요구)

- `evaluated_trades >= 100` (100건 이상) — 미만이면 `INSUFFICIENT_SAMPLE`
- `trading_days >= 28` (28거래일 이상) — 미만이면 `INSUFFICIENT_DAYS`

표본이 부족하면 성과 지표는 신뢰할 수 없으므로 즉시 `INSUFFICIENT_SAMPLE` 로
종결합니다(live promotion 차단).

## 성과 기준

| 기준 | 임계 | 미달 reason_code |
|---|---|---|
| profit_factor | ≥ 1.3 | `LOW_PROFIT_FACTOR` |
| win_rate (승률) | ≥ 50% | `LOW_WIN_RATE` |
| payoff_ratio (손익비) | ≥ 1.0 | `LOW_PAYOFF_RATIO` |
| expectancy (기대값) | > 0 | `NON_POSITIVE_EXPECTANCY` |
| average_return | > 0 | `NON_POSITIVE_AVERAGE_RETURN` |

## 리스크 기준

| 기준 | 임계 | 미달 reason_code |
|---|---|---|
| max_drawdown (MDD) | ≤ 10% | `HIGH_MAX_DRAWDOWN` |
| max_consecutive_losses (연속 손실) | ≤ 5 | `HIGH_CONSECUTIVE_LOSSES` |
| daily_loss_limit_breach | 0건 | `DAILY_LOSS_LIMIT_BREACH` |
| kill_switch_trigger | 0건 | `KILL_SWITCH_TRIGGERED` |

## 주문 품질 기준

| 기준 | 임계 | reason_code |
|---|---|---|
| order_failure_rate (주문 실패율) | ≤ 5% (FAIL) | `HIGH_ORDER_FAILURE_RATE` |
| rejected_rate (거절율) | ≤ 3% (FAIL) | `HIGH_REJECTED_RATE` |
| partial_fill_rate (부분체결율) | > 30% → **WARN** | `HIGH_PARTIAL_FILL_RATE` |
| avg_slippage_bps (평균 슬리피지) | > 50 bps → **WARN** | `HIGH_SLIPPAGE` |

> WARN 은 차단 사유가 아니라 주의 표시입니다. WARN 만으로는 READY 가 유지됩니다.

## 차단 사유 / 시장 데이터 품질 해석

- `NO_MARKET_DATA` / `MARKET_CLOSED` / `PRICE_STALE` 가 많은 표본은 *유효
  거래일* 로 보기 어렵습니다 — 표본/일수 산정에서 제외하고, 부족하면
  `INSUFFICIENT_SAMPLE` 로 차단하는 것이 안전합니다(장 닫힌 날은 정상).

## 정합성 / 무결성 / 보안 기준 (모두 0건)

| 기준 | 임계 | reason_code |
|---|---|---|
| portfolio_drift_critical | 0건 | `PORTFOLIO_DRIFT_CRITICAL` |
| event_integrity HIGH/CRITICAL | 0건 | `EVENT_INTEGRITY_CRITICAL` |
| secret_exposure | 0건 | `SECRET_EXPOSURE_DETECTED` |
| broker_order_type_mismatch | 0건 | `BROKER_ORDER_TYPE_MISMATCH` |

## 결과 (verdict)

- `INSUFFICIENT_SAMPLE` : 표본/일수 부족 — live promotion 차단.
- `BLOCKED` : 기준 미달(FAIL ≥ 1) — live promotion 차단(`LIVE_PROMOTION_BLOCKED`).
- `READY_FOR_LIVE_REVIEW` : 모든 기준 충족 — 실전 *검토 가능*
  (`can_review_live_canary=True`).

## 기준 충족 시에도 — auto live promotion 금지

- 모든 기준을 충족해도 `auto_live_promotion=false`, `is_live_authorization=false`
  (dataclass `__post_init__` 가드) 이며 실전 주문은 **생성되지 않습니다.**
- 다음 게이트가 별도로 필요합니다: **Live Capital Review**(#41) +
  **Manual Approval**(#42) + **Canary Gate**(#43). 본 기준 통과는 그 검토의
  *전제 조건*일 뿐입니다.

## 주의

- 이 게이트는 **실전 전환을 승인하지 않으며**, **수익을 보장하지 않습니다.**

## 관련 파일

- `backend/app/governance/paper_gate_performance.py`
  (`evaluate_paper_gate_performance`)
- `backend/tests/test_paper_gate_performance_criteria.py`
- `backend/tests/test_paper_gate_performance_policy.py`
- 배경: [`docs/paper_gate_policy.md`](paper_gate_policy.md)(#72),
  [`docs/live_manual_approval_gate.md`](live_manual_approval_gate.md)(#42),
  [`docs/live_canary_gate.md`](live_canary_gate.md)(#43)
