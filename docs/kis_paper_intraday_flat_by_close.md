# KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1 — 14:30 신규매수 마감 + 당일 청산

> KIS **모의투자** 전용. 실거래 권한이 아니다 (`is_live_authorization=False`).
> 이 문서는 정책·구현·운영 절차·알려진 한계(BLOCKER)를 함께 기록한다.
> 전략 성과 개선과 무관하다. 단타 연구에서 확인된 PF 저조, 비용 차감 후 손실,
> 약 10초 레이턴시 문제는 **해결되지 않은 위험으로 남아 있다.**

## 1. 하루 일정 (KST, 서버 시계 기준)

| 시각 | 단계(`IntradayPhase`) | 신규 BUY | 일반 SELL(손절·익절) | EOD 강제청산 |
|---|---|---|---|---|
| ~09:00 | PRE_OPEN | ✗ | ✗ | – |
| 09:00~09:05 | OPENING_BUFFER | ✗ | ✗ | – |
| 09:05~14:30 | ENTRY_OPEN | ✓ | ✓ | – |
| **14:30**~15:15 | BUY_CUTOFF | **✗ (14:30:00부터)** | ✓ | 대조만 |
| **15:15**~15:20 | EOD_FLATTEN | ✗ | coordinator가 단독 수행 | ✓ 시장가 매도 |
| 15:20~15:30 | CLOSING_AUCTION | ✗ | ✗ (기본) | ✗ (기본, 플래그로만) |
| **15:30**~ | POST_CLOSE | ✗ | ✗ | 최종 대조 → 판정 |

- 정규장(KRX 09:00~15:30)만 사용한다. NXT 프리·애프터마켓은 사용하지 않는다.
- 각 시각은 정규장 마감 기준 오프셋이다: 매수 마감 = 마감 − 60분, 강제청산 시작 = 마감 − 15분,
  단일가 시작 = 마감 − 10분. 특수일(예: 수능일 10:00~16:30)에는 모든 시각이 함께 이동한다.
- 단일 진실은 `app/scheduler/intraday_session.py`의 `IntradayPolicy`이다.
  프론트엔드 시각은 신뢰하지 않는다.

### 휴장일 달력
- 2025~2027 KRX 휴장일과 특수 세션이 코드에 내장되어 있다.
  **2027년분과 2026-11-19(수능일)는 잠정값이다. KRX 공식 공지로 확인해야 한다.**
- 달력에 없는 연도는 `CALENDAR_UNKNOWN`으로 처리해 신규 BUY를 차단한다.
  EOD 청산은 기본 시각으로 계속 수행한다.
- override 파일은 `KRX_CALENDAR_OVERRIDE_PATH`에 지정한 JSON이다:
  `{"holidays": ["YYYY-MM-DD"], "special_sessions": {"YYYY-MM-DD": ["10:00","16:30"]}, "covered_years": [2028]}`

## 2. 신규 BUY 차단: 모든 진입점에서 재검증

공통 helper는 `app/kis_paper/intraday_buy_gate.py::program_buy_block_reason`이다.
대상은 **프로그램 주문**(`trade_reason="kis_paper_auto"`)의 BUY만이다.

| 진입점 | 위치 |
|---|---|
| KIS 모의 자동주문 게이트 | `auto_permission.evaluate_kis_paper_order_permission` (BUY_ENTRY) |
| 실시간 스캔 tick | `driver_bridge.kis_paper_realtime_scan_tick` (BUY 후보를 skip) |
| **단일 주문 진입점** | `execution/order_router.route_order` (broker 호출 전 REJECTED audit) |
| 승인 큐 | `permission/gate.PermissionGate.approve` (재평가 단계) |

차단 사유 코드는 다음과 같다:
`BUY_CUTOFF_ACTIVE`, `BUY_BLOCKED_NON_TRADING_DAY`, `BUY_BLOCKED_CALENDAR_UNKNOWN`,
`BUY_BLOCKED_AFTER_CLOSE`, `BUY_BLOCKED_BY_EOD_INCIDENT`, `BUY_BLOCKED_GATE_ERROR`(fail-closed).

**수동주문**(수동매매 패널, `manual_buy`)은 사람이 내린 결정이므로 이 게이트의 대상이 아니다.

## 3. 목적별 권한 게이트

