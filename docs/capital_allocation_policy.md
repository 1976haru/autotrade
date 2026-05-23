# Paper 자본 배분 정책 — Seed Money + 종목당 투자금 (P-시리즈)

> 본 문서는 AI Paper 모의매매에서 *가상 자본* 을 어떻게 배분하는지의 단일
> 진실. 실전 계좌와 *완전히 분리* 된 가상 자본 — 어떤 broker / OrderExecutor /
> route_order / KIS 실거래 한도와도 결합되지 않는다.

## 1. P-01 — 초기 시드머니 (본 PR)

### 1-1. 허용 옵션

| 옵션 | KRW 정수 | 한국어 라벨 |
|---|---|---|
| 1 | `10_000_000` | 1,000만원 (기본값) |
| 2 | `30_000_000` | 3,000만원 |
| 3 | `50_000_000` | 5,000만원 |

위 3종 외 값은 *허용되지 않는다* — caller 는 `InvalidPaperCapitalError` 로
거부되거나, `fallback_to_default=True` 시 default (1,000만원) 로 대체된다.

### 1-2. 통화

KRW 한정 (P-01 시점). USD / JPY 등 타 통화는 별도 PR.

### 1-3. 영구 저장

P-01 시점 *in-memory* 만 — 프로세스 재시작 시 default 로 복귀. DB / settings
영구화는 **P-16** 에서 별도 PR.

### 1-4. 절대 invariant

| 항목 | 값 |
|---|---|
| `PaperCapitalConfig.is_paper_only` | `True` (영구 — 변경 시 ValueError) |
| `PaperCapitalConfig.is_live_authorization` | `False` (영구) |
| `PaperCapitalConfig.currency` | `"KRW"` (영구) |
| capital_config 모듈 import 대상 | broker / OrderExecutor / KIS / 외부 HTTP / AI SDK / `app.core.config.get_settings` 어느 것도 0건 |
| `.enable_live_trading = ...` mutation | 0건 |
| `broker.place_order(` / `route_order(` 호출 | 0건 |

### 1-5. 코드 위치

| 항목 | 경로 |
|---|---|
| 모듈 | `backend/app/auto_paper/capital_config.py` |
| Test | `backend/tests/test_paper_capital_config.py` (32 PASS) |
| API GET | `GET /api/auto-paper/capital-config` |
| API POST | `POST /api/auto-paper/capital-config` |
| Frontend | `frontend/src/components/common/PaperCapitalCard.jsx` |
| Frontend test | `frontend/src/components/common/PaperCapitalCard.test.jsx` (11 PASS) |

### 1-6. API 응답 contract

`GET /api/auto-paper/capital-config`:

```json
{
  "initial_cash":                  10000000,
  "allowed_initial_cash_options":  [10000000, 30000000, 50000000],
  "currency":                      "KRW",
  "is_paper_only":                 true,
  "is_live_authorization":         false,
  "updated_at":                    "2026-05-21T01:23:45.678+00:00",
  "notice":                        "Paper 시드머니는 *모의매매 전용* ..."
}
```

`POST /api/auto-paper/capital-config` 입력:

```json
{
  "initial_cash":         30000000,
  "fallback_to_default":  false
}
```

응답: 위 GET 응답 + `"fallback_used": false`. 허용되지 않은 값 + `fallback_to_default=false` → **400**.

### 1-7. UI 동작

`PaperCapitalCard` 카드에 다음을 항상 노출:

- 3개 옵션 button (선택된 옵션은 `data-selected="true"`)
- "이 값은 모의매매 전용이며 실전 계좌와 무관합니다" disclaimer
- 현재 설정 + KRW 정수 + 통화
- 영구 invariant 배지 3종 (`Paper 전용` / `실거래 OFF 유지` / `주문 권한 없음`)
- *임의 KRW 입력 form 0개* — 허용 옵션만 chip 선택. 잘못된 값 입력 차단.

## 2. P-02 — 종목당 최대 투자금 (본 PR 추가)

종목당 가상 매수 한도를 두 방식으로 설정 가능.

### 2-1. 허용 모드

| 모드 | 의미 | 입력 필드 |
|---|---|---|
| `FIXED_KRW` | 절대 KRW 한도 | `per_symbol_max_krw` |
| `PCT_OF_EQUITY` | 시드머니 대비 비율 | `per_symbol_max_pct` |

### 2-2. 허용 값

| 모드 | 옵션 |
|---|---|
| `FIXED_KRW` | `1_000_000` (**기본값**, 100만원) / `2_000_000` (200만원) |
| `PCT_OF_EQUITY` | `0.10` (10%) |

기본 모드 = `FIXED_KRW`. 기본 effective cap = 1,000,000 KRW.

### 2-3. effective_per_symbol_cap_krw 계산

```
FIXED_KRW          → per_symbol_max_krw 그대로
PCT_OF_EQUITY      → floor(initial_cash * per_symbol_max_pct)
```

예시 (PCT_OF_EQUITY + 0.10):

| 시드머니 | effective cap |
|---|---|
| 10,000,000 (1,000만원) | 1,000,000 (100만원) |
| 30,000,000 (3,000만원) | 3,000,000 (300만원) |
| 50,000,000 (5,000만원) | 5,000,000 (500만원) |

### 2-4. API contract

`GET /api/auto-paper/capital-config` 응답에 P-02 신규 필드 추가:

