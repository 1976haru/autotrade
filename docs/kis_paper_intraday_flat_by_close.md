# KIS Paper 당일 청산형 단타 — V1 + V2(EOD Safety Hardening)

> KIS **모의투자** 전용이다. 실거래 권한은 없다 (`is_live_authorization=False`).
> 이 문서는 정책, 구현, 운영 절차, 남은 BLOCKER를 함께 기록한다.
> 이 작업은 전략 성과 개선과 무관하다. 단타 연구에서 확인된 PF 저조, 비용 차감 후 손실,
> 약 10초 레이턴시 문제는 **해결되지 않은 위험으로 남아 있다.**

## 1. 하루 일정 (KST, 서버 시계 기준)

| 시각 | 단계 (`IntradayPhase`) | 신규 BUY (자동·수동·승인큐) | 일반 SELL | coordinator |
|---|---|---|---|---|
| ~09:00 | PRE_OPEN | ✗ | ✗ | – |
| 09:00~09:05 | OPENING_BUFFER | ✗ | ✗ | – |
| 09:05~14:30 | ENTRY_OPEN | ✓ | ✓ | – |
| **14:30**~15:00 | BUY_CUTOFF | **✗ (14:30:00부터)** | ✓ (스캔) | 잔고·미체결 대조 + **미체결 BUY 취소** |
| **15:00**~15:15 | EOD_PRECHECK | ✗ | coordinator 단독 | 사전점검(1회) + **단계적 청산**(tick당 2종목, 손실 큰 종목부터) |
| **15:15**~15:20 | EOD_FLATTEN | ✗ | coordinator 단독 | **잔여 전량** 시장가 매도 |
| 15:20~15:30 | CLOSING_AUCTION | ✗ | ✗ | 대조만 (단일가 주문·취소는 기본 OFF) |
| **15:30**~ | POST_CLOSE | ✗ | ✗ | 최종 대조 → `FLAT_CONFIRMED` 또는 `FLATTEN_INCOMPLETE` |

- 정규장(KRX)만 사용한다. NXT 프리·애프터마켓은 쓰지 않는다. 취소 주문에도 `EXCG_ID_DVSN_CD=KRX`를 지정한다.
- 시각은 모두 정규장 마감 기준 오프셋이다.
  - 매수 마감 = 마감 − 60분
  - 사전점검 = 마감 − 30분
  - 강제청산 = 마감 − 15분. **12~30분 범위로 클램프**하므로 설정값으로 EOD를 끌 수 없다.
  - 단일가 = 마감 − 10분
- 단일 진실은 `app/scheduler/intraday_session.py`의 `IntradayPolicy`이다.

### 휴장일과 특수거래일
- 내장 달력은 2025~2027년이다.
  - 2026년부터 공휴일이 된 노동절(5/1)과 제헌절(2026-07-17)을 반영했다.
  - 2027년 대체공휴일을 반영했다: 2/9, 5/3, 7/19, 8/16, 10/4, 10/11, 12/27.
- **2027년 휴장일은 월력요항(2026-06 보도) 기준 잠정값이다.**
  KRX의 2027 휴장일 공지(보통 12월)가 나오면 확인해야 한다.
- **미확인 특수거래일**은 하루 종일 신규 BUY를 차단한다(`BUY_BLOCKED_SPECIAL_SESSION_UNCONFIRMED`).
  - 해당 날짜: 2026-11-19(목, 2027학년도 수능)과 2027-01-04(새해 첫 거래일).
  - EOD는 더 이른 마감(15:30) 기준으로 잡는다. 실제 장이 16:30까지 열려도 장중 청산이므로 안전하다.
  - KRX 공지를 확인한 뒤 override JSON의 `"confirmed_special_sessions": ["2026-11-19"]`로 해제한다.
