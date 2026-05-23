# KIS 모의투자 BUY 주문 E2E 확인 (3-01)

AI Agent Council 의 BUY 판단이 **KIS 모의투자(Paper) BUY 주문 접수**까지 이어지는지
장중에 직접 확인하는 체크리스트. **실거래가 아니다** — 모든 주문은 KIS 모의투자
경로(`broker_order_type=KIS_PAPER`, `is_live_authorization=false`)로만 나가며, 실전
계좌에는 주문이 0건이어야 한다.

> 본 문서는 운영 확인용이며 주문 신호가 아니다. 실거래 활성화 절차가 아니다.

## 1. EXE 실행 전 `backend/.env` 확인

| 변수 | 값 | 비고 |
|---|---|---|
| `DEFAULT_MODE` | `PAPER` | 모의 운용 |
| `KIS_IS_PAPER` | `true` | **실전 전환 금지** |
| `ENABLE_KIS_PAPER_AUTO_TRADING` | `true` | 모의 자동주문 허용 |
| `KIS_PAPER_AUTO_ORDER_DRY_RUN` | `false` | 실제 *모의* 주문 전송 (dry-run 아님) |
| `KIS_PAPER_FILL_POLLING` | `true` | 체결 갱신 (선택) |
| `ENABLE_LIVE_TRADING` | `false` | **실거래 차단** |
| `ENABLE_AI_EXECUTION` | `false` | AI 자동 실행 차단 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | 선물 차단 |

- `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` 는 **`backend/.env` 에만** 입력한다.
  프론트엔드 / 로그 / Git 에 절대 저장하지 않는다.

## 2. EXE 기동 후 UI 확인

1. **Settings 탭** — KIS 자격(App Key/Secret/계좌)이 *구성됨* 으로 표시되는지
   (값 자체는 노출되지 않고 `*_present` 상태만 표시).
2. **Agent 탭 (AgentCouncilVoteCard)** — 4전략(ORB/Momentum/Gap/VWAP) vote 와
   최종 판단이 `BUY` 인 tick 확인. exit_plan(손절/익절) 표시, RiskOfficer veto 미적용.
3. **Decision Episode 카드** — 해당 episode 에서:
   - `final_action = BUY`
   - `kis_order_result.reason_code = KIS_PAPER_SUBMITTED`
   - `broker_order_no` 값 존재 (예: `KIS-PAPER-BUY-0001`)
   - `broker_order_type = KIS_PAPER`
   - `is_live_authorization = false`
   - `exit_plan_validation.valid = true`, `risk_veto_result.veto_applied = false`

## 3. KIS 모의투자 화면 확인

- 한국투자증권 **모의투자** 계좌의 주문/체결 화면에서 위 종목 BUY 주문 접수를 확인.
- **실전 계좌**에는 주문이 없어야 한다 (반드시 별도 확인).

## 4. 안전 차단 동작 (주문이 *나가지 않아야* 하는 경우)

다음 상황에서는 BUY 신호가 있어도 KIS 모의 주문이 생성되지 않는다:

- KIS 자격 미설정 / readiness BLOCKED → 게이트 차단, `broker_order_sent=false`,
  `broker_order_no` 없음, `KIS_PAPER_SUBMITTED` 미발생.
- `ENABLE_KIS_PAPER_AUTO_TRADING=false` → 차단.
- exit_plan 검증 실패(가격 없음 등) → Agent Council 이 `HOLD` 로 강등 → 주문 0건.
- RiskOfficer veto(위험 플래그 허용치 초과) → `HOLD` 강등 → 주문 0건.
- 거래 시간 창(09:05~14:50 KST) 밖 → 차단.

## 5. 코드 단 보장 (테스트로 lock)

- `backend/tests/test_kis_paper_buy_e2e.py` — council BUY → `KIS_PAPER_SUBMITTED`
  + `broker_order_no` + `broker_order_sent=true` + AgentDecisionLog meta(votes/
  selected_strategies/risk_profile) 연결 + readiness/exit-invalid/risk-veto 차단.
- 주문은 항상 sanctioned 경로(`route_order` → RiskManager → PermissionGate →
  OrderExecutor → KisBrokerAdapter[`place_order(is_paper=True)`])로만 위임 —
  `broker.place_order` 직접 호출 0건, OrderExecutor 우회 0건.
- 모든 결과 `broker_order_type=KIS_PAPER`, `is_live_authorization=false`. raw KIS
  응답 전체 / secret / 계좌번호 저장 0건 (allowlist + sanitize).
