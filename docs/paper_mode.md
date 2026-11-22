# PAPER 모드 운영자 가이드

`PAPER`는 KIS 모의투자(가상 자금) 계좌로 **실제 주문을 보내는** 모드입니다. SHADOW의 read-only 검증을 마친 뒤 다음 단계로 사용합니다. 실제 자금이 빠져나가지는 않지만 KIS API 호출과 주문 흐름은 모두 실 운영과 동일합니다.

> SHADOW가 아직 익숙하지 않다면 먼저 [`docs/shadow_mode.md`](shadow_mode.md)를 따라 read-only 검증을 끝내세요.

## SHADOW vs PAPER 차이

| 항목 | LIVE_SHADOW | PAPER |
|---|---|---|
| 시세/잔고/포지션 조회 | ✓ KIS 모의 서버 | ✓ KIS 모의 서버 |
| 주문 (place_order) | ✗ RiskManager가 거부 | ✓ KIS 모의 서버에 전송 |
| 자금 이동 | 없음 | 가상 자금 |
| RiskManager 검증 | 모드 사유로 즉시 REJECTED | 일반 검증 (notional, cash, position 등) |
| OrderAuditLog | REJECTED 기록 | RECEIVED → 후속 체결 조회로 갱신 |
| 적합한 사용자 행동 | 신호/잔고만 관찰 | 실제 주문 흐름 검증 |

## 다층 안전 가드 (PAPER 한정)

PAPER 모드 주문이 KIS 라이브 서버로 잘못 흘러가지 않도록 세 층의 가드:

1. **`get_broker()` 팩토리** — `DEFAULT_MODE=PAPER`인데 `KIS_IS_PAPER=false`면 시작 거부 (`RuntimeError`).
2. **`KisClient.place_order`** — `tr_id`가 `is_paper`에 따라 별개 (`VTTC0802U`/`VTTC0801U` paper, `TTTC0802U`/`TTTC0801U` live). paper 클라이언트는 live tr_id를 보낼 수 없음.
3. **`KisBrokerAdapter.place_order`** — `is_paper=False`이면 호출 시 `NotImplementedError` (라이브 라우팅은 `LIVE_MANUAL_APPROVAL` PR에서 별도 wire).

## 사전 준비

SHADOW 가이드와 동일하게 KIS 모의투자 앱 등록 + 키 발급. `backend/.env`만 다음과 같이:

```env
DEFAULT_MODE=PAPER              # 핵심: PAPER로 변경
ENABLE_LIVE_TRADING=false       # PAPER에서는 의미 없음 (false 유지 권장)
ENABLE_AI_EXECUTION=false
ENABLE_FUTURES_LIVE_TRADING=false

KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_앱시크릿
KIS_ACCOUNT_NO=1234567890       # 모의투자 계좌 10자리
KIS_IS_PAPER=true               # 필수 — false면 backend 시작 자체가 차단됨
```

backend 가동:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

`/api/status`에서 `default_mode: "PAPER"` 확인.

## 검증 절차

### A. 시세·잔고는 SHADOW와 동일

```bash
curl http://127.0.0.1:8000/api/broker/price/005930
curl http://127.0.0.1:8000/api/broker/balance
curl http://127.0.0.1:8000/api/broker/positions
```

### B. 작은 주문으로 흐름 끝까지 통과 확인

`RiskPolicy` 기본 한도(`max_order_notional=1_000_000`) 안에 들어오는 작은 매수로 시작하세요.

```bash
curl -X POST http://127.0.0.1:8000/api/broker/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol":"005930","side":"BUY","quantity":1}'
```

기대 결과:

- HTTP 200
- 응답 body의 `status` = `"RECEIVED"` (KIS는 접수만 동기 응답, 체결은 비동기)
- 응답에 `order_id`(KIS ODNO) 포함

### C. 후속 체결 조회

```bash
# audit log: 방금 주문이 REJECTED가 아닌 RECEIVED + executed=true로 기록되어야 함
curl 'http://127.0.0.1:8000/api/audit/orders?limit=3'
```

KIS 모의서버에서 체결이 일어나면 `KisBrokerAdapter.get_order_status(order_id)`로 채울 수 있습니다. 현재 자동 폴링은 미구현 — 다음 PR에서 audit 갱신 자동화 예정.

### D. 리스크 한도 초과 주문이 차단되는지