- 달력에 없는 연도는 `CALENDAR_UNKNOWN`으로 처리해 신규 BUY를 차단한다. EOD는 기본 시각으로 수행한다.
- override 파일은 `KRX_CALENDAR_OVERRIDE_PATH`에 지정한다. 예:
  `{"holidays": [...], "special_sessions": {...}, "confirmed_special_sessions": [...], "covered_years": [...]}`

## 2. 신규 BUY 차단 — 모든 진입점

| 진입점 | 대상 | 위치 |
|---|---|---|
| 자동 스캔 / background tick | 프로그램 BUY | `driver_bridge` (BUY 후보 skip) + `auto_permission` |
| run-once | 프로그램 BUY | `auto_permission` → `route_order` |
| **단일 주문 진입점** | 프로그램 BUY (시간·달력·직전일 EOD 사고) + **수동매매 BUY** (KIS 브로커일 때 시간·달력) | `route_order` (브로커 호출 전 REJECTED audit) |
| 승인 큐 | 위와 동일 | `PermissionGate.approve` |
| 이미 접수된 미체결 BUY | 프로그램 BUY | EOD coordinator가 **취소 요청**한다 (§4) |

수동매매 BUY 차단은 KIS 브로커로 가는 주문에만 적용한다. mock/시뮬레이션 브로커는 거래소 세션과 무관하기 때문이다.

## 3. 목적별 권한 게이트 · RiskManager

| 목적 | 시간창 | confidence/quality | 일일 횟수 | 1회 금액 |
|---|---|---|---|---|
| BUY_ENTRY | 09:05~14:30 | 적용 | 적용 | `kis_paper_auto_max_order_notional` |
| NORMAL_EXIT | 09:05~15:20 | 면제(실보유) | 면제 | `kis_paper_auto_max_exit_order_notional` (0 = 보유수량이 상한) |
| EOD_FORCED_EXIT | 15:00~15:20 (단일가는 플래그) | 면제 | 면제 | 위와 동일 |

- SELL은 보유 수량 이내여야 한다. 무보유 매도와 숏은 금지한다.
- RiskManager에서 **보유 수량 이내 reduce-only SELL**은 진입 금액 한도와 일일 손실 한도를 적용하지 않는다.
  긴급정지는 SELL도 그대로 막는다.

## 4. 취소 주문 (V2, 모의 전용)

