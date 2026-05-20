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

## 4. P-시리즈 전체 매핑

| 번호 | 항목 | 본 PR | 상태 |
|---|---|---|---|
| P-01 | 초기 Paper 시드머니 설정 | (이전 PR) | done |
| P-02 | 종목당 투자금 설정 | (이전 PR) | done |
| P-03 | 최대 동시 보유 종목 수 제한 | ✅ 본 PR | done |
| P-04 | 매수 가능성 체크 | (다음) | pending |
| P-... | (이후 항목) | (별도 PR) | pending |
| P-16 | Paper 자본 설정 영구 저장 | (예정) | pending |
