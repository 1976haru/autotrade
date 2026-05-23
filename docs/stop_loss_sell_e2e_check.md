# 손절 SELL 자동화 E2E 확인 (3-03)

BUY 후 보유 종목 가격이 평균단가 대비 손절선(stop_loss)에 도달하면 자동으로
**SELL(STOP_LOSS) 청산** 판단이 생성되고 KIS 모의투자 SELL 주문까지 이어지는지
장중에 확인하는 체크리스트. **손실 확대 방지**가 목적이며, SELL 은 *보유 청산*
일 뿐 신규 숏 진입이 아니다. 실거래는 0건이어야 한다.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 실거래 활성화 절차가 아니다.

## 손절선 기준 (우선순위)

1. `position.stop_loss` (절대 손절가) 가 있으면 그대로 사용.
2. 없으면 `position.stop_loss_pct` 를 평균단가 기준으로 계산.
3. 없으면 risk_profile 기본 `stop_loss_pct`.
4. 아무것도 없으면 손절 SELL 판단 불가 (`STOP_LOSS_NOT_CONFIGURED`).

`stop_loss_pct` 내부 표준은 **percent**(2.0 = 2%). `0.02` 같은 ratio 입력은
자동으로 `2.0%` 로 정규화한다. 음수/0/None 은 미설정으로 안전 처리(예외 없음).

계산 예: 평균단가 75,000 · stop_loss_pct 2.0% → 손절가 75,000 × (1 − 0.02) =
**73,500**. 현재가 ≤ 73,500 이면 → SELL / `sell_reason_code=STOP_LOSS`.

## EXE 장중 확인 순서

1. BUY 체결로 보유 종목 생성.
2. **평균단가** 확인 (Decision Episode / 포지션).
3. **exit_plan 손절가**(또는 stop_loss_pct) 확인.
4. 현재가가 손절가 이하로 하락.
5. **Agent 탭** — 해당 종목 판단이 `SELL` / `STOP_LOSS` 로 표시되는지.
6. **Decision Episode** — 다음 확인:
   - `final_action = SELL`
   - `sell_reason_code = STOP_LOSS`
   - `held_position = true`, `is_short_entry = false`
   - SELL 수량 ≤ 보유 수량
   - `broker_order_type = KIS_PAPER`
   - `is_live_authorization = false`
7. **KIS 모의투자 화면** — SELL 주문 접수 + `broker_order_no` 확인.
8. **실전 계좌** — 주문이 없어야 한다 (반드시 확인).

## 주문이 *나가지 않아야* 하는 경우

- 보유 포지션 없음(`held_position=false` 또는 수량 0) → SELL 금지
  (`NO_HELD_POSITION_FOR_SELL`).
- 현재가가 손절선 위 → STOP_LOSS SELL 미생성.
- 손절 미설정(`STOP_LOSS_NOT_CONFIGURED`) → 가격 트리거 없음.
- KIS 자격 미설정 / readiness BLOCKED → 게이트 차단, `broker_order_sent=false`.

## 코드 단 보장 (테스트로 lock)

- `backend/tests/test_stop_loss_sell_automation.py` — pct→price 계산(절대 > pct >
  risk_profile default), BUY 후 하락 → SELL(STOP_LOSS) → KIS_PAPER_SUBMITTED +
  broker_order_no, 손절선 미도달/보유 없음/미설정/수량초과/readiness 차단.
- SELL 수량은 항상 보유(청산 가능) 수량 이하, `is_short_entry=false`/
  `short_position=false` 영구. 주문은 sanctioned 경로(route_order → RiskManager →
  PermissionGate → OrderExecutor → KisBrokerAdapter[`place_order(is_paper=True)`])
  로만 위임 — broker 직접 호출 0건. 모든 결과 `broker_order_type=KIS_PAPER`,
  `is_live_authorization=false`. secret / 계좌번호 저장 0건.