- 경로: `route_cancel` → `OrderExecutor.cancel` → `KisBrokerAdapter.cancel_order` → `KisClient.cancel_order_paper`.
  **`broker.cancel_order`를 호출하는 곳은 `OrderExecutor.cancel` 하나뿐이다** (#40 단일 진입점 확장).
- API는 공식 KIS 샘플(open-trading-api `order_rvsecncl`)을 따른다:
  - `POST /uapi/domestic-stock/v1/trading/order-rvsecncl`, 모의 tr_id `VTTC0013U`
  - body: `RVSE_CNCL_DVSN_CD=02`(취소), `QTY_ALL_ORD_YN=Y`(잔량 전부), `ORD_QTY=0`, `ORD_UNPR=0`,
    `ORD_DVSN=00`, `EXCG_ID_DVSN_CD=KRX`
  - 실전 TR(`TTTC0013U`)은 코드에 없다. `is_paper=False`이면 네트워크 호출 전에 거부한다.
- 가드(`route_cancel`): 아래 중 하나라도 해당하면 REJECTED audit만 남기고 브로커를 호출하지 않는다.
  - 긴급정지 ON (#37: 킬스위치 중 자동 취소 금지)
  - PAPER 모드가 아님
  - 실거래 플래그가 켜져 있음
  - paper 브로커가 아님
  - 같은 `client_order_id`가 이미 처리됨 → `DuplicateOrderError`
- `KRX_FWDG_ORD_ORGNO`에는 일별체결 행의 `ord_gno_brno`를 쓴다.
  - 값이 없으면 네트워크 호출 없이 `CANCEL_ORG_NO_UNKNOWN`으로 REJECTED 처리한다.
  - 정정취소가능주문조회(`inquire-psbl-rvsecncl`, TTTC0084R)는 **모의 TR이 없다.**
    그래서 "취소 가능 주문 조회 선행" 단계는 일별체결 전 페이지 조회(VTTC0081R) 잔량으로 대신한다.
- 취소 **접수**는 취소 완료가 아니다. 다음 대조에서 잔량이 0이면 `CONFIRMED_NO_REMAINING`이 된다.
  - 접수 후 90초 동안은 다시 요청하지 않는다.
  - 시도는 3회까지이고, 초과하면 `CANCEL_FAILED`와 CRITICAL 경고를 남긴다.
- 취소 직전에 체결된 수량은 KIS 잔고와 일별체결 수량으로 원장에 반영되고, EOD에서 매도한다.
- KIS 일별체결에 아직 보이지 않는 프로그램 BUY는 조직번호를 몰라 취소할 수 없으므로 대기한다.
  이런 BUY가 남아 있으면 `FLAT_CONFIRMED`를 주지 않는다.

## 5. EOD coordinator (`app/kis_paper/eod_liquidation.py`)

상태 흐름: `IDLE → BUY_CUTOFF_ACTIVE → FLATTENING ⇄ WAITING_FOR_FILLS → FLAT_CONFIRMED | FLATTEN_INCOMPLETE`, 그리고 `BLOCKED`.

- **실행**: `BackgroundTickDriver.run_forever`가 매 interval(기본 30초)마다 실행한다.
  - wall-clock 기준이라 루프가 RUNNING/STOPPED/PAUSED인지와 무관하고, 프론트엔드 polling에도 의존하지 않는다.
  - 스캔 tick보다 먼저, 같은 asyncio task에서 실행된다.
- **조용히 끄는 경로는 없다(V2).** 아래 설정을 제거했고, env에 남아 있어도 무시한다.
  - `KIS_PAPER_EOD_FLATTEN_ENABLED`
  - `KIS_PAPER_EOD_FLATTEN_WHEN_LOOP_STOPPED`
  - EOD가 멈추는 경우는 다음뿐이며, 모두 화면에 경고가 표시된다:
    - VIRTUAL_ONLY 모드
    - 실거래 플래그 ON
    - 긴급정지
    - KIS paper 브로커가 아님
- **청산 대상**은 `min(프로그램 순보유, KIS 실보유)`이다.
  - 수동 보유나 다른 모듈의 보유는 `unmanaged_holdings`로만 표시하고 매도하지 않는다.
  - 원장 조회에 실패하면 `BLOCKED/EOD_OWNERSHIP_UNKNOWN` + CRITICAL로 처리한다.
- **15:00 사전점검**: 잔고, 미체결, API 상태를 1회 기록한다. 실패하면 15:00에 바로 CRITICAL을 남긴다.
- **주문 경로**: `execute_kis_paper_auto_order → route_order → RiskManager → PermissionGate → OrderExecutor → KisBrokerAdapter(is_paper=True)`.
- **중복 방지 장치**:
  1. DB lease
  2. 미체결 SELL이나 KIS 미확인 EOD 주문이 있으면 추가 매도 0
  3. 결정적 ID와 route_order idempotency
     - 매도 ID: `eod-dYYYYMMDD-sSYMBOL-nN`
     - 취소 ID: `cxl-dYYYYMMDD-oODNO-nN`
  4. 종목당 3회 시도 제한
  5. 프로세스 내 Lock
  - ID 안의 글자(d/s/o/n)는 숫자 연속을 끊어, 비밀정보 스캐너의 credit_card 패턴 오탐을 막는다.
    구형식 ID는 AgentDecisionLog 기록이 거부되는 결함이 있었다(V2에서 수정).
- **최종 판정(15:30~)**: 아래가 모두 0이고 조회에 성공했을 때만 `FLAT_CONFIRMED`이다.
  - 프로그램 보유
  - 관리 종목 미체결 SELL
  - 프로그램 미체결 BUY와 미확인 BUY
  - 미확인 EOD 주문

  그 외에는 `FLATTEN_INCOMPLETE`와 CRITICAL을 남기고, 운영자 확인 전까지 다음 거래일 신규 BUY를 차단한다.
- **긴급정지**: 주문과 취소를 하지 않고 `BLOCKED`로 둔다(14:30 이후 모든 단계).
  보유가 남아 있으면 UI에 "수동 청산 필요"를 즉시 표시한다. 장중에도 DB 원장 기준으로 표시한다.
- **재시작**: 매 단계마다 KIS 잔고와 주문을 먼저 대조한다.
  상태, 시도 횟수, 주문번호, 취소 요청 이력은 DB(`eod_liquidation_state`, `order_audit_log`)에서 복원하므로 중복 주문이 나가지 않는다.
  다만 lease TTL 때문에 재시작 직후 최대 45초 지연될 수 있다.

## 6. 설정 (env, 안전 기본값)

| 변수 | 기본 | 의미 |
|---|---|---|
| `KIS_PAPER_AUTO_ORDER_WINDOW_END` | `14:30` | 신규 BUY 마감 (실효값 = min(이 값, 마감 − 60분)) |
| `KIS_PAPER_BUY_CUTOFF_MINUTES_BEFORE_CLOSE` | `60` | |
| `KIS_PAPER_EOD_FLATTEN_MINUTES_BEFORE_CLOSE` | `15` | [12, 30] 클램프 |
| `KIS_PAPER_EOD_STAGED_SYMBOLS_PER_STEP` | `2` | 15:00~15:15 tick당 매도 종목 수 |
| `KIS_PAPER_EOD_CLOSING_AUCTION_ORDERS_ENABLED` | `false` | 15:20~15:30 신규 청산·취소 (모의 접수 미확인) |
| `KIS_PAPER_EOD_MAX_ATTEMPTS_PER_SYMBOL` | `3` | EOD 매도·취소 시도 상한 |
| `KIS_PAPER_AUTO_MAX_EXIT_ORDER_NOTIONAL` | `0` | 청산 1회 금액 한도 (0 = 보유수량이 상한) |
| `KRX_CALENDAR_OVERRIDE_PATH` | `` | 휴장일/특수일 override JSON |

LIVE 플래그(`ENABLE_LIVE_TRADING=false`, `KIS_IS_PAPER=true`, `ENABLE_AI_EXECUTION=false`)는 바꾸지 않았다.

## 7. API / UI

- `GET /api/kis-paper/eod/status`: 브로커를 호출하지 않는다. 반환 항목:
  - 일정(14:30 / 15:00 / 15:15 / 15:30), 단계, 신규매수 가능 여부와 사유
  - EOD 상태, 진행 요약, 미체결 BUY 취소 현황
  - `emergency_stop`, `program_ledger_holdings`, `manual_liquidation_required`
  - `auto_liquidation_guaranteed=false`
  - 모드, 출처, 마지막 주문번호, 체결 감지 시각, 실패 코드, 대조 시각, background driver 동작 여부
- `POST /api/kis-paper/eod/acknowledge`: 사고를 확인 처리해 다음 날 BUY 차단을 해제한다. 주문은 내지 않는다.
- 홈의 `IntradaySessionPanel`에 표시되는 항목:
  - 일정 4개, 모의계좌 배지, 모드, 진행률
  - 취소 상태, 긴급정지 수동 청산 경고
  - "자동 보장 아님" 안내, 사고 확인 버튼

## 8. quick / slow 시험과 종일 운용의 차이

- 설정 화면의 quick(3틱)과 slow(20틱)는 **단발 점검**이다(KisPaperEngine, 기본 dry-run).
  종일 운용이 아니며 EOD 청산과 취소도 하지 않는다.
- 종일 운용은 백엔드 상시 서비스(`ENABLE_AI_PAPER_BACKGROUND_TICK=true`)가 담당한다.
- **PC 절전, 앱 종료, 네트워크 단절이 있으면 자동 청산과 취소가 실행되지 않는다. 자동 청산은 보장되지 않는다.**
  이 경우 `FLATTEN_INCOMPLETE` 또는 미판정이 되고, 다음 날 BUY가 차단된다.

## 9. BLOCKER / 알려진 한계

1. **실제 KIS 모의 환경에서 취소(VTTC0013U) 접수를 아직 확인하지 않았다.** 코드와 단위 테스트만 완료했다.
   `ORD_DVSN=00`과 `KRX_FWDG_ORD_ORGNO=ord_gno_brno` 매핑은 리허설에서 검증해야 한다.
2. 15:20~15:30 종가 단일가 구간에서 모의서버가 주문을 받는지 확인하지 못했다. 기본값은 OFF이다.
3. 주문 TR이 구형이다. 현재 KIS는 `VTTC0011U/0012U`를 쓰는데, 코드는 아직 `VTTC0801U/0802U`를 쓴다.
   기존 운영에서 체결이 확인되어 이번 작업에서는 바꾸지 않았다. 신형 TR 전환은 별도 작업이다.
4. 장 마감 후 일별체결에 소멸 잔량이 남아 보이면 보수적으로 `FLATTEN_INCOMPLETE`로 판정한다. 운영자가 확인해 해제한다.
5. 2027년 휴장일과 특수일(2026-11-19, 2027-01-04)은 잠정값이다. 미확인 특수일에는 신규 BUY를 차단한다.

## 10. 운영자 리허설 순서 (실주문은 운영자 별도 승인 후에만)

1. **점검 모드 리허설 (주문 0)**
   - `.env`에 다음을 설정한다:
     - `MARKET_DATA_PROVIDER=kis`
     - `ENABLE_AI_PAPER_BACKGROUND_TICK=true`
     - `ENABLE_KIS_PAPER_AUTO_TRADING=true`
     - `KIS_PAPER_AUTO_ORDER_DRY_RUN=true`
   - 백엔드를 재시작한다.
   - 패널에서 다음을 확인한다:
     - 일정 4개(14:30 / 15:00 / 15:15 / 15:30)
     - "모의투자 계좌" 배지
     - 실행기 경고가 없는지
   - 14:30에 "⛔ 신규 매수 불가"가 표시되는지, 15:00에 사전점검 기록, 15:15에 "강제청산 진행 중"이 표시되는지 확인한다.
2. **smoke 리허설 (운영자 승인 후)**
   - `KIS_PAPER_AUTO_ORDER_DRY_RUN=false`, `KIS_PAPER_SMOKE_MODE=true`(1종목·1주)로 바꾼다.
   - 확인할 것:
     - 실제 주문번호
     - 15:00~15:15 청산 체결
     - 15:30 "당일 청산 완료 확인"
     - **KIS 모의 앱 잔고가 0인지**
3. **취소 리허설 (운영자 승인 후)**: 프로그램 주문은 시장가라 14:30에 미체결 BUY가 남는 경우가 드물다.
   그래서 취소 경로는 미체결 BUY가 실제로 생긴 날에만 실측할 수 있다.
   그런 날에는 패널의 "미체결 매수 취소" 줄이 "취소 요청됨 → 잔량 없음 확인"으로 바뀌는지,
   KIS 앱에서도 취소가 반영됐는지 확인하고 기록한다. 그 전까지 취소는 **미검증**이다.
4. **사고 시**
   - 즉시 멈추기: 🛑긴급정지. 주문과 취소가 모두 멈추므로, 남은 보유는 KIS 모의 앱에서 직접 정리한다.
   - "청산 미완료"가 표시되면 KIS 앱에서 잔고와 미체결을 확인하고 정리한 뒤, 패널의 "확인했음"을 누른다.
