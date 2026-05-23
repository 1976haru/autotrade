# 장마감 강제 청산 SELL 후보 E2E 확인 (3-06)

단타/당일 전략의 **오버나이트 리스크**를 피하기 위해, 지정된 장마감 강제청산
시각(기본 **15:20 KST**)에 보유 종목을 SELL(MARKET_CLOSE_EXIT) 후보로 생성한다.
SELL 은 *보유 청산* 일 뿐 신규 숏 진입이 아니며, KIS 모의/Paper 기준이다. 실거래는
0건이어야 한다.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 실거래 활성화 절차가 아니다.

## 강제청산 정책

| 항목 | 기본값 |
|---|---|
| `market_close_exit_enabled` | 활성 시 평가 (PositionContext) |
| `force_exit_time_kst` | `15:20` |
| `market_close_time_kst` | `15:30` |
| `allow_overnight` | `false` (오버나이트 허용 시 강제청산 미발동) |

조건 (모두 충족 시 SELL / `sell_reason_code=MARKET_CLOSE_EXIT`):
1. `held_position=true` 이고 청산 가능 수량 > 0.
2. `market_close_exit_enabled=true`.
3. `allow_overnight=false`.
4. `current_time_kst ≥ force_exit_time_kst` (KST 시각 비교, "HH:MM").
   - legacy `market_time_phase=="CLOSING"` 도 트리거로 인정(하위호환).
5. 비정상 시각("99:99"/빈값 등)은 시각 트리거 미발동(예외 없음).

주문 전송 가능 여부(장 상태/readiness)는 **기존 게이트**가 막는다 — 본 기능은
*SELL 후보 판단*까지. 15:20 은 아직 주문 가능 시각으로 가정.

### 청산 조건 우선순위 (고정 · 테스트로 lock)

**STOP_LOSS > TAKE_PROFIT > TRAILING_STOP > MARKET_CLOSE_EXIT** (그 다음 VWAP/
Momentum). 동시 성립 시 위 순서로 결정.

## EXE 장중 확인 순서

1. 장마감 강제청산 시각 설정 확인 (기본 15:20 KST).
2. 보유 종목 존재 확인.
3. 15:20 도달 시 **Agent 탭** — `SELL` / `MARKET_CLOSE_EXIT` 표시.
4. **Decision Episode** — `final_action=SELL`, `sell_reason_code=MARKET_CLOSE_EXIT`,
   `held_position=true`, `is_short_entry=false`, SELL 수량 ≤ 보유,
   `broker_order_type=KIS_PAPER`, `is_live_authorization=false`.
5. **KIS 모의투자 화면** — SELL 주문 접수 + `broker_order_no` 확인.
6. **실전 계좌** — 주문이 없어야 한다.

## 주문이 *나가지 않아야* 하는 경우

- 강제청산 시각 전(예: 15:19) → 미발동.
- 보유 없음 → SELL 금지.
- `allow_overnight=true`(오버나이트 허용 전략) → 강제청산 미발동.
- `market_close_exit_enabled=false` / 비정상 시각 → 미발동.
- KIS 자격 미설정 / readiness BLOCKED / 거래 시간 창 밖 → 게이트 차단.

## 코드 단 보장 (테스트로 lock)

- `backend/tests/test_market_close_exit_sell.py` — 15:20 도달 → MARKET_CLOSE_EXIT
  SELL → KIS_PAPER_SUBMITTED + broker_order_no, 15:19 미발동, 보유없음/오버나이트/
  미설정/비정상시각/수량cap/우선순위(STOP_LOSS·TAKE_PROFIT·TRAILING 우선)/readiness
  차단.
- SELL 수량 ≤ 보유, `is_short_entry=false`/`short_position=false` 영구. 주문은
  sanctioned 경로(route_order → RiskManager → PermissionGate → OrderExecutor →
  KisBrokerAdapter[`place_order(is_paper=True)`]) 위임 — broker 직접 호출 0건.
  `broker_order_type=KIS_PAPER`, `is_live_authorization=false`. secret 저장 0건.
