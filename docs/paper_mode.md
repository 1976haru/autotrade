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

## PAPER 다음 단계

PAPER로 충분한 검증을 마친 후:

1. **`LIVE_MANUAL_APPROVAL`** — 실계좌 + 사용자 승인 큐 (PermissionGate 통과 필수). KIS_IS_PAPER=false로 전환되며, 한 번 승인된 주문만 broker에 도달.
2. **`LIVE_AI_ASSIST`** — AI 추천 + 사용자 승인.
3. **`LIVE_AI_EXECUTION`** — 별도 옵트인 후에만 활성. CLAUDE.md에 따라 기본 비활성화.

각 단계 전환은 별도 PR + 운영자 검토를 거칩니다 (`docs/promotion_policy.md`).
