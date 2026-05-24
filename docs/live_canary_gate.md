# 실전 Canary Gate (#43 / 5-03)

> 충분한 KIS 모의 / Paper 검증 후 *제한적* 실전 테스트(canary)를 검토할 때도,
> canary 가 무제한으로 열리지 않도록 조건을 고정합니다. **이 게이트를 모두
> 통과해도 실제 실전 주문은 생성되지 않습니다** — `canary_ready=True` 는 *검토
> 가능* 상태일 뿐입니다. `LIVE_AI_EXECUTION` 은 canary gate 통과 전 불가합니다.

이 문서는 [`docs/live_manual_approval_gate.md`](live_manual_approval_gate.md)
(#42) 와 Paper Gate([`docs/paper_gate_policy.md`](paper_gate_policy.md), #72)
위에 쌓이는 안전 게이트입니다.

## 실전 canary 의 목적

- KIS 모의/Paper 에서 충분히 검증된 전략을, *극소액 · 단일 종목 · 1일 1건* 수준의
  **제한적** 실전 테스트로 검토하기 위한 단계입니다.
- **실전 자동매매 전환이 아닙니다.** canary 는 운영자가 수동으로, 매우 제한된
  범위에서만 검토합니다.

## canary 가 검토 가능(canary_ready)이 되려면 — 모두 충족

1. **#42 Live Manual Approval Gate 전체 통과**: Live Capital Review + Manual
   Approval + Operator Approval(operator+사유+시각) + Symbol Whitelist(+주문 종목
   포함) + Max order notional + Daily live limit.
2. **Paper Gate(#72) PASS** + `can_review_live_canary=true`.
3. **`LIVE_AI_EXECUTION` 비활성**(canary 는 AI 자동 실행이 아님 — 위반 시
   `CANARY_AI_EXECUTION_BLOCKED`).
4. **1일 1건 제한**: `daily_order_count_limit == 1` 이고 오늘 canary 주문 건수가
   한도 미만.
5. **최소 주문금액** 설정 (`min_order_notional_krw > 0`).
6. **최대 주문금액** 설정 (`max_order_notional_krw > 0`, 최소금액 이상, 그리고
   canary 허용 상한 이하 — 무제한 금지).
7. **Daily live notional limit** 설정.
8. **canary risk profile = CONSERVATIVE**(보수적).
9. **canary 운용 기간(window) 활성**.

위 중 하나라도 없으면 canary 는 **차단**되고 reason_code 가 기록됩니다.
기본 상태(아무 조건 없음)에서는 항상 차단됩니다.

> 성과 기준(거래 건수/일수/PF/승률/MDD 등)은 **기존 Paper Gate 기준을 참조만**
> 합니다 — 본 게이트가 성과 기준을 새로 완화하지 않습니다.

## reason_code

| reason_code | 의미 |
|---|---|
| `NOT_A_LIVE_ORDER` | 실전 주문 요청이 아님 (Paper/모의 — 무회귀) |
| (#42 codes) | Live Capital Review / Manual / Operator / Whitelist / Max notional / Daily limit 미충족 |
| `CANARY_PAPER_GATE_REQUIRED` | Paper Gate 미통과 |
| `CANARY_REVIEW_NOT_AVAILABLE` | can_review_live_canary 아님 |
| `CANARY_AI_EXECUTION_BLOCKED` | LIVE_AI_EXECUTION 은 canary 전 불가 |
| `CANARY_DAILY_ORDER_LIMIT_REQUIRED` | 1일 1건 제한 미설정 |
| `CANARY_DAILY_ORDER_LIMIT_EXCEEDED` | 오늘 canary 한도 도달 |
| `CANARY_MIN_ORDER_NOTIONAL_REQUIRED` | 최소 주문금액 미설정 |
| `CANARY_MAX_ORDER_NOTIONAL_REQUIRED` | 최대 주문금액 미설정/최소 미만 |
| `CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH` | 최대 주문금액이 허용 상한 초과 |
| `CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED` | daily live notional limit 미설정 |
| `CANARY_RISK_PROFILE_REQUIRED` | 보수적 risk profile 아님 |
| `CANARY_WINDOW_REQUIRED` | canary 운용 기간 비활성 |
| `CANARY_REVIEW_READY` | 모든 조건 충족 — 실전 canary *검토 가능*(자동 실행 아님) |

## gate 통과 ≠ 실제 주문

- `canary_ready=True` 여도 `is_live_authorization` / `broker_order_sent` /
  `order_created` 는 **항상 false**, `broker_order_no=None` 입니다(dataclass
  `__post_init__` 가드). Paper Gate 통과 후에도 **자동 주문이 아닙니다.**
- 실제 실전 canary 주문 경로는 별도 옵트인 PR + 운영자 명시 승인 이후에만
  *검토*됩니다.

## 사용자가 확인할 항목

- 실전 canary 는 기본 차단이며, 위 9개 조건을 모두 충족해야 *검토 가능* 상태가
  됩니다.
- canary 는 1일 1건 · 극소액 · 단일 whitelist 종목 · 보수적 profile · 제한된
  기간으로만 검토됩니다.
- 이 게이트는 **실전 전환을 승인하지 않으며**, **수익을 보장하지 않습니다.**

## 관련 파일

- `backend/app/permission/live_canary_gate.py` (`evaluate_live_canary_gate`)
- `backend/app/permission/live_manual_approval_gate.py` (#42)
- `backend/tests/test_live_canary_gate.py`
- `backend/tests/test_live_canary_gate_policy.py`
- 배경: [`docs/live_manual_approval_gate.md`](live_manual_approval_gate.md),
  [`docs/paper_gate_policy.md`](paper_gate_policy.md),
  [`docs/capital_allocation_policy.md`](capital_allocation_policy.md)
