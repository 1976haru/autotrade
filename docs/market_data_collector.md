# Market Data Collector (체크리스트 #19)

## 목적

자동매매 / Agent 판단의 입력이 되는 OHLCV 시계열 데이터를 일관된 인터페이스로 수집·검증·저장한다. 본 단계는 **Mock / Yfinance / DB 캐시 기반**의 분/시/일봉 수집과 누락률 계산을 다루며, 실시간 현재가 / 체결 / 호가 / WebSocket 피드는 Phase 2로 분리한다.

본 모듈은 **broker 주문 API를 직접 호출하지 않으며**, RiskManager / PermissionGate / OrderExecutor 분기와 분리되어 있다 (CLAUDE.md 절대 원칙).

## 현재 구현 범위

| 영역 | 상태 | 위치 |
|---|---|---|
| `MarketDataAdapter` ABC | ✓ 기존 | `app/market/base.py` |
| Mock adapter (1d only) | ✓ 기존 | `app/market/mock.py` |
| Yfinance adapter (1m/5m/1h/1d) | ✓ 기존 | `app/market/yfinance_adapter.py` |
| `MarketBar` 테이블 + UNIQUE(symbol, interval, timestamp) | ✓ 기존 | `app/db/models.py`, alembic 0001 |
| `BarCache.get/save` | ✓ 기존 | `app/market/cache.py` |
| `/api/market/bars` 라우트 | ✓ 기존 | `app/api/routes_market.py` |
| `staleness.py` (cache stale 검사) | ✓ 기존 | `app/market/staleness.py` |
| **OHLCV 검증 / 정렬 / 중복 제거** (#19) | ✓ 본 PR | `app/market/candle_builder.py` |
| **1m → 5m 집계** (#19) | ✓ 본 PR | 동일 |
| **Coverage / Missing Rate 계산** (#19) | ✓ 본 PR | 동일 |
| **`MarketDataCollector` orchestration** (#19) | ✓ 본 PR | `app/market/collector.py` |
| **`should_block_new_buy` pre-check helper** (#19) | ✓ 본 PR | 동일 (read-only) |
| 실시간 현재가 / 체결 / 호가 / WebSocket | 🛑 **Phase 2** | TBD |
| tick / orderbook 별도 테이블 | 🛑 **Phase 2** | TBD (파티셔닝/보관정책 결정 후) |

## MarketDataAdapter 구조

```
MarketDataAdapter (ABC)
  ├─ MockMarketData         (1d only, 결정론적 합성)
  ├─ YfinanceMarketData     (1m/5m/1h/1d, 외부 네트워크)
  └─ <KisMarketData>        (Phase 2 — 옵트인)
```

`get_bars(symbol, start, end, interval) -> list[Bar]` 단일 메서드 — `Bar`는 `app/backtest/types.py::Bar` (frozen dataclass: symbol/timestamp/open/high/low/close/volume).

`Interval` enum: `1d / 1h / 5m / 1m`. 각 adapter는 자신이 지원하는 interval 외에는 `ValueError`로 거부한다.

## MarketDataCollector

`app/market/collector.py::MarketDataCollector`는 adapter를 입력으로 받아 다음 파이프라인을 수행한다:

1. **adapter.get_bars(...)** — 외부 호출 (또는 Mock).
2. **`validate_bars`** — OHLCV 무결성 검증 (high>=low, open/close in [low, high], volume>=0). 위반 시 `CandleValidationError`.
3. **`deduplicate_bars`** — 같은 (symbol, timestamp)이 여러 번이면 마지막만 유지.
4. **`sort_bars`** — timestamp 오름차순 정렬 (timezone-naive는 UTC로 가정).
5. **`BarCache.save`** — `db`가 주어지면 `MarketBar` 테이블에 영구화. 같은 키 행은 delete-then-insert.
6. **Coverage 계산** — `expected_bar_count` 대비 `actual_count` → `CollectionResult`.

### 주요 메서드

| 메서드 | 의미 |
|---|---|
| `collect(symbol, start, end, interval, db=None)` | 단일 심볼 수집. `CollectionResult` 반환 |
| `collect_many(symbols, start, end, interval, db=None)` | 여러 심볼 직렬 수집. adapter 예외는 호출자에 전파 (운영자가 retry/skip 결정) |
| `collect_and_aggregate_1m_to_5m(symbol, start, end, db)` | 1분봉 fetch → DB 저장 → 5분봉 파생 → DB 저장 (adapter는 1m만 호출, RPM 절약) |

### `CollectionResult`

```python
CollectionResult(
    symbol="005930", interval="1m",
    fetched_count=78, saved_count=78,
    expected_count=80, missing_count=2,
    missing_rate=0.025, coverage_score=97.5,
    start=..., end=...,
)
```

## Candle Builder (`candle_builder.py`)

순수 함수 모음 — DB / network 의존성 없음. broker import 0건.

| 함수 | 의미 |
|---|---|
| `validate_bar(bar)` / `validate_bars(bars)` | OHLCV 무결성 검증. 위반 시 `CandleValidationError` |
| `sort_bars(bars)` | timestamp 오름차순 새 리스트. timezone-naive는 UTC로 처리 |
| `deduplicate_bars(bars)` | 같은 (symbol, timestamp) 중복 제거, 마지막 우선 |
| `assert_single_symbol(bars)` | 모든 bar가 같은 symbol인지 검증. 빈 리스트면 None |
| `partition_by_symbol(bars)` | 다중 symbol을 dict로 분리 |
| `aggregate_1m_to_5m(bars)` | 단일 symbol의 1분봉 → 5분봉 |
| `expected_bar_count(start, end, interval)` | 단순 시간 범위 기반 예상 봉 수 (장운영 시간 미반영) |
| `compute_missing_rate(...)` | 누락률 + coverage_score 산출 |
| `interval_to_seconds(interval)` | 1m=60 / 5m=300 / 1h=3600 / 1d=86400 |

## 1m → 5m 변환 규칙

1분봉 N개를 5분 버킷으로 묶어 1개의 5분봉으로 만든다. 버킷 시작은 분 단위 floor (UTC 기준 09:00, 09:05, ...).

| 필드 | 규칙 |
|---|---|
| `open` | 버킷 안 첫 1분봉의 open |
| `close` | 버킷 안 마지막 1분봉의 close |
| `high` | max |
| `low` | min |
| `volume` | sum |
| `timestamp` | 버킷 시작 시각 (UTC) |
| 결측 처리 | 5개 모두 채워지지 않아도 봉을 만든다 — KRX 분봉 결측이 흔하므로. 결측은 `compute_missing_rate`로 별도 표면화 |

다중 symbol 입력은 거부 — `aggregate_1m_to_5m`은 단일 symbol을 전제한다 (`assert_single_symbol`이 raise). 다중 symbol을 묶어 처리하려면 `partition_by_symbol`로 먼저 나눈다.

## Coverage / Missing Rate

| 필드 | 의미 |
|---|---|
| `expected_count` | 단순 시간 범위 / interval 추정 봉 수 (`(end-start) // interval_seconds + 1`) |
| `actual_count` | adapter가 반환한 봉 수 |
| `missing_count` | `max(0, expected - actual)` — actual이 expected를 초과하면 0으로 clamp |
| `missing_rate` | `missing_count / expected_count`. expected_count=0이면 **None** |
| `coverage_score` | `100 * (1 - missing_rate)`, [0, 100]로 clamp. expected_count=0이면 **None** |

### expected_count의 한계

본 단순 추정은 **장운영 시간(09:00–15:30 KST 평일) / 휴장일 / 장중 일시 정지를 반영하지 않는다.** 따라서:
- 24시간 범위에서 1분봉을 요청하면 expected는 1440이지만 실제 KRX는 정규장 6.5h × 60 = 390개만 발생.
- 이 경우 missing_rate는 과대 추정된다 — 본 단계는 *상한* 모니터링용으로만 사용.

정확한 KRX 캘린더 반영은 **후속 작업** (KRX 휴장일 캘린더 + 운영 시간 정확 반영). 현재는 운영자가 단순 시간 범위를 의식해서 데이터를 요청하는 것을 전제로 한다.

## DB 저장 정책

본 단계에서는 **기존 `MarketBar` 테이블만 사용**한다. 새 tick / orderbook / trade 테이블은 만들지 않는다.

| 데이터 | 본 단계 | Phase 2 |
|---|---|---|
| 1m / 5m / 1h / 1d OHLCV | `MarketBar` 테이블 (UNIQUE symbol+interval+timestamp) | 동일 |
| 현재가 (단일 호출) | 미저장 — broker.get_price 즉시 응답 | TBD — `quote_snapshot` 후보 |
| 체결 (execution feed) | 미저장 | TBD — `market_tick` 후보 (대량) |
| 호가 (orderbook) | 미저장 | TBD — `orderbook_snapshot` 후보 (대량, 별도 보관 정책 필요) |

대량 tick 저장은 데이터량이 커서 별도 파티셔닝/보관 정책 필요 — TimescaleDB hypertable 또는 PG native 파티셔닝 결정 후 별도 PR에서 진행. 자세한 매트릭스는 [`database_schema.md`](database_schema.md) "대량 데이터 정책" 절.

### timezone 처리

- `MarketBar.timestamp`는 SQLite/PG 모두 naive `DateTime`으로 저장 (`_utcnow()` 헬퍼가 UTC).
- adapter / collector / candle_builder는 **naive datetime을 UTC로 가정**. tz-aware 입력은 UTC로 변환.
- 호출자는 입력 datetime을 가능하면 UTC로 명시 (`tzinfo=timezone.utc`).

### 잘못된 OHLCV 처리

- collector는 `validate_bars`를 통과하지 못한 행을 **저장 전에 거부**한다 (`CandleValidationError` raise).
- 부분 실패는 본 단계에서 지원하지 않는다 — 한 batch가 통째로 거부된다 (운영자가 즉시 인지).
- 향후 partial-skip 옵션이 필요해지면 별도 옵트인 PR.

## Staleness / Freshness 정책

데이터 피드가 멎은 상태에서 strategy / 운영자가 stale 캐시 위에서 신호를 만드는 사고를 방지한다.

### 코드 위치

| 함수 | 위치 | 역할 |
|---|---|---|
| `latest_bar_fetched_at` | `app/market/staleness.py` | (symbol, interval) 최근 fetched_at |
| `is_bar_cache_stale` | 동일 | (stale, age_seconds) 반환 |
| `stale_symbols` | 동일 | bulk 모니터링 — 모든 stale 심볼 |
| **`should_block_new_buy`** (#19) | `app/market/collector.py` | order pre-check용 — (block, reason) |

### 정책 (운영자 가이드)

1. **신규 BUY 전에는 데이터 freshness 확인 필요.**
2. `latest_bar_fetched_at` 또는 `MarketBar.fetched_at`이 `max_age_seconds`보다 오래되면 **신규 진입 차단**.
3. **WebSocket reconnect 중에는 신규 BUY 무조건 금지** — `should_block_new_buy(websocket_reconnecting=True)`.
4. **SELL / 청산은 별도 정책** — stop-loss 등은 freshness가 낮아도 실행되어야 함. 본 helper는 BUY pre-check 전용.
5. RiskManager의 `stale_price_max_age_seconds` (기본 60초)는 **broker quote 단계** 가드(143). 본 helper의 `max_age_seconds`는 **bar cache 단계** 가드(171). 두 가드가 직렬로 작동.
6. 본 단계에서는 **`route_order` / RiskManager 흐름을 변경하지 않는다**. 향후 LIVE order 라우팅 PR에서 `should_block_new_buy`를 호출자가 사용한다. 자세한 강화는 **체크리스트 #20 Data Freshness**에서 진행 예정.

### 사용 예 (Phase 2 LIVE order 라우팅 PR에서 도입 예정)

```python
# 본 PR에서는 호출하지 않음 — 정책과 helper만 제공.
from app.market.collector import should_block_new_buy

block, reason = should_block_new_buy(
    db, symbol="005930", interval="1m",
    max_age_seconds=60,
    websocket_reconnecting=feed.is_reconnecting(),
)
if block and order.side == "BUY":
    # RiskManager에 reason을 전달하거나, 별도 audit row를 남긴다.
    ...
```

## 현재가 / 체결 / 호가 Phase 계획

| Phase | 항목 | 트리거 |
|---|---|---|
| **본 PR (#19)** | 분/시/일봉 OHLCV 수집 + 검증 + coverage | — |
| **Phase 2** | 현재가 (`broker.get_price` wrapper로 polling, 1초 단위) | LIVE_SHADOW 안정화 후 |
| **Phase 2** | 체결 / 호가 — KIS WebSocket 통합 | KIS adapter LIVE place_order 활성화 후 |
| **Phase 2** | tick / orderbook 별도 테이블 + 파티셔닝 | 데이터량 / 보관 비용 결정 후 |
| **Phase 3** | Kiwoom REST 조건검색 통합 (Watchlist 자동 갱신 후보) | [`kiwoom_rest_research.md`](kiwoom_rest_research.md) Phase 4+ |

## 실제 broker API 미사용 원칙

본 PR은 다음을 **절대 호출하지 않는다**:
- `KisClient` / `KisBrokerAdapter`의 어떤 메서드 (collector는 broker import 0건)
- 실 LIVE host (`https://openapi.koreainvestment.com:9443`) — `KIS_IS_PAPER=true` 강제
- WebSocket 실시간 피드

CI 테스트는 **Mock adapter 또는 in-memory `_FakeAdapter`**만 사용한다. 외부 네트워크 0건.

## 테스트 방법

```bash
# 본 모듈 테스트만
cd backend
python -m pytest tests/test_market_candle_builder.py tests/test_market_collector.py -q

# 전체 회귀
python -m pytest -q
python -m ruff check app tests
```

테스트는 `_FakeAdapter` (in-memory `MarketDataAdapter` 구현)로 외부 호출 없이 collector 파이프라인 전체를 검증한다 — adapter 결과 → 검증 → 정렬 → 저장 → coverage 계산 → 5m 집계 → staleness pre-check.

## 후속 과제 (Backlog)

| 항목 | 트리거 |
|---|---|
| KRX 정규 운영 시간 + 휴장일 캘린더를 `expected_bar_count`에 반영 | 정확한 missing_rate 필요 시점 |
| 현재가 / 체결 / 호가 실시간 피드 | KIS WebSocket 통합 PR |
| `market_tick` / `orderbook_snapshot` 테이블 + 파티셔닝 | 데이터량 결정 후 |
| `should_block_new_buy`를 `route_order` pre-check에 wire | 체크리스트 #20 Data Freshness |
| Strategy / Agent가 `BarCache`에서 active watchlist를 universe로 사용 | LIVE strategy 활성화 PR |
| Partial-skip 옵션 (잘못된 행만 거부, 나머지는 저장) | 운영 데이터에서 필요성 확정 시 |
| `/api/market/coverage` 운영자 dashboard endpoint | UI 추가 요청 시 |

## 관련 문서

- [`database_schema.md`](database_schema.md) — `MarketBar` 컬럼/인덱스, 대량 데이터 정책
- [`api_limits.md`](api_limits.md) — KIS quote 호출 한도, 폴링 정책
- [`broker_selection.md`](broker_selection.md) — adapter 매트릭스
- [`kiwoom_rest_research.md`](kiwoom_rest_research.md) — Phase 2 외부 데이터 소스 후보
- [`risk_policy.md`](risk_policy.md) — `stale_price_max_age_seconds` (broker quote 단계)
- [`watchlist_policy.md`](watchlist_policy.md) — universe 후보군과의 연계 (향후)