```bash
# 50주 BUY @ 75,000 = 3.75M (max_order_notional=1M 초과)
curl -X POST http://127.0.0.1:8000/api/broker/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol":"005930","side":"BUY","quantity":50}'
```

기대: HTTP 400 + `{"detail":{"decision":"REJECTED","reasons":["order notional exceeds max_order_notional", ...]}}`. KIS에는 도달하지 않음 (audit에는 REJECTED 기록).

### E. KIS 포털에서 교차 확인

KIS Developers/모의투자 포털에서 같은 계좌의 주문 내역을 보면, B에서 보낸 주문이 동일한 ODNO로 표시되어야 합니다. 표시되지 않으면 환경 설정(특히 `KIS_IS_PAPER`, `KIS_ACCOUNT_NO`)을 점검하세요.

## 알려진 한계

- **체결 자동 갱신 미지원** — 현재는 주문 시 RECEIVED만 audit에 기록됩니다. 운영자가 `get_order_status(order_id)` 또는 audit 조회를 수동으로 실행해 갱신 필요.
- **`cancel_order` 미구현** — KIS 정정/취소(`order-rvsecncl`)는 다음 PR. 모의에서 잘못 보낸 주문은 KIS 포털에서 직접 취소.
- **부분 체결, 정정 주문** — 현재 어댑터는 단순 매수/매도 시장가/지정가만 지원. 조건부 주문은 추후.
- **시장 외 시간** — 점검 시간(평일 06:00–07:00, 주말)에는 응답 비정상.

## 사고 대응

| 증상 | 대응 |
|---|---|
| backend 시작 시 `RuntimeError: KIS_IS_PAPER=true` | `.env`의 `KIS_IS_PAPER=true` 누락 — 수정 후 재기동 |
| 주문이 항상 REJECTED | 주문 수량/금액이 RiskPolicy 한도 초과 → 수량 축소 또는 한도 조정 |
| 응답은 RECEIVED인데 KIS 포털엔 없음 | 계좌번호 8+2 분할 오류 또는 모의/실전 환경 혼동 (`KIS_IS_PAPER`) |
| 401/403 KIS 인증 오류 | App Key/Secret 오타, 모의투자 앱이 아닌 실전 앱 키 사용 |
| `place_order` 호출 시 `NotImplementedError: LIVE_MANUAL_APPROVAL` | 어댑터가 `is_paper=False`로 잘못 생성됨 — 환경변수 점검 |

## 홈 "자동매매 시작" 준비 점검 (fix/autopaper-start-readiness)

승인 팝업을 띄우기 전에 `GET /api/auto-paper/run-readiness`의 `order_blockers`를 확인하고,
승인 API(`POST /api/auto-paper/paper-session-approval`)도 같은 조건을 다시 검사한다.
사유가 하나라도 있으면 승인은 `409 ORDER_PATH_NOT_READY:<codes>`로 거부되고 홈에 원인이 표시된다.

| 사유 코드 | 조건 | 조치 |
|---|---|---|
| `MARKET_DATA_PROVIDER_NOT_KIS` | `MARKET_DATA_PROVIDER` ≠ `kis` | `%APPDATA%\Autotrade\.env`에서 `kis`로 바꾸고 재실행 |
| `BACKGROUND_TICK_DISABLED` | `ENABLE_AI_PAPER_BACKGROUND_TICK=false` | `true` |
| `KIS_CREDENTIALS_MISSING` | 앱키/시크릿/계좌번호 중 빈 값 | 모의투자 키 입력 |
| `FILL_POLLER_DISABLED` | `KIS_PAPER_FILL_POLLING=false` | `true` |
| `KIS_PAPER_SMOKE_MODE_ON` | `KIS_PAPER_SMOKE_MODE=true` | `false` |

`POST /api/auto-paper/start` 응답에는 `order_mode`와 `order_blockers`가 함께 온다. `order_mode`가
`KIS_REALTIME_PAPER_AUTO`가 아니면 홈에 "가상 전용 — KIS 주문 안 나감" 배지가 표시된다.

