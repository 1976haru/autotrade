# 트레일링 스탑 SELL 자동화 E2E 확인 (3-05)

보유 종목이 상승해 **장중 최고가(high_watermark)** 를 갱신한 뒤, 최고가 대비
`trailing_stop_pct` 이상 하락하면 **수익 보호 SELL(TRAILING_STOP)** 청산 판단이
생성되고 KIS 모의투자 SELL 주문까지 이어지는지 확인하는 체크리스트. SELL 은
*보유 청산* 일 뿐 신규 숏 진입이 아니다. 실거래는 0건이어야 한다.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 실거래 활성화 절차가 아니다.

## 트레일링 스탑 기준 (우선순위)

1. `position.exit_plan.trailing_stop_pct` / `position.trailing_stop_pct`.
2. 없으면 risk_profile 기본 trailing_stop_pct (≈ stop 폭).
3. 아무것도 없으면 `TRAILING_STOP_NOT_CONFIGURED` (미발동).

`trailing_stop_pct` 내부 표준은 **percent**(2.0 = 2%). `0.02` ratio 입력은 `2.0%`
로 정규화. 음수/0/None 은 미설정(예외 없음).

계산: `trailing_stop_price = high_watermark × (1 − pct/100)`.
조건: `current_price ≤ trailing_stop_price` **AND** `high_watermark > average_entry_price`
(수익 보호 구간) **AND** `held_position=true` → SELL / `sell_reason_code=TRAILING_STOP`.

- `high_watermark` 가 없으면 *임의 추정하지 않고* 미발동.
- `high_watermark ≤ average_entry_price` 이면 아직 수익 보호 구간이 아니므로
  트레일링 미발동 (`TRAILING_STOP_NOT_IN_PROFIT`).

### 청산 조건 우선순위 (고정 · 테스트로 lock)

**STOP_LOSS > TAKE_PROFIT > TRAILING_STOP > MARKET_CLOSE_EXIT** (그 다음 VWAP/
Momentum vote). 동시 성립(비정상 데이터)은 위 순서로 결정한다.

## EXE 장중 확인 순서

1. BUY 체결로 보유 종목 생성, **평균단가** 확인.
2. 장중 **최고가(high_watermark)** 갱신 확인.
3. **trailing_stop_pct** / **trailing_stop_price** 확인.
4. 현재가가 trailing_stop_price 이하로 하락.
5. **Agent 탭** — `SELL` / `TRAILING_STOP` 표시 확인.
6. **Decision Episode** — `final_action=SELL`, `sell_reason_code=TRAILING_STOP`,
   `held_position=true`, `is_short_entry=false`, SELL 수량 ≤ 보유, `broker_order_type
   =KIS_PAPER`, `is_live_authorization=false`.
7. **KIS 모의투자 화면** — SELL 주문 접수 + `broker_order_no` 확인.
8. **실전 계좌** — 주문이 없어야 한다 (반드시 확인).

## 주문이 *나가지 않아야* 하는 경우

- 보유 없음 → SELL 금지. 현재가가 트레일가 위 → 미발동. high_watermark ≤ 평단
  (수익 구간 아님) → 트레일링 미발동(다른 조건은 별개). 미설정/invalid pct →
  미발동. KIS 자격 미설정 / readiness BLOCKED → 게이트 차단.

## 코드 단 보장 (테스트로 lock)

- `backend/tests/test_trailing_stop_sell_automation.py` — 상승 후 하락 →
  TRAILING_STOP SELL → KIS_PAPER_SUBMITTED + broker_order_no, 미도달/수익구간아님/
  보유없음/수량cap/미설정/우선순위(STOP_LOSS·TAKE_PROFIT 우선)/readiness 차단.
- SELL 수량 ≤ 보유, `is_short_entry=false`/`short_position=false` 영구. 주문은
  sanctioned 경로(route_order → RiskManager → PermissionGate → OrderExecutor →
  KisBrokerAdapter[`place_order(is_paper=True)`]) 위임 — broker 직접 호출 0건.
  `broker_order_type=KIS_PAPER`, `is_live_authorization=false`. secret 저장 0건.
