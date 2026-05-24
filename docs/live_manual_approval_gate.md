# 실전 주문 Manual Approval Gate (#42 / 5-02)

> 실전(LIVE) 주문은 **환경변수 하나로 켤 수 없습니다.** 반드시 Live Capital
> Review + 운영자 수동 승인(Manual Approval) + Operator Approval + Symbol
> Whitelist + Max order notional + Daily live limit 을 **모두** 통과해야만
> 실전 주문이 *검토* 가능합니다. 통과해도 자동으로 실전 주문이 생성되지
> 않습니다.

이 문서는 [`docs/capital_allocation_policy.md`](capital_allocation_policy.md)
(Paper ≠ Live capital, #41) 의 후속 안전 게이트입니다.

## 핵심 정책

1. 실전 주문은 **기본적으로 차단**됩니다.
2. `ENABLE_LIVE_TRADING` 가 true 여도 실전 주문은 **자동 허용되지 않습니다.**
3. `ENABLE_AI_EXECUTION` 가 true 여도 실전 주문은 **자동 허용되지 않습니다.**
4. **Live Capital Review** 승인이 없으면 차단됩니다.
5. **운영자 Manual Approval** 이 없으면 차단됩니다.
6. **Operator Approval**(operator + 사유 + 시각)이 없으면 차단됩니다.
7. **Symbol Whitelist** 가 없거나 주문 종목이 포함되지 않으면 차단됩니다.
8. **Max order notional** 설정이 없으면 차단됩니다.
9. **Daily live limit** 설정이 없으면 차단됩니다.
10. **Paper 승인 / Paper 자금**은 실전 승인으로 **재사용할 수 없습니다.**
11. 모든 조건을 충족해도 `is_live_authorization` / `broker_order_sent` /
    `order_created` 는 **항상 false** 이며 실제 주문은 생성되지 않습니다(검토
    readiness 표시 전용).

## 실전 주문 요청으로 간주되는 경우

`broker_order_type=KIS_LIVE` / `KIS_IS_PAPER` 가 false / `live_order_requested=true`
/ 운용 모드가 `LIVE_MANUAL_APPROVAL` · `LIVE_AI_ASSIST` · `LIVE_AI_EXECUTION`
중 하나일 때. (SIMULATION / PAPER / LIVE_SHADOW 는 실전 주문 요청이 아니며 게이트가
간섭하지 않습니다 — Paper 경로 무회귀.)

## reason_code

| reason_code | 의미 |
|---|---|
| `NOT_A_LIVE_ORDER` | 실전 주문 요청이 아님 (Paper/모의 — 차단 아님) |
| `PAPER_APPROVAL_NOT_LIVE_APPROVAL` | Paper 승인/자금을 실전 승인으로 사용 불가 |
| `LIVE_CAPITAL_REVIEW_REQUIRED` | Live 자금 검토 필요 |
| `LIVE_MANUAL_APPROVAL_REQUIRED` | 운영자 수동 승인 필요 |
| `OPERATOR_APPROVAL_REQUIRED` | operator/사유/시각 포함 승인 필요 |
| `SYMBOL_WHITELIST_REQUIRED` | Symbol Whitelist 설정 필요 |
| `SYMBOL_NOT_WHITELISTED` | 주문 종목이 whitelist 에 없음 |
| `MAX_ORDER_NOTIONAL_REQUIRED` | Max order notional 설정 필요 |
| `DAILY_LIVE_LIMIT_REQUIRED` | Daily live limit 설정 필요 |
| `LIVE_MANUAL_GATE_REVIEW_READY` | 모든 조건 충족 — 실전 주문 *검토* 가능(활성화 아님) |

검사 우선순위: paper 재사용 → live capital review → manual approval →
operator approval → symbol whitelist → symbol 포함 → max notional → daily limit.

## Paper 모드와 Live 모드 분리

- Paper / KIS_PAPER 경로는 본 게이트의 영향을 받지 않습니다(`NOT_A_LIVE_ORDER`).
- Paper 자금(시드머니/한도)·Paper 승인은 실전 한도/승인으로 **자동 연결되지
  않습니다** (#41 정책과 동일). payload 에 paper capital 필드가 섞이면 실전
  요청에서 `PAPER_APPROVAL_NOT_LIVE_APPROVAL` 로 차단됩니다.

## 안전 불변 (테스트로 lock)

- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` 만으로 허용 0건.
- 어떤 결과에서도 `is_live_authorization=false`, `broker_order_sent=false`,
  `order_created=false`, `broker_order_no=None`, `contains_secret=false`.
- broker / OrderExecutor / route_order / KIS live endpoint / 외부 HTTP / AI SDK
  import·호출 0건. 안전 flag(`.env`) 변경 0건.
- Secret / API key / 계좌번호 원문 carry 0건 (operator 이름만, 식별자).

## 사용자가 Settings 에서 확인할 문구

- "실전 주문은 manual approval 전용입니다."
- "ENABLE_LIVE_TRADING 만으로는 주문되지 않습니다."
- "Live Capital Review 와 운영자 승인이 필요합니다."
- "현재 실전 자동매매는 차단 상태입니다."

## 주의

- 이 문서/게이트는 **실전 전환을 승인하지 않습니다.** 실제 실전 주문 경로는
  별도 옵트인 PR + 운영자 명시 승인 이후에만 *검토*됩니다.
- 이 시스템은 **수익을 보장하지 않습니다.**

## 관련 파일

- `backend/app/permission/live_manual_approval_gate.py`
  (`evaluate_live_manual_approval_gate`)
- `backend/app/permission/live_capital_guard.py` (#41 Paper≠Live capital)
- `backend/tests/test_live_manual_approval_gate.py`
- `backend/tests/test_live_manual_approval_policy.py`
- 정책 배경: [`docs/capital_allocation_policy.md`](capital_allocation_policy.md)
