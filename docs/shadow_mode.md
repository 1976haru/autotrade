# LIVE_SHADOW 운영자 가이드

`LIVE_SHADOW`는 실제 KIS 계좌에서 시세·잔고·포지션을 **읽기만** 하는 모드입니다. 신호 기록과 검증을 위한 단계로, 어떤 주문도 broker에 도달하지 않습니다. 본 문서는 dev 환경에서 SHADOW를 띄워 검증하는 절차입니다.

## 안전 보장 (다층 가드)

이 모드에서 절대 실주문이 일어나지 않는 이유:

1. **`RiskManager`** — `LIVE_SHADOW` 모드의 모든 주문 요청을 `REJECTED`로 종결합니다. `place_order`는 호출되지 않습니다.
2. **`KisBrokerAdapter.place_order`** — 호출되더라도 `NotImplementedError`를 던집니다 (defense in depth).
3. **`route_order` 단일 진입점** — HTTP 주문 라우트와 `LiveStrategyEngine.submit_tick` 모두 동일한 가드 체인을 통과합니다.

이 세 층은 독립적이므로 한 층이 깨져도 다른 층이 차단합니다.

## 사전 준비

### 1. KIS Developers 계정과 앱키 발급

1. <https://apiportal.koreainvestment.com/> 가입
2. **모의투자** 앱 등록 (실전 앱은 절대 사용하지 않음)
3. App Key, App Secret, 모의투자 계좌번호(10자리) 메모

### 2. 로컬 `.env` 작성

`backend/.env.example`을 복사해 `backend/.env`를 만들고 다음 값을 채웁니다.

```env
DEFAULT_MODE=LIVE_SHADOW
ENABLE_LIVE_TRADING=false      # SHADOW에서 의미 없지만 안전을 위해 그대로 false
ENABLE_AI_EXECUTION=false      # AI는 이번 단계에서 사용하지 않음
ENABLE_FUTURES_LIVE_TRADING=false

KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_앱시크릿
KIS_ACCOUNT_NO=1234567890       # 10자리, 8자리(CANO) + 2자리(상품코드)
KIS_IS_PAPER=true               # 모의투자 서버 사용
```

`.env`는 `.gitignore`에 등록되어 있어 커밋되지 않습니다. 키를 어떤 채팅·이슈·커밋 메시지에도 붙여 넣지 마세요.

### 3. backend 가동

```bash
cd backend
source .venv/bin/activate    # macOS / Linux
# .venv\Scripts\activate     # Windows
pip install -r requirements.txt   # 처음 한 번
alembic upgrade head              # 스키마가 있으면 no-op
uvicorn app.main:app --reload
```

부팅 직후 `/api/status`로 운용모드를 확인합니다.

```bash
curl http://127.0.0.1:8000/api/status
# {"app":"...","default_mode":"LIVE_SHADOW", ...}
```

## 검증 절차

### A. 시세 조회 (안전한 read-only)

```bash
curl http://127.0.0.1:8000/api/broker/price/005930
# {"symbol":"005930","price":<현재가>,"timestamp":"...","source":"kis"}
```

`source`가 `"kis"`로 표시되면 어댑터가 정상 라우팅된 것입니다. `mock`이라면 모드 환경변수를 점검하세요.

### B. 잔고/포지션 조회

```bash
curl http://127.0.0.1:8000/api/broker/balance
# {"cash":<예수금>, "equity":<총평가>, "buying_power":..., "currency":"KRW"}

curl http://127.0.0.1:8000/api/broker/positions
# [{"symbol":"...","quantity":...,"avg_price":...,"market_price":...}, ...]
```

빈 모의계좌라면 `cash=0`, `positions=[]`이 정상입니다.

### C. 주문이 차단되는지 확인

```bash
curl -X POST http://127.0.0.1:8000/api/broker/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol":"005930","side":"BUY","quantity":1}'

# HTTP 400, body:
# {"detail":{"decision":"REJECTED","reasons":["LIVE_SHADOW records signals only; live orders disabled"]}}
```

이 응답이 나오지 않으면(예: 200) 즉시 backend를 중단하고 환경변수 / 운용모드 / 코드 변경 이력을 점검하세요.

### D. 감사 로그

차단된 주문도 audit 테이블에 기록됩니다.

```bash
curl 'http://127.0.0.1:8000/api/audit/orders?limit=5'
# 가장 최근 항목의 mode="LIVE_SHADOW", decision="REJECTED", executed=false
```

frontend `📜 로그` 탭 → `주문` 서브탭에서도 동일한 내용이 보입니다.

### E. (선택) 프론트엔드 차트 확인

frontend를 띄우면 `📈 차트` 탭에서 KIS 시세를 그릴 수 있습니다(같은 `MARKET_DATA_PROVIDER` 설정의 영향을 받음 — 차트는 KIS가 아닌 시장 데이터 어댑터를 통합니다).

```bash
cd frontend
npm ci
npm run dev    # http://localhost:5173
```

## ShadowTrade 추정 기록 (#43)

`LIVE_SHADOW` 모드에서 RiskManager가 모든 주문을 `REJECTED`로 종결하는 것은 변경되지 않습니다. 그 위에 `shadow_trade` 테이블이 *추가*되어 운영자가 “실 시세에서 다른 가드까지 다 통과한 후보였다면”을 사후 분석할 수 있게 합니다.

### 기록되는 필드 (요약)