```json
{
  "initial_cash":                       10000000,
  "per_symbol_mode":                    "FIXED_KRW",
  "per_symbol_max_krw":                 1000000,
  "per_symbol_max_pct":                 0.10,
  "effective_per_symbol_cap_krw":       1000000,
  "allowed_per_symbol_max_krw_options": [1000000, 2000000],
  "allowed_per_symbol_max_pct_options": [0.10],
  "is_paper_only":                      true,
  "is_live_authorization":              false
}
```

`POST /api/auto-paper/per-symbol-allocation` 입력 (partial update — None 필드는 보존):

```json
{
  "mode":                "PCT_OF_EQUITY",
  "per_symbol_max_krw":  null,
  "per_symbol_max_pct":  0.10,
  "fallback_to_default": false
}
```

허용되지 않은 값 + `fallback_to_default=false` → **400** `invalid_per_symbol_allocation`
+ `allowed_modes` / `allowed_per_symbol_max_krw_options` /
`allowed_per_symbol_max_pct_options` carry.

### 2-5. UI 동작 (`PaperCapitalCard` 확장)

별도 섹션 "📌 종목당 최대 투자금" 노출:
- 3개 옵션 chip — **100만원** / **200만원** / **시드머니의 10%**
- 현재 적용 한도 (effective_per_symbol_cap_krw, KRW + 한국식 만원)
- "이 값은 Paper 모의매매 전용이며 실전 주문금액이 아닙니다" disclaimer
- 시드머니가 변경되면 PCT 모드의 hint 텍스트가 *자동* 갱신 (예: "현재 시드머니
  3,000만원 × 10%")
- input / textarea / select 0개 — 임의 KRW / % 입력 form 차단

### 2-6. PositionSizingPolicy 와의 결합 (설계 메모)

`PositionSizingPolicy.max_position_krw` (default 5,000,000) 와는 *별도* 변수.
caller (예: `consume_agent_recommendations`) 가 다음 두 값 중 **더 보수적인**
(min) 을 적용해 sizing 결정:

```python
effective_cap = min(
    paper_cfg.effective_per_symbol_cap_krw,   # P-02
    sizing_policy.max_position_krw,           # 기존 policy
)
```

본 결합 적용은 별도 PR (P-시리즈 후속) — 본 PR 은 *config 노출만*. 기존 sizing
경로는 변경 0건.

### 2-7. 안전 invariant (P-02 추가분)

| 항목 | 값 |
|---|---|
| `per_symbol_max_krw not in ALLOWED_PER_SYMBOL_MAX_KRW` | ValueError / 400 거부 |
| `per_symbol_max_pct not in ALLOWED_PER_SYMBOL_MAX_PCT` | ValueError / 400 거부 |
| `per_symbol_mode` not in `PerSymbolAllocationMode` | ValueError / 400 거부 |
| `is_paper_only` / `is_live_authorization` | P-01 invariant 그대로 유지 |
| capital_config 모듈 import (broker / OrderExecutor / KIS / settings 등) | 0건 유지 |
| `broker.place_order(` / `route_order(` 호출 | 0건 유지 |
| frontend per-symbol 섹션의 BUY/SELL/Place Order/매수/매도 라벨 | 0개 |
| frontend per-symbol 섹션의 input/textarea/select | 0개 |

## 3. 사용자 흐름 예시 (P-01 동작 확인)

```
1. EXE 실행 → Dashboard → "Paper 시드머니" 카드 노출
2. 기본 1,000만원 선택됨
3. 사용자가 "5,000만원" 클릭
4. backend POST /api/auto-paper/capital-config { initial_cash: 50_000_000 }
5. 응답: initial_cash=50000000, fallback_used=false
6. 카드 요약에 "현재 Paper 시드머니: 5,000만원 (KRW)" 표시
7. AI Paper Auto Loop 가 매수 판단 시 본 값을 `account_equity` 로 사용
   (P-02 종목당 투자금이 이 값을 기준으로 sizing 결정)
8. broker / KIS / 실거래 호출 0건
9. 앱 재시작 시 default 1,000만원 으로 복귀 (P-16 에서 영구화)
```

## 3-A. P-03 — 최대 동시 보유 종목 수 제한 (본 PR 추가)

과도한 분산 / 과도한 동시 진입 / 자금 과다 사용을 막는 *Paper 전용* 안전장치.

### 3-A-1. 허용 옵션

| 옵션 | 의미 |
|---|---|
| `3` | **기본값** — 최대 3종목 동시 보유 |
| `5` | 최대 5종목 동시 보유 |
| `10` | 최대 10종목 동시 보유 |

위 3종 외 값 → `InvalidMaxConcurrentPositionsError` (또는 fallback=true 시
default 로 대체).

### 3-A-2. 가드 로직 (`check_concurrent_buy_allowed`)

`app/auto_paper/concurrent_positions_guard.py` 의 순수 함수.

| 입력 action | 입력 symbol vs held | 결과 |
|---|---|---|
| `BUY` | 신규 종목 + 보유 수 < 한도 | `ALLOW` |
| `BUY` | 신규 종목 + 보유 수 ≥ 한도 | `BLOCKED_MAX_POSITIONS` |
| `BUY` | 이미 보유 중 (`symbol in held`) | `ALLOW` (`is_existing_position=True`) |
| `SELL` / `EXIT` / `HOLD` / `WATCH` 등 | (any) | `SKIP_NON_BUY` |

특징:
- HOLD / SELL / EXIT 는 *제한 없음* — 청산 방향은 종목 수 한도와 무관.
- 추가 매수 (같은 종목) 는 *고유 종목 수 변화 없음* → 항상 ALLOW.
- 결과 dataclass `ConcurrentBuyCheckResult`:
  - `is_order_signal=False` / `is_live_authorization=False` / `is_paper_only=True` 영구.
  - broker / route_order 호출 0건 — *advisory* 평가만.
- 동일 종목 중복 entry 가 `current_held_symbols` 에 들어와도 set 으로 dedupe.

### 3-A-3. EMERGENCY_STOP 처리

본 가드 *밖* 의 책임. AutoPaperLoop / consumer 가 `EMERGENCY_STOP` 상태이면
어떤 신규 판단도 진행하지 않으므로 본 가드 호출조차 발생하지 않는다 (정책 +
별도 테스트로 lock).

### 3-A-4. API contract

`GET /api/auto-paper/capital-config` 응답에 P-03 신규 필드:

```json
{
  "max_concurrent_positions":                  3,
  "allowed_max_concurrent_positions_options":  [3, 5, 10]
}
```

`POST /api/auto-paper/max-concurrent-positions` 입력:

```json
{
  "max_concurrent_positions": 5,
  "fallback_to_default":      false
}
```

허용 외 + `fallback_to_default=false` → **400** `invalid_max_concurrent_positions`.

`POST /api/auto-paper/max-concurrent-positions/preview` (advisory):

입력:
```json
{
  "action":               "BUY",
  "symbol":               "005930",
  "current_held_symbols": ["000660", "035720"]
}
```

응답 (200, advisory):
```json
{
  "verdict":                     "ALLOW",
  "current_unique_symbol_count": 2,
  "max_concurrent_positions":    3,
  "symbol":                      "005930",
  "action":                      "BUY",
  "reason":                      "신규 종목 BUY 허용 (current_unique=2 < 3)",
  "is_existing_position":        false,
  "is_order_signal":             false,
  "is_live_authorization":       false,
  "is_paper_only":               true
}
```

### 3-A-5. UI 동작 (`PaperCapitalCard` 확장)

별도 섹션 "📊 최대 동시 보유 종목" 노출:
- 3개 옵션 chip — **3종목** / **5종목** / **10종목**
- 현재 설정값 표시
- "이 값은 Paper 모의매매 전용이며 실전 주문 한도가 아닙니다" disclaimer
- "한도 도달 시 신규 진입만 차단 — 청산은 자유" 안내
- input / textarea / select 0개 — 임의 입력 form 차단

### 3-A-6. 안전 invariant (P-03 추가분)

| 항목 | 값 |
|---|---|
| `max_concurrent_positions not in ALLOWED_MAX_CONCURRENT_POSITIONS` | ValueError / 400 거부 |
| `ConcurrentBuyCheckResult.is_order_signal` | False 영구 |
| `ConcurrentBuyCheckResult.is_live_authorization` | False 영구 |
| `ConcurrentBuyCheckResult.is_paper_only` | True 영구 |
| capital_config / concurrent_positions_guard 모듈 import | broker / OrderExecutor / KIS / settings / 외부 HTTP / AI SDK 0건 |
| frontend section BUY / SELL / Place Order / 매수 / 매도 / 실거래 라벨 | 0개 |
| frontend section input / textarea / select | 0개 |

## 3-B. P-04 — Paper BUY affordability 체크 (본 PR 추가)

종목 1주 가격이 종목당 투자금 한도보다 비싸거나, 남은 Paper 현금이 1주
가격보다 적으면 AI Paper BUY 후보에서 자동 제외하는 *advisory* 사전 검사.

예시 (사용자 요청서):
- 종목당 한도 100만원 + SK하이닉스 1주 1,800,000원 → 1주도 살 수 없으므로
  `PRICE_OVER_CAP` (BUY 차단).

### 3-B-1. AffordabilityVerdict (8종)

| verdict | 의미 | 차단 |
|---|---|---|
| `AFFORDABLE` | 1주 이상 가능 | — (BUY 진행) |
| `PRICE_OVER_CAP` | 1주 가격 > 종목당 한도 | BUY 차단 |
| `INSUFFICIENT_CASH` | 남은 현금 < 1주 가격 | BUY 차단 |
| `BELOW_MIN_LOT` | floor(min(cap, cash)/price) < 1 | BUY 차단 |
| `MAX_POSITIONS_REACHED` | 보유 ≥ 한도 + 신규 종목 | BUY 차단 (P-03 매핑) |
| `INVALID_PRICE` | price ≤ 0 | BUY 차단 |
| `MISSING_PRICE` | price is None | BUY 차단 |
| `SKIP_NON_BUY` | action != BUY | — (SELL/EXIT/HOLD/NO_OP 영향 없음) |

verdict 우선순위 (위→아래 첫 매칭):
1. SKIP_NON_BUY → 2. MISSING_PRICE → 3. INVALID_PRICE → 4. PRICE_OVER_CAP
→ 5. INSUFFICIENT_CASH → 6. BELOW_MIN_LOT → 7. MAX_POSITIONS_REACHED
→ 8. AFFORDABLE

### 3-B-2. 매수 가능 수량 계산

```
affordable_quantity = floor( min(effective_per_symbol_cap_krw, available_cash_krw) / price )
```

예시 (cap=1M, cash=10M):
- price=70,000 → min=1M, 1M//70k = **14주**
- price=200,000 → 1M//200k = **5주**
- price=900,000 → 1M//900k = **1주**
- price=1,800,000 → cap < price → `PRICE_OVER_CAP`, qty=0

### 3-B-3. 절대 invariant

| 항목 | 값 |
|---|---|
| `AffordabilityResult.is_order_signal` | False (dataclass __post_init__ 가드) |
| `AffordabilityResult.is_live_authorization` | False |
| `AffordabilityResult.is_paper_only` | True |
| `affordable_quantity < 0` | ValueError |
| `affordability_check.py` import 대상 (broker / OrderExecutor / KIS / settings / 외부 HTTP / AI SDK) | 0건 |
| `.enable_live_trading = ...` 등 안전 flag mutation | 0건 |
| `broker.place_order(` / `route_order(` / `KisClient(` 호출 | 0건 |
| 추가 매수 (이미 보유 종목) | `MAX_POSITIONS_REACHED` 건너뜀 (`is_existing_position=True`) |

### 3-B-4. API contract

`POST /api/auto-paper/affordability/preview` (advisory):

입력:
```json
{
  "action":               "BUY",
  "symbol":               "000660",
  "price":                1800000,
  "available_cash_krw":   10000000,
  "current_held_symbols": [],
  "effective_per_symbol_cap_krw": null,
  "max_concurrent_positions":     null
}
```

`effective_per_symbol_cap_krw` / `max_concurrent_positions` 가 None 이면
*현재 PaperCapitalConfig* 자동 사용. 명시 override 도 허용 (테스트 / what-if).

응답 (200):
```json
{
  "verdict":                      "PRICE_OVER_CAP",
  "symbol":                       "000660",
  "action":                       "BUY",
  "price":                        1800000,
  "effective_per_symbol_cap_krw": 1000000,
  "available_cash_krw":           10000000,
  "affordable_quantity":          0,
  "current_held_unique_symbols":  0,
  "max_concurrent_positions":     3,
  "is_existing_position":         false,
  "reason_ko":                    "1주 가격이 종목당 투자금 한도 1,000,000원을 초과해 Paper 매수 후보에서 제외했습니다.",
  "risk_flag":                    "price_over_per_symbol_cap",
  "is_affordable":                false,
  "is_order_signal":              false,
  "is_live_authorization":        false,
  "is_paper_only":                true,
  "notice":                       "본 결과는 advisory — Paper 전용..."
}
```

### 3-B-5. AI Paper 연동 권장 흐름

PaperDecision 생성 (`paper_decision_bridge` / `consume_agent_recommendations`)
에서 BUY 후보 *전* 본 helper 를 호출:

```python
from app.auto_paper.affordability_check import (
    AffordabilityVerdict,
    check_paper_affordability,
)

result = check_paper_affordability(
    action="BUY",
    symbol=candidate.symbol,
    price=current_price,
    available_cash_krw=current_paper_cash,
    effective_per_symbol_cap_krw=paper_cfg.effective_per_symbol_cap_krw,
    current_held_symbols=paper_positions.unique_symbols(),
    max_concurrent_positions=paper_cfg.max_concurrent_positions,
)
if not result.is_affordable and result.verdict != AffordabilityVerdict.SKIP_NON_BUY:
    # action 을 HOLD / NO_OP 로 강등, risk_flags 에 result.risk_flag 추가,
    # metadata.affordability_result = result.to_dict(), ledger 에 reason_ko
    # 기록, AgentDecisionLog 의 reasons 에도 reason_ko 추가.
    ...
```

본 helper 의 *호출* 은 caller 책임 — affordability_check 모듈 자체는 paper
ledger / DB / AgentDecisionLog 를 직접 쓰지 않는다 (관심사 분리).

### 3-B-6. UI (`PaperAffordabilityCard`)

별도 카드 "🧮 매수 가능성":
- verdict 헤드라인 (색상 매핑: AFFORDABLE 녹색 / 한도/현금/가격 이슈 빨강 /
  한도 도달 주황)
- 사람 친화 한국어 사유 (reason_ko)
- 6개 수치 (종목 / 1주 가격 / 종목당 한도 / 남은 Paper 현금 / 매수 가능 수량 / 보유/한도)
- "Paper 전용 · 실제 주문 아님" 영구 배지
- "broker / OrderExecutor 호출은 어떤 경로에서도 발생하지 않습니다" disclaimer
- input/textarea/select 0개 — caller 가 prop 으로 후보를 주입
- button 0개 — 본 카드는 *advisory 표시 전용*

## 3-C. P-05 — 최소 1주 매수 조건 (본 PR 추가)

국내주식은 *정수 단위* 거래 — 소수점 주식 없음. AI Paper 가 *정수 ≥ 1*
인 BUY 만 실행하도록 강제하는 마이크로 가드. P-04 (가격 vs 한도 vs 현금) 와
함께 호출되며, P-04 가 macro / P-05 가 micro 책임.

### 3-C-1. MinLotVerdict (5종)

| verdict | 의미 |
|---|---|
| `ALLOWED` | quantity 가 정수 ≥ 1 |
| `BLOCKED_ZERO_QUANTITY` | quantity = 0 또는 None |
| `BLOCKED_NEGATIVE_QUANTITY` | quantity < 0 |
| `BLOCKED_FRACTIONAL_QUANTITY` | 소수점 (0.5, 1.5 등) / NaN / inf / 비-숫자 |
| `SKIP_NON_BUY` | action != BUY |

Verdict 우선순위 (위→아래 첫 매칭):
1. `SKIP_NON_BUY` → 2. `BLOCKED_ZERO_QUANTITY` (None) → 3. 비-숫자 / NaN /
inf → 4. `BLOCKED_NEGATIVE_QUANTITY` → 5. `BLOCKED_FRACTIONAL_QUANTITY` →
6. `BLOCKED_ZERO_QUANTITY` (== 0) → 7. `ALLOWED`

### 3-C-2. floor 정책 (`compute_paper_affordable_lot`)

```
affordable_lot = floor( min(effective_per_symbol_cap_krw, available_cash_krw) / price )
```

특징:
- **반올림 금지** — 항상 *내림*. 다음 lot 가 cap / cash 를 초과할 수 있으므로
  보수적.
- 예시: budget=999,999 + price=1,000 → 999주 (1,000주 아님).
- 예시: budget=1,000,000 + price=1,000,001 → 0주 (1.0 미만은 0).
- `cap=0` 은 *cap 제약 없음* 으로 간주 — `cash` 만 제약.
- `price ≤ 0` 또는 `None` → 0 반환 (caller 가 P-04 의 INVALID_PRICE /
  MISSING_PRICE 로 처리).

### 3-C-3. 절대 invariant

| 항목 | 값 |
|---|---|
| `MinLotCheckResult.is_order_signal` | False (dataclass __post_init__ 가드) |
| `MinLotCheckResult.is_live_authorization` | False |
| `MinLotCheckResult.is_paper_only` | True |
| `floored_quantity < 0` | ValueError |
| `compute_paper_affordable_lot` 반환 타입 | 항상 `int` (float / Decimal / numpy 0건) |
| `min_lot_check.py` 모듈 import (broker / OrderExecutor / KIS / settings / 외부 HTTP / AI SDK) | 0건 |
| `broker.place_order(` / `route_order(` / `KisClient(` / 안전 flag mutate | 0건 |
| 소수점 주식 지원 | False 영구 (`fractional_share_supported=False`) |
| 반올림 정책 | `"floor"` 영구 |
| bool quantity (True/False) | `BLOCKED_FRACTIONAL_QUANTITY` (의도치 않은 정수 변환 차단) |

### 3-C-4. API contract

`POST /api/auto-paper/min-lot/validate` — 결정된 quantity 검증:

입력:
```json
{ "action": "BUY", "symbol": "005930", "quantity": 1.5 }
```

응답:
```json
{
  "verdict":             "BLOCKED_FRACTIONAL_QUANTITY",
  "requested_quantity":  1.5,
  "floored_quantity":    1,
  "action":              "BUY",
  "symbol":              "005930",
  "reason_ko":           "매수 수량 1.5 는 소수점이라 Paper 매수 후보에서 제외...",
  "risk_flag":           "fractional_quantity",
  "is_allowed":          false,
  "is_paper_only":       true,
  "is_live_authorization": false
}
```

`GET /api/auto-paper/min-lot/preview` — 현재 cap 기준 *예시 1주 가격 매트릭스*:

```json
{
  "effective_per_symbol_cap_krw": 1000000,
  "initial_cash":                 10000000,
  "min_lot_quantity":             1,
  "fractional_share_supported":   false,
  "rounding_policy":              "floor",
  "examples": [
    { "price":   50000, "affordable_quantity": 20, "is_affordable": true  },
    { "price":  100000, "affordable_quantity": 10, "is_affordable": true  },
    { "price":  500000, "affordable_quantity":  2, "is_affordable": true  },
    { "price": 1000000, "affordable_quantity":  1, "is_affordable": true  },
    { "price": 2000000, "affordable_quantity":  0, "is_affordable": false },
    { "price": 5000000, "affordable_quantity":  0, "is_affordable": false }
  ],
  "is_paper_only": true,
  "is_live_authorization": false
}
```

### 3-C-5. UI 확장 (`PaperCapitalCard`)

기존 카드에 새 섹션 "🔢 최소 1주 매수 — 예상 가능 수량" 추가:
- 6개 예시 가격 × `affordable_quantity` (현재 cap 기준)
- "Paper 매수는 정수 1주 이상만 허용 (소수점 주식 불가)" disclaimer
- "수량은 항상 내림(floor) — 반올림하지 않습니다" 명시
- "최소 수량 1주 · 소수점 주식 지원 안 함 · 반올림 정책 floor" policy 행
- input/textarea/select/button **0개** — *advisory 표시 전용*
- 시드머니 / 종목당 한도 변경 시 자동 재조회 (config 변경 → preview 갱신)

### 3-C-6. P-04 affordability 와의 관계

| 책임 | P-04 affordability_check | P-05 min_lot_check |
|---|---|---|
| 매크로 (가격 vs 한도 vs 현금) | ✅ | — |
| 보유 종목 수 한도 | ✅ | — |
| 매수 가능 *수량 계산* (floor) | ✅ (호출자에게 반환) | ✅ (compute helper 제공) |
| 결정된 *quantity 자체* 검증 (정수 ≥ 1) | — | ✅ |
| 소수점 / NaN / inf / bool 거부 | — | ✅ |

권장 호출 흐름:
```
1. P-04: result_p4 = check_paper_affordability(...)
2. result_p4.is_affordable 이면 → sizing 결정 → qty 계산
3. P-05: result_p5 = validate_paper_min_lot(action, quantity)
4. result_p5.is_allowed 일 때만 PaperDecision 진행
```

P-04 의 `affordable_quantity` 와 P-05 의 `compute_paper_affordable_lot()`
은 *동일한 floor 정책* — 본 PR 의 회귀 테스트가 두 결과의 일치를 lock.

## 4. P-시리즈 전체 매핑

| 번호 | 항목 | 본 PR | 상태 |
|---|---|---|---|
| P-01 | 초기 Paper 시드머니 설정 | (이전 PR) | done |
| P-02 | 종목당 투자금 설정 | (이전 PR) | done |
| P-03 | 최대 동시 보유 종목 수 제한 | (이전 PR) | done |
| P-04 | 매수 가능성 체크 | (이전 PR) | done |
| P-05 | 최소 1주 매수 조건 | (이전 PR) | done |
| P-06 | 고가주 처리 정책 | (이전 PR) | done |
| P-07 | Paper 현금 잔고 체크 | (이전 PR) | done |
| P-08 | Position sizing (max_amount/price) | (이전 PR) | done |
| P-09 | 성향별 자금 배분 (보수/안정/공격) | (이전 PR) | done |
| P-10 | 일일 최대 매수금액 한도 | (이전 PR) | done |
| P-11 | 종목별 최대 비중 제한 | (이전 PR) | done |
| P-12 | 중복 보유 방지 (BUY 가드) | (이전 PR) | done |
| P-13 | 물타기 / 추가매수 / 피라미딩 정책 | ✅ 본 PR | done |
| P-... | (이후 항목) | (별도 PR) | pending |
| P-16 | Paper 자본 설정 영구 저장 | (예정) | pending |


## 5. P-13 — 물타기 / 추가매수 / 피라미딩 정책 (본 PR 추가)

> **본 섹션은 *시스템 안전 정책* 이며 *투자 조언이 아니다*.** Paper /
> SIMULATION 검증 흐름의 단일 진실. 실거래 / LIVE_AI_EXECUTION 활성화는
> *별도 promotion gate* + 별도 PR 통과 없이는 불가.

### 5-1. 문서 목적

본 섹션은 Paper / AI Paper 자금 배분에서 **동일 종목 추가매수**, **물타기
(averaging down)**, **피라미딩 (pyramiding)** 정책을 정의한다. 세 정책 모두
*손실 확대 위험* 이 크므로 기본 금지하며, 향후 전략별 검증을 거쳐 옵트인
가능한 구조만 미리 둔다.

### 5-2. 기본 원칙

1. **자금은 한 번에 과도하게 투입하지 않는다** — P-07 cash / P-10 daily /
   P-11 symbol-weight / P-12 duplicate 가드가 모두 *동시 적용* 된다.
2. **종목당 투자금, 일일 매수한도, 종목별 최대비중, 중복보유 방지를 모두 적용
   한다** — 본 정책은 *그 위의* 별도 가드.
3. **추가매수 / 물타기 / 피라미딩은 손실 확대 위험으로 기본 금지** — 사용자
   요청서 §1 6번 명시.
4. **공격형 risk profile 도 자동 허용 사용 안 함** — 위험 성향 ≠ 위험 행동
   자동 옵트인 (사용자 요청서 §4 명시).
5. **추가매수 허용은 별도 명시 설정** (`allow_additional_buy=True`) 이며
   *실거래 권한 부여가 아니다*.
6. **허용 시에도** Paper cash / 일일 한도 / 종목 비중 / RiskManager /
   PermissionGate 는 *별도 유지*.

### 5-3. 성향별 정책 (사용자 요청서 §4 정확 매트릭스)

| 성향 | allow_additional_buy | allow_averaging_down | allow_pyramiding |
|---|---|---|---|
| 보수형 (CONSERVATIVE) | **false** | **false** | **false** |
| 안정형 (BALANCED, default) | **false** | **false** | **false** |
| 공격형 (AGGRESSIVE) | **false** | **false** | **false** |

> ⚠️  공격형 도 자동 물타기 허용 안 함. 위험 성향에 따라 종목당 한도 / 일일
> 한도 / 종목 비중 *값* 은 다르지만, 추가매수 자체는 *별도 명시 옵트인* 만
> 가능.

### 5-4. 동일 종목 추가매수 정책 (P-12 + P-13)

- **기본값**: 금지 (`allow_additional_buy = False`)
- **차단 사유 코드**: `DUPLICATE_POSITION_BUY_BLOCKED` (P-12)
- **사용자 표시 문구**: `"이미 보유 중인 종목이라 추가 매수 차단"`
- **옵트인 방법** (사용자 명시 설정 필요): `allow_additional_buy=True` →
  P-12 결과 `ADDITIONAL_BUY_ALLOWED`. 단, 다른 layer (cash / daily /
  weight / RiskManager / PermissionGate) 는 *그대로 적용*.

### 5-5. 물타기 정책 (Averaging Down)

- **기본값**: 금지 (`allow_averaging_down = False`)
- **정책 코드**: `AVERAGING_DOWN_DISABLED`
- **사용자 표시 문구**: `"물타기는 손실 확대 위험으로 기본 금지입니다."`
- **사유**: 손실 중인 포지션에 추가매수하면 평균단가는 내려가지만 **노출이
  커진다** — 반대 방향 움직임이 계속되면 손실이 *기하급수* 로 증가. Paper
  검증 단계에서도 동일 위험.
- **향후 허용 시**: 별도 PR + 전략 검증 + 최대 손실 / 종목비중 / 일일 한도 /
  현금잔고 체크 *유지* 필수.

### 5-6. 피라미딩 정책 (Pyramiding)

- **기본값**: 금지 (`allow_pyramiding = False`)
- **정책 코드**: `PYRAMIDING_DISABLED`
- **사용자 표시 문구**: `"피라미딩은 기본 금지입니다."`
- **사유**: 수익 중인 포지션에 추가매수도 *추세 반전 시 손실 확대* 위험.
  전략별 검증 (예: trailing stop / volatility breakout) 후에만 옵트인.
- **향후 허용 시**: 전략별 명시 설정 + 위험관리 룰 확인.

### 5-7. 설정 우선순위 (사용자 요청서 §7 정확)

```
1. 사용자 명시 설정 (manual_allow_additional_buy)
2. 전략별 명시 설정 (strategy.allow_additional_buy)   ← 후속 PR
3. risk profile 기본 설정                              ← 모든 profile False
4. 시스템 기본값 (DEFAULT_ALLOW_*)                     ← 영구 False
```

**시스템 기본값은 항상 safe**:
- `DEFAULT_ALLOW_ADDITIONAL_BUY = False`
- `DEFAULT_ALLOW_AVERAGING_DOWN = False`
- `DEFAULT_ALLOW_PYRAMIDING = False`

### 5-8. 허용 시 필요한 추가 조건

`allow_additional_buy=True` 같은 옵트인이 있어도 BUY 가 실제 진행되려면 다음을
*모두* 통과해야 한다 (사용자 요청서 §8):

1. **P-07 Paper 현금 충분** (cash check)
2. **P-10 일일 최대 매수금액 이내** (daily buy limit)
3. **P-11 종목별 최대 비중 이내** (symbol weight limit)
4. **RiskManager 통과**
5. **PermissionGate 통과**
6. **Audit log 기록** (모든 차단 / 허용 사유 carry)

옵트인은 *중복 보유 차단만* 해제 — 다른 안전 검사는 *그대로 적용* 된다.

### 5-9. reason_code 일람표

본 시리즈에서 사용하는 *추가매수 / 물타기 / 피라미딩 / 한도 / 현금* 관련
reason_code 정렬 (사용자 요청서 §9):

| reason_code | 의미 | 발생 layer |
|---|---|---|
| `ADDITIONAL_BUY_DISABLED` | 추가매수 시스템 정책 금지 | P-13 |
| `AVERAGING_DOWN_DISABLED` | 물타기 시스템 정책 금지 | P-13 |
| `PYRAMIDING_DISABLED` | 피라미딩 시스템 정책 금지 | P-13 |
| `DUPLICATE_POSITION_BUY_BLOCKED` | 보유 중 종목 추가 BUY 차단 | P-12 |
| `SYMBOL_WEIGHT_LIMIT_EXCEEDED` | 종목별 최대 비중 초과 | P-11 |
| `DAILY_BUY_LIMIT_EXCEEDED` | 일일 최대 매수금액 초과 | P-10 |
| `INSUFFICIENT_PAPER_CASH` | Paper 현금 부족 | P-07 |

### 5-10. 운영 단계별 정책 (사용자 요청서 §10)

| 단계 | 추가매수 기본 정책 |
|---|---|
| **Simulation** | 추가매수 기본 금지 (테스트 시 옵트인 가능) |
| **Paper** | 추가매수 기본 금지 — 명시 설정 시에만 테스트 가능 |
| **Shadow** | 추가매수 기본 금지 |
| **Live Manual** | 별도 PR 전까지 금지 |
| **Live AI Execution** | **금지 (Live AI Execution 자체가 미허가 상태)** |

> ⚠️  `LIVE_AI_EXECUTION` 모드에서의 자동 추가매수는 *영구 미허가* —
> `AIExecutionActivationGate` (#75) 가 `futures_allowed=False` 와 동등하게
> 추가매수도 별도 promotion gate 필요.

### 5-11. 변경 절차

추가매수 / 물타기 / 피라미딩 *기본 정책* 을 변경하려면:

1. **별도 PR** — capital_allocation_policy.md + capital_config.py + 테스트
   동시 갱신.
2. **테스트 보강** — 새로운 default 가 적용된 후의 시나리오를 모든 risk
   profile / 운영 단계에서 검증.
3. **문서 동기** — `docs/capital_allocation_policy.md` 의 §5 표를 함께 갱신.
4. **실거래 연결은 promotion gate 필요** — Paper Gate (#72) / Live Manual
   Gate (#73) / AI Assist Gate (#74) / AI Execution Gate (#75) 통과 + 운영자
   명시 옵트인.

### 5-12. 절대 invariant (테스트로 lock)

- `DEFAULT_ALLOW_ADDITIONAL_BUY` / `DEFAULT_ALLOW_AVERAGING_DOWN` /
  `DEFAULT_ALLOW_PYRAMIDING` 의 *기본값* 은 영구 `False` — 본 PR 의 정적
  grep + pytest 가 강제.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `LIVE_AI_EXECUTION` 변경
  0건.
- 옵트인 시에도 broker / OrderExecutor / route_order 호출 0건 — 본 정책 모듈
  은 *advisory only*.
- 모든 정책 응답에 `is_paper_only=True` / `is_live_authorization=False` /
  `is_order_signal=False` 영구.

---

## Paper Capital vs Live Capital Separation (P-20)

> **핵심 원칙: Paper capital 은 가상 자금이며, 실전 계좌 잔고도 실전 주문 가능
> 금액도 아니다.** Paper 자금 설정값이 Live 주문 금액 / 수량 / 한도로 *자동
> 연결되면 안 된다*. Live 전환은 별도 live capital review + manual approval +
> promotion gate 를 반드시 거친다.

### P-20-1. Paper capital 정의

- Paper capital 은 AI Paper / SIMULATION / SHADOW 검증을 위한 **가상 자금** 이다.
- **실제 증권계좌 잔고가 아니다.**
- **실제 주문 가능 금액이 아니다.**
- 시드머니 / 종목당 투자금 / 일일 매수 한도 / 최대 보유 / 종목 비중 / risk
  profile 모두 *Paper 운용 기준* 일 뿐이다.

### P-20-2. Live capital 정의

- Live capital 은 **실제 증권계좌 잔고 + 운영자가 승인한 실전 주문 한도** 를
  의미한다.
- Live capital 은 Paper capital 과 **별도 검토 / 별도 설정 / 별도 승인** 이
  필요하다.
- 본 프로젝트는 Live capital 설정 기능을 *아직 제공하지 않는다* — placeholder
  (`live_capital_review_status` / `LIVE_CAPITAL_REVIEW_REQUIRED`) 만 둔다.

### P-20-3. 절대 원칙

- Paper capital 설정은 **Live 주문 금액으로 사용하지 않는다.**
- Paper per-symbol allocation 은 **Live per-order amount 가 아니다.**
- Paper max daily buy amount 는 **Live daily order limit 이 아니다.**
- Paper max positions 는 **Live max positions 가 아니다.**
- Paper risk profile 은 **Live risk authorization 이 아니다.**

### P-20-4. Live 전환 조건

`ENABLE_LIVE_TRADING=true` *만으로 충분하지 않다*. 다음을 모두 거쳐야 한다:

- 운영자 manual approval (PermissionGate 큐)
- live capital review (별도 설정 — 본 PR 미구현, placeholder)
- live order notional cap (별도 설정)
- promotion gate (Paper Gate #72 / Live Manual Gate #73 / AI Assist Gate #74 /
  AI Execution Gate #75)
- audit log
- canary 또는 최소 주문 한도 정책

### P-20-5. 금지 예시

- ❌ Paper 시드머니 10,000,000원을 실전 계좌 주문 한도로 사용
- ❌ Paper 종목당 투자금 1,000,000원을 실전 1회 주문 금액으로 자동 사용
- ❌ Paper 일일 매수 한도 3,000,000원을 실전 일일 주문 한도로 자동 사용

### P-20-6. 허용 예시

- ✅ Paper 결과를 *참고자료* 로 보는 것
- ✅ Live 주문 금액은 별도 live capital config 또는 manual approval 에서만 결정
- ✅ Live order 는 PermissionGate / RiskManager / operator approval 을 반드시 통과

### P-20-7. reason_code 표

| reason_code | 의미 | 한국어 메시지 |
|---|---|---|
| `PAPER_CAPITAL_NOT_LIVE_CAPITAL` | Live 경로에 paper capital 이 섞임 (무시됨) | Paper 자금 설정은 실전 주문 한도가 아닙니다. |
| `LIVE_CAPITAL_REVIEW_REQUIRED` | live capital 미검토/미승인 | 실전 주문에는 별도 Live 자금 검토가 필요합니다. |
| `LIVE_ORDER_NOTIONAL_NOT_CONFIGURED` | live order notional 미설정 | 실전 주문 금액(live order notional)이 설정되지 않았습니다. |
| `LIVE_CAPITAL_PERMISSION_DENIED` | live 자금 권한 거부 | 실전 자금 권한이 거부되었습니다. |
| `PAPER_MODE_PAPER_CAPITAL_OK` | 비-live 모드에서 paper capital 정상 사용 | 현재 설정은 Paper / AI Paper 전용입니다. |

구현: `app/permission/live_capital_guard.py::evaluate_live_capital_authorization`.
`LiveCapitalReviewResult.live_capital_approved=False` / `is_live_authorization=
False` 영구 (placeholder — 실전 활성화 기능 아님).

### P-20-8. 운영 단계별 정책

| 모드 | Paper capital | Live 주문 |
|---|---|---|
| `SIMULATION` | 사용 가능 (paper sizing) | 없음 |
| `PAPER` | 사용 가능 (paper sizing) | 없음 |
| `LIVE_SHADOW` | 비교/시뮬레이션용 | 실주문 없음 (RiskManager 가 모두 REJECTED) |
| `LIVE_MANUAL_APPROVAL` | **자동 사용 금지** | live approval 필요 (`LIVE_CAPITAL_REVIEW_REQUIRED`) |
| `LIVE_AI_ASSIST` | **자동 사용 금지** | live approval 필요 |
| `LIVE_AI_EXECUTION` | **자동 사용 금지** | 별도 promotion gate 전까지 차단 |

> **PermissionGate.approve 는 paper capital 을 읽지 않는다** — 주문 수량/가격은
> 제출 시점 `PendingApproval` 스냅샷에서 복원되며, paper capital 설정과 결합
> 0건. 본 가드는 그 분리를 코드/문서/테스트로 *고정* 한다.