| 목적 | 시간창 | confidence/quality | 일일 횟수 | 1회 금액 한도 |
|---|---|---|---|---|
| BUY_ENTRY | 09:05~14:30 | 적용 | 적용 | `kis_paper_auto_max_order_notional` |
| NORMAL_EXIT | 09:05~15:20 | 면제(실보유 청산) | 면제 | `kis_paper_auto_max_exit_order_notional` (0=보유수량이 상한) |
| EOD_FORCED_EXIT | 15:15~15:20 (+단일가 플래그) | 면제 | 면제 | 위와 동일 |

- 모든 SELL은 보유 수량 이내여야 한다(`SELL_QUANTITY_INVALID`). 무보유 매도, 초과 매도, 숏은 금지한다.
- RiskManager(P0)에서는 **보유 수량 이내의 reduce-only SELL**에 대해
  진입 notional 한도, 자본 대비 진입 한도, 일일 손실 한도의 차단을 적용하지 않는다.
  긴급정지는 SELL도 그대로 차단한다.
  - 이전에 있던 결함: 평가액이 100만원을 넘은 포지션은 손절·익절이 불가능했다.
    일일 손실 한도에 도달하면 손실 포지션도 청산할 수 없었다.

## 4. EOD coordinator (`app/kis_paper/eod_liquidation.py`)

상태: `IDLE → BUY_CUTOFF_ACTIVE → FLATTENING ⇄ WAITING_FOR_FILLS → FLAT_CONFIRMED | FLATTEN_INCOMPLETE`, 그리고 `BLOCKED`.

- **실행 주체**: `BackgroundTickDriver.run_forever`가 매 interval(기본 30초)마다 wall-clock으로 실행한다.
  루프 상태가 RUNNING/STOPPED/PAUSED인지와 무관하다. 프론트엔드 polling에도 의존하지 않는다.
  스캔 tick보다 먼저 같은 asyncio task에서 실행되므로 동시 매도가 생기지 않는다.
- **청산 대상**은 `min(프로그램 순보유, KIS 실보유)`이다.
  - 프로그램 순보유는 `kis_paper_auto` 체결 BUY에서 SELL을 뺀 값이다.
    오늘 주문분은 KIS 일별체결 수량으로 보정한다.
  - 수동 보유나 다른 모듈의 보유는 매도하지 않고 `unmanaged_holdings`로만 표시한다.
  - 원장 조회에 실패하면 `BLOCKED/EOD_OWNERSHIP_UNKNOWN`으로 처리한다(fail-closed).
- **주문 경로**: `execute_kis_paper_auto_order → route_order → RiskManager → PermissionGate →
  OrderExecutor → KisBrokerAdapter.place_order(is_paper=True)`. 우회하는 경로는 없다.
- **중복 방지**:
  1. DB lease (`eod_liquidation_state.lease_owner/expires_at`, TTL은 max(45초, 1.5×interval))
  2. 종목별 미체결 SELL 잔량이나 KIS에 아직 보이지 않는 EOD 주문이 있으면 추가 매도하지 않는다.
  3. 결정적 `client_order_id = eod-YYYYMMDD-SYMBOL-N`과 route_order idempotency
  4. 종목당 시도 상한 3회(RiskManager 거부 포함)
  5. 프로세스 내 asyncio.Lock
- **체결 확인**: `OrderResult.RECEIVED`는 청산 완료가 아니다.
  KIS 잔고(`inquire-balance`)와 일별체결 전 페이지(`inquire-daily-ccld` 연속조회, VTTC0081R)로만 판정한다.
- **최종 판정(15:30 이후)**: 다음이 모두 0이고 조회에 성공했을 때만 `FLAT_CONFIRMED`이다.
  - 프로그램 관리 보유
  - 관리 종목의 미체결 SELL
  - 프로그램의 미체결 BUY
  - KIS에서 아직 확인되지 않은 EOD 주문

  그 외에는 `FLATTEN_INCOMPLETE`로 기록하고 CRITICAL 이벤트를 남긴다.
  운영자가 확인할 때까지 **다음 거래일 신규 BUY를 차단**한다.
  판정 전에 서버가 꺼져 상태가 남은 경우(미판정)도 같은 방식으로 차단한다.
- **재시작 복구**: 상태, 시도 횟수, 주문번호는 DB에서 복원한다.
  15:15에 재시작하면 lease가 만료된 뒤(최대 45초) 새 프로세스가 이어서 처리하며, 기존 미체결 주문은 다시 보내지 않는다.
  15:22에 복구되면 단일가 구간이므로 기본 설정에서는 신규 주문 없이 대기·대조만 한다.
