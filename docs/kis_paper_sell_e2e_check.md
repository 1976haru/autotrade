# SELL 주문 KIS 모의 API E2E 확인 (3-07)

3-02~3-06 에서 생성된 SELL(청산) 판단이 실제 KIS Paper SELL `route_order` 경로를
통해 **모의 주문으로 접수**되고, `broker_order_no` + 체결 상태(FILLED/부분/미체결/
거절)까지 기록되는지 확인하는 체크리스트. SELL 은 *보유 청산* 일 뿐 신규 숏 진입이
아니며, 실거래는 0건이어야 한다.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 실거래 활성화 절차가 아니다.

## SELL 트리거 (어느 것이든 → 청산)

STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / MARKET_CLOSE_EXIT
(우선순위: STOP_LOSS > TAKE_PROFIT > TRAILING_STOP > MARKET_CLOSE_EXIT).

## 체결 상태 처리

| 상태 | result | order_quality |
|---|---|---|
| FILLED | submitted=true / fill_status=FILLED / filled==qty | FILLED |
| PARTIALLY_FILLED | filled<qty / unfilled>0 / partial_fill=true | PARTIALLY_FILLED |
| UNFILLED | submitted=true / filled=0 / fill_status=None | SUBMITTED |
| REJECTED | submitted=false / broker_order_sent=false / broker_order_no 없음 | REJECTED |

모든 상태에서 `broker_order_type=KIS_PAPER`, `is_live_authorization=false`.

## EXE 장중 확인 순서

1. 보유 종목 존재 확인.
2. SELL 조건 발생 (STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / MARKET_CLOSE_EXIT).
3. **Agent 탭** — SELL 판단 확인.
4. **Decision Episode** — `sell_reason_code`, `kis_order_result.reason_code=
   KIS_PAPER_SUBMITTED`, `broker_order_no`, `broker_order_type=KIS_PAPER`,
   `order_status`/`fill_status`, `held_position=true`, `is_short_entry=false`.
5. SELL 수량 ≤ 보유 수량 확인 (초과 매도 없음).
6. **KIS 모의투자 화면** 주문내역에서 SELL 주문 접수 확인.
7. **실전 계좌** — 주문이 없어야 한다.

## 주문이 *나가지 않아야* 하는 경우

- KIS 자격 미설정 / readiness BLOCKED → route_order 호출 0건, `broker_order_sent=
  false`, `broker_order_no` 없음.
- 보유 포지션 없음 → SELL 판단 미생성 (강세장이면 신규 BUY 는 가능).
- 청산 가능 수량 0 → HOLD.

## 코드 단 보장 (테스트로 lock)

- `backend/tests/test_kis_paper_sell_e2e.py` — SELL(STOP_LOSS) → KIS_PAPER_SUBMITTED
  + broker_order_no, FILLED/PARTIALLY_FILLED/UNFILLED/REJECTED 상태, qty≤보유/
  available cap, no-short, readiness/보유없음/수량0 차단, AgentDecisionLog/
  decision_episode/order_quality/ledger 연결, secret 미노출, 전 상태
  is_live_authorization=false. route_order 는 fake (실 KIS API 0건).
- 주문은 sanctioned 경로(route_order → RiskManager → PermissionGate →
  OrderExecutor → KisBrokerAdapter[`place_order(is_paper=True)`]) 위임 — broker
  직접 호출 0건. raw KIS 응답 전체 저장 0건(allowlist + sanitize).