| 필드 | 의미 |
|---|---|
| `audit_id` | 같은 주문의 `OrderAuditLog.id` (cross-reference) |
| `would_have_decision` | LIVE_SHADOW gate 외 다른 reason이 0건이면 `APPROVED`, 그 외는 `REJECTED` |
| `would_have_reasons` | 다른 가드(긴급정지/포지션 한도/시세 stale 등)의 reason. LIVE_SHADOW gate reason은 제외 |
| `latest_price` | 시세 timestamp 검사 통과한 현재가 |
| `estimated_fill_price` | 추정 체결가 — 본 PR은 `latest_price` 그대로 사용 (`estimation_method=latest_price_proxy`) |
| `estimated_slippage_bps` | 0.0 (proxy 모델은 슬리피지를 가정하지 않음) |
| `actual_broker_order_sent` | **invariant False** — broker.place_order는 호출되지 않음. 테스트로 강제 |
| `confidence_note` | "추정치 — 실 체결과 다를 수 있다 (orderbook depth / 부분체결 / 호가 공백 / 슬리피지 미반영)" |

### 실제 주문과의 차이

`shadow_trade` row는 **주문이 아닙니다**. broker.place_order는 호출되지 않으며, 다음 항목은 추정에 반영되지 않습니다:

- **Orderbook depth** — 호가창 두께 / 1호가 매물 수량
- **호가 공백** — 호가 단위 사이의 점프
- **부분체결** — `filled_quantity < quantity`로 끝나는 주문
- **슬리피지** — 시장 충격, taker fee, time-in-force, IOC 거절 등

따라서 `would_have_decision=APPROVED` + `estimated_fill_price=latest_price` row가 많다고 해서 “실거래에서 동일하게 체결되었을 것”이라고 결론 내리지 마세요. 실 체결 품질은 PAPER 단계 + LIVE_MANUAL_APPROVAL 단계에서 reconciliation으로 별도 측정합니다.

### 조회 API (read-only, broker 호출 0건)

```bash
# 목록 (created_at desc)
curl 'http://127.0.0.1:8000/api/shadow/trades?limit=50'
curl 'http://127.0.0.1:8000/api/shadow/trades?symbol=005930'
curl 'http://127.0.0.1:8000/api/shadow/trades?would_have_decision=APPROVED'

# 요약 (Dashboard 카드에서 호출)
curl 'http://127.0.0.1:8000/api/shadow/summary'
# {
#   "total": 12,
#   "would_have_approved_count": 9,
#   "would_have_rejected_count": 3,
#   "by_strategy": {"sma": 12},
#   "avg_estimated_slippage_bps": 0.0,
#   "actual_broker_orders_sent": 0,            ← invariant: 항상 0
#   "invariant_note": "LIVE_SHADOW 기록은 실제 주문이 아닙니다…"
# }
```

`actual_broker_orders_sent`가 **0이 아니면 즉시 incident**입니다. 본 카운트가 1 이상이면 LIVE_SHADOW 가드 체인이 깨졌다는 의미이므로, backend를 즉시 중단하고 다음을 점검:

1. `app/risk/risk_manager.py` — LIVE_SHADOW 분기에서 `SHADOW_RECORD_ONLY_REASON`을 누적하는지
2. `app/execution/executor.py` — `OrderExecutor._EXECUTABLE_DECISIONS`에 REJECTED가 포함되어 있지 않은지
3. `app/brokers/kis.py::place_order` — `is_paper=False`에서 `NotImplementedError`를 던지는지

### Frontend Shadow Summary 카드

Dashboard 상단 (PC/모바일 공통)에 다음 정보를 표시합니다:

- 총 기록 수 / would-have 통과 / 다른 가드 거부 (3개 타일)
- `actual_broker_orders_sent`: 0이 아니면 붉은색 강조 + “invariant 위반”
- 평균 추정 슬리피지 (bps)
- “실제 주문 아님” 배지 + “LIVE_SHADOW: 실제 주문 없이 신호만 기록” disclaimer

자세한 정책: [`live_shadow_trade_policy.md`](live_shadow_trade_policy.md).

## 알려진 한계

- `cancel_order`, `get_order_status`는 `NotImplementedError` (다음 PR에서 SHADOW 모드용 구현 추가 예정).
- 1초 단위 실시간 시세 폴링은 미지원 — 호출할 때마다 KIS REST를 직접 친다.
- 토큰은 24시간 유효, 어댑터가 자동 갱신.
- KIS 서버 점검 시간(보통 평일 06:00–07:00, 주말)에는 응답이 비정상일 수 있다.

## 사고 대응

| 증상 | 대응 |
|---|---|
| `/api/status`가 `LIVE_SHADOW`가 아님 | `.env`의 `DEFAULT_MODE` 확인, uvicorn 재시작 |
| 시세 응답 `source: "mock"` | KIS adapter가 활성화 안 됨 — 모드 + KIS 키 점검 |
| 주문이 200으로 통과 | 즉시 종료, `RiskManager.evaluate_order` 동작 점검, **운영 환경에는 절대 배포하지 말 것** |
| KIS 401 / token 오류 | App Key/Secret 오타, 모의/실전 환경 혼동 (KIS_IS_PAPER) |
| KIS 403 / 잔고 조회 실패 | 계좌번호 길이/형식 확인 (10자리 = 8 + 2) |

## SHADOW 다음 단계

이 모드에서 충분히 검증된 후 단계적으로 승격:

1. **`PAPER`** — KIS 모의 주문 실제 라우팅 (가상 자금)
2. **`LIVE_MANUAL_APPROVAL`** — 실계좌 주문 + 사용자 승인 큐
3. **`LIVE_AI_ASSIST`** — AI 추천 + 사용자 승인
4. **`LIVE_AI_EXECUTION`** — AI 자동 실행 (별도 옵트인 후)

각 단계는 별도 PR + 운영자 검토를 거칩니다 (`docs/promotion_policy.md` 참조).