- **긴급정지(EMERGENCY_STOP)**: 주문 0, `BLOCKED/EOD_EMERGENCY_STOP_MANUAL_ACTION_REQUIRED`,
  CRITICAL 이벤트. 자동 청산을 하지 않는다(#37 Kill Switch 원칙). 운영자가 직접 정리한다.
- **STOP**: 신규 매수는 즉시 중단된다(스캔 tick 미실행).
  보유가 있으면 15:15 EOD 청산은 계속 수행한다(`KIS_PAPER_EOD_FLATTEN_WHEN_LOOP_STOPPED=true` 기본값).
  끄면 `BLOCKED/EOD_LOOP_STOPPED_FLATTEN_DISABLED`가 되어 운영자 책임으로 넘어간다.

## 5. 설정 (env, 안전 기본값)

| 변수 | 기본 | 의미 |
|---|---|---|
| `KIS_PAPER_AUTO_ORDER_WINDOW_END` | `14:30` | 신규 BUY 마감 (실효값 = min(이 값, 마감−60분)) |
| `KIS_PAPER_BUY_CUTOFF_MINUTES_BEFORE_CLOSE` | `60` | 마감 기준 매수 마감 오프셋 |
| `KIS_PAPER_EOD_FLATTEN_ENABLED` | `true` | EOD 강제청산 (실주문은 기존 opt-in이 모두 충족될 때만) |
| `KIS_PAPER_EOD_FLATTEN_MINUTES_BEFORE_CLOSE` | `15` | 강제청산 시작 오프셋 |
| `KIS_PAPER_EOD_CLOSING_AUCTION_ORDERS_ENABLED` | `false` | 15:20~15:30 신규 청산 주문 (모의 접수 미확인) |
| `KIS_PAPER_EOD_MAX_ATTEMPTS_PER_SYMBOL` | `3` | 종목당 EOD 매도 시도 상한 |
| `KIS_PAPER_EOD_FLATTEN_WHEN_LOOP_STOPPED` | `true` | 정지 중에도 청산 |
| `KIS_PAPER_AUTO_MAX_EXIT_ORDER_NOTIONAL` | `0` | 청산 1회 금액 한도 (0=보유수량이 상한) |
| `KRX_CALENDAR_OVERRIDE_PATH` | `` | 휴장일 override JSON |

LIVE 플래그(`ENABLE_LIVE_TRADING=false`, `KIS_IS_PAPER=true`, `ENABLE_AI_EXECUTION=false`)는 변경하지 않았다.

## 6. API / UI

- `GET /api/kis-paper/eod/status`: 일정, 단계, 신규매수 가능 여부와 사유, EOD 상태, 진행 요약,
  운용 모드, 가격·브로커 출처, 마지막 주문번호, 마지막 체결 감지 시각, 실패 코드, 마지막 대조 시각,
  background driver 동작 여부를 반환한다. broker는 호출하지 않는다.
- `POST /api/kis-paper/eod/acknowledge` `{trading_date, decided_by, note}`: 사고를 확인 처리해
  다음 날 BUY 차단을 해제한다. 사고 기록은 보존하며 주문은 내지 않는다.
- `POST /api/auto-paper/start` 응답에 `intraday_buy_allowed / _reason_code / _reason_message`가 포함된다.
- 홈(ReferenceHome)의 시작 버튼 아래 `IntradaySessionPanel`에 표시되는 항목:
  - 14:30 / 15:15 / 15:30 일정
  - 모의계좌 배지(가상 모드는 "가상 · KIS 주문 없음")
  - 운용 모드, 청산 진행률, EOD 상태, 출처
  - 마지막 주문번호·체결·실패·대조 시각
  - 사고 확인 버튼

## 7. quick / slow 시험과 종일 운용의 차이

- 설정 화면의 quick(3틱)과 slow(20틱)는 `KisPaperEngine`이 수행하는 **단발 점검**이다.
  기본적으로 dry-run이며 종일 운용이 아니다. EOD 청산도 수행하지 않는다.
- 종일 운용은 백엔드 상시 서비스(`BackgroundTickDriver`, `ENABLE_AI_PAPER_BACKGROUND_TICK=true`)가 담당한다.
- **PC 절전, 앱 종료, 네트워크 단절, 백엔드 다운이 15:15~15:20에 겹치면 청산이 실행되지 않는다.**
  이 경우 다음 단계에서 `FLATTEN_INCOMPLETE` 또는 미판정이 되고, 다음 날 BUY가 차단된다.

## 8. BLOCKER / 알려진 한계

1. **취소 주문 미구현**: `KisBrokerAdapter.cancel_order`는 여전히 `NotImplementedError`이다.
   - 공식 KIS 샘플에서 확인한 내용: 정정취소는 `/uapi/domestic-stock/v1/trading/order-rvsecncl`이고,
     모의 TR은 `VTTC0013U`, 실전 TR은 `TTTC0013U`이다.
   - 구현하려면 #40 OrderExecutor 단일 진입점 계약을 확장(취소용 sanctioned 경로, audit, 테스트)해야 하므로 별도 PR로 분리했다.
   - 현재 대기 BUY/SELL은 **취소하지 않고 보고만** 한다. 시장가 주문이라 연속매매 중에는 대기가 드물다.
2. **정정취소가능주문조회(`inquire-psbl-rvsecncl`, TTTC0084R)는 모의 TR이 없다.**
   미체결은 일별체결 전 페이지에서 잔량(`rmn_qty`)으로 계산한다.
3. **15:20~15:30 종가 단일가의 모의 시장가 접수 여부를 공식 문서로 확인하지 못했다.**
   그래서 기본값은 OFF이다. 15:15~15:20에 체결되지 않은 잔량은 그날 `FLATTEN_INCOMPLETE`가 될 수 있다.
4. 장 마감 후 KIS 일별체결에 소멸 잔량(`rmn_qty>0`)이 남아 보이면 보유가 0이어도 `FLATTEN_INCOMPLETE`가 된다.
   보수적인 판정이며, 운영자가 확인해서 해제한다.
5. 휴장일 달력 중 2027년분과 2026-11-19 수능일 세션은 잠정값이다.
6. lease TTL 때문에 15:15 직후 재시작하면 최대 45초 동안 청산이 지연된다.
7. 소유권 원장은 `filled_quantity`/KIS 체결수량에 의존한다.
   fill polling이 꺼진 상태에서 오늘 체결분은 KIS 일별체결로 보정하지만, 과거 미보정분은 원장에서 빠질 수 있다.

## 9. 운영자 실사용 리허설 (초보자용)

**사전 확인 (장 시작 전)**
1. `.env`(또는 `%APPDATA%\Autotrade\.env`)에 다음이 설정되어 있는지 확인한다:
   - `KIS_IS_PAPER=true`
   - `ENABLE_LIVE_TRADING=false`
   - `MARKET_DATA_PROVIDER=kis`
   - `ENABLE_AI_PAPER_BACKGROUND_TICK=true`
   - `ENABLE_KIS_PAPER_AUTO_TRADING=true`
2. 첫날은 `KIS_PAPER_AUTO_ORDER_DRY_RUN=true`(점검 모드, 주문 0)로 둔다.
   원하면 smoke(`KIS_PAPER_SMOKE_MODE=true`, 1종목·1주)로 시작한다.
3. 백엔드를 재시작한다. 홈의 "오늘 단타 일정" 패널에서 다음을 확인한다:
   - 신규매수 마감 14:30, 강제청산 시작 15:15, 정규장 종료 15:30
   - 배지가 **"모의투자 계좌"**인지
   - "백엔드 상시 실행기가 꺼져 있어" 경고가 **없는지**

**장중**
4. 09:05 이후 `▶ 자동매매 시작`을 누른다.
5. 14:30이 지나면 패널이 "⛔ 신규 매수 불가 — 신규 매수 마감…"으로 바뀌는지 확인한다.
6. 15:15부터 "청산 상태: 장마감 강제청산 진행 중"과 함께 남은 보유, 주문 접수, 체결 수가 바뀌는지 확인한다.
7. 15:30 이후 "당일 청산 완료 확인 (보유 0)"이 나오면 성공이다.
   한국투자증권 모의투자 앱이나 웹의 잔고에서도 0인지 직접 확인한다.

**사고 시**
- 즉시 멈추기: `🛑 긴급정지`. 모든 주문이 차단되고 자동 청산도 하지 않으므로, 남은 보유는 KIS 모의 앱에서 직접 정리한다.
- "청산 미완료 — 운영자 확인 필요"가 표시되는 경우:
  1. KIS 모의계좌 잔고와 미체결을 직접 확인한다.
  2. 필요하면 KIS 앱에서 직접 정리한다.
  3. 패널의 "확인했음 (매수 차단 해제)"을 누른다.
- dry-run 리허설로 이상이 없음을 확인한 뒤에만 `KIS_PAPER_AUTO_ORDER_DRY_RUN=false`로 바꾸고 재시작한다.
