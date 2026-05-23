# 중복 매수·과다 노출 방지 재검증 (3-08)

자동매매가 공격적 성향(AGGRESSIVE)으로 동작하더라도 **Risk Gate / KIS Gate /
Permission Gate 를 우회하지 못한다**. 중복 매수 / 종목 비중 / 일일 매수금액 / 일일
주문 횟수 / per-order notional / 최대 보유 종목 수 / 현금 부족 / 1주 가격 초과가
모두 차단되는지 확인하는 체크리스트. KIS 모의/Paper 기준 — 실거래 0건.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 실거래 활성화 절차가 아니다.

## 차단 사유 reason_code (한국어)

| reason_code | 한국어 |
|---|---|
| DUPLICATE_POSITION_BUY_BLOCKED | 이미 보유 중인 종목이라 추가 매수 차단 |
| SYMBOL_WEIGHT_LIMIT_EXCEEDED | 종목별 최대 비중을 초과하여 매수 차단 |
| DAILY_BUY_LIMIT_EXCEEDED | 일일 최대 매수금액을 초과하여 매수 차단 |
| DAILY_ORDER_LIMIT_EXCEEDED | 일일 최대 주문 횟수를 초과하여 매수 차단 |
| NOTIONAL_LIMIT_EXCEEDED | 1회 주문금액 한도를 초과하여 매수 차단 |
| MAX_POSITIONS_REACHED | 최대 보유 종목 수에 도달하여 매수 차단 |
| INSUFFICIENT_PAPER_CASH | 남은 Paper 현금이 부족하여 매수 차단 |
| MIN_LOT_NOT_AFFORDABLE | 1주 가격이 투자한도 초과로 제외 |

KIS Gate sentinel(`KIS_PAPER_ORDER_LIMIT_EXCEEDED` / `KIS_PAPER_NOTIONAL_LIMIT_
EXCEEDED`)은 위 canonical code 로 정규화되어 동일 한국어로 표시된다.

## AGGRESSIVE 우회 불가 (핵심)

모든 Risk Gate 함수(`check_duplicate_position_buy` / `check_symbol_weight_limit` /
`check_daily_buy_limit` / `check_paper_affordability` / KIS permission gate)는
**risk_profile 인자를 받지 않는다** — 프로파일 무관 하드 한도이므로 AGGRESSIVE 가
완화/우회할 수 없다 (테스트로 lock). 차단 시:
- `route_order` 호출 0건 (KIS Gate 직전 차단).
- `broker_order_sent=false`, `broker_order_no` 없음, `KIS_PAPER_SUBMITTED` 미발생.
- AgentDecisionLog / decision_episode / ledger 에 reason_code 기록.

## SELL(청산) 은 별개

SELL 은 보유 청산이므로 중복 매수 가드가 막지 않는다(`SKIP_NON_BUY` /
`NOT_APPLICABLE`). SELL 수량은 보유 수량 이하 (3-02). 손절/익절/트레일링/장마감 SELL
무회귀.

## EXE 확인 순서

1. 보유 종목 추가 매수 시도 → "이미 보유 중인 종목이라 추가 매수 차단" 표시.
2. 종목 비중 한도 초과 시도 → "종목별 최대 비중을 초과하여 매수 차단".
3. 일일 매수금액/주문횟수 한도 도달 → 해당 한국어 차단 사유.
4. 현금 부족 / 1주 가격 초과 → 해당 차단 사유.
5. **Agent 탭 / BuyBlockReasons 카드 / Decision Episode** 에서 한국어 차단 사유 확인.
6. **KIS 모의투자 화면** — 차단된 종목은 주문 0건.
7. **실전 계좌** — 주문 0건.

## 코드 단 보장 (테스트로 lock)

- `backend/tests/test_risk_gate_buy_exposure_e2e.py` — 8개 한도 차단 + AGGRESSIVE
  우회 불가(시그니처에 risk_profile 없음) + KIS Gate 차단 시 route_order 0건 +
  SELL 무회귀 + reason_code 한국어 표시.
- `frontend/src/utils/buyBlockReasons.test.js` — 신규 코드 한국어 매핑 + alias.
- 주문은 sanctioned 경로 위임 — broker 직접 호출 0건, OrderExecutor/RiskManager/
  PermissionGate 우회 0건. `is_live_authorization=false`. secret/계좌 저장·표시 0건.