- **휴장일:** `market_clock.current_market_phase`가 KRX 휴장일 달력을 반영한다. 휴장일은 `WEEKEND`와 똑같이 휴장으로 처리하고, 특수 세션(지연 개장)은 달력의 개장·마감 시각을 따른다.
- **다음 거래일 대기:** 장 마감 후·주말·휴장일에는 승인 대상이 `GET /api/auto-paper/next-trading-day`(KRX 달력 기준)가 된다. 그 날짜로 승인한 뒤 시작하면 루프가 `WAITING_MARKET`(`armed_for`=대상일)으로 기다렸다가 그날 09:00에 `RUNNING`이 된다. 대상일을 놓치면 `MARKET_CLOSED`가 된다. 대기는 주문 권한이 아니다. `authorize_order`가 `target_date == 오늘`을 그대로 검사하므로 대기 중 주문은 0건이다. 승인 가능 날짜는 오늘 또는 다음 거래일뿐이다.
### 세션 상태와 재시작 fail-closed 정책

| 승인 상태 | 신규 BUY | 청산 SELL·EOD | 해제 |
|---|---|---|---|
| `SCHEDULED` / `ACTIVE` | 가능 | 가능 | — |
| `EXIT_ONLY` | **차단** (`EMERGENCY_STOP_SESSION_BLOCKED` / `RESTART_REAPPROVAL_REQUIRED`) | 가능 (기존 SELL 검증 그대로: KIS 보유·주문가능수량, 프로그램 소유 등) | 새 **명시적 승인**만 |
| `BLOCKED` 등 | 차단 | 차단 (기존 의미) | — |

- **긴급정지:** `POST /api/auto-paper/emergency-stop`은 대기를 풀고 세션을 `EXIT_ONLY`로 바꿔 **승인 파일과 운영 DB 양쪽에** 저장한다. 응답에는 `session_block`과 `session_block_persisted`가 담긴다.
  - 현재 프로세스는 저장 결과와 상관없이 즉시 신규 BUY를 막는다.
  - 저장이 양쪽 모두 실패하면 CRITICAL 로그를 남긴다. 이때도 아래 재시작 정책 때문에 자동 복구는 일어나지 않는다.
- **충돌 시 제한 우선:** `load_approval`은 파일과 DB(같은 날짜·계좌) 중 더 제한적인 상태를 쓴다. 순서는 `BLOCKED` > `EXIT_ONLY` > `SCHEDULED/ACTIVE`이며, BUY 차단 표식은 어느 한쪽에만 있어도 적용된다. 파일이 삭제·손상되면 DB에서 그대로 복구한다.
- **신규 BUY의 DB 대조:** `session_trading_authorized`와 `authorize_order(BUY)`는 DB를 조회하지 못하거나 DB가 파일보다 제한적이면 거부한다(`APPROVAL_DB_UNVERIFIED`). DB의 차단 기록이 조회 실패로 가려지고 파일의 오래된 `SCHEDULED`만 보이는 경우를 막기 위해서다.
- **재시작 자동 복구는 일회용 허가가 있을 때만:** 허가(`paper-session-restore-permit.json`, 승인 파일과 같은 폴더)는 **정상 종료** 때만 lifespan 종료 처리에서 기록된다. 기록 조건은 루프가 `RUNNING`/`WAITING_MARKET`이고, 프로세스 BUY 차단이 없고, 세션이 신규 BUY 가능 상태이며 DB 대조에 성공한 경우다.
  - 다음 시작 때 허가를 읽고 즉시 삭제한다. 허가가 현재 승인(날짜·계좌·승인시각)과 일치하고 DB 대조가 성공해야만 복구한다.
  - 크래시, 강제 종료, 긴급정지 저장 실패, 허가 손상·불일치, DB 조회 실패 시에는 복구하지 않는다. 세션은 `EXIT_ONLY`로 바뀌고 그 프로세스는 신규 BUY를 막는다. 청산과 EOD는 계속되며, 다시 매수하려면 홈에서 재승인해야 한다.
  - 사용자가 멈춘 루프(PAUSED/STOPPED)는 정상 종료해도 자동 재개하지 않는다.

## PAPER 다음 단계

PAPER로 충분한 검증을 마친 후:

1. **`LIVE_MANUAL_APPROVAL`** — 실계좌 + 사용자 승인 큐 (PermissionGate 통과 필수). KIS_IS_PAPER=false로 전환되며, 한 번 승인된 주문만 broker에 도달.
2. **`LIVE_AI_ASSIST`** — AI 추천 + 사용자 승인.
3. **`LIVE_AI_EXECUTION`** — 별도 옵트인 후에만 활성. CLAUDE.md에 따라 기본 비활성화.

각 단계 전환은 별도 PR + 운영자 검토를 거칩니다 (`docs/promotion_policy.md`).
