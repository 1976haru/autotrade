# Data Freshness Policy (체크리스트 #20)

## 1. 목적

지연된 시세 / 봉 / 피드 위에서 단타 진입 신호가 만들어지는 것을 방지한다. 데이터가 N초 이상 미수신된 상태에서는 **신규 BUY 신호와 주문 요청을 폐기 또는 차단**한다. 청산(SELL)은 위험 축소 목적일 수 있으므로 자동 차단하지 않고 별도 정책으로 관리한다 ([5절](#5-sell--청산-정책)).

본 단계는 **freshness 안전성 모듈 / helper / 테스트 / 문서**에 집중한다 — `RiskManager` / `PermissionGate` / `OrderExecutor` / `route_order`의 기존 흐름은 변경하지 않는다 (CLAUDE.md 절대 원칙).

## 2. Freshness 기준 (4축)

| 기준 | 신호 출처 | 코드 위치 | 비고 |
|---|---|---|---|
| **quote timestamp** | `broker.get_price`의 응답 timestamp | `is_quote_stale` | RiskManager #143 가드와 같은 축 |
| **tick last_seen_at** | KIS WebSocket / push tick 메시지 | 동일 (호출자가 last_seen_at 채워줌) | Phase 2 — KIS WebSocket 통합 후 |
| **bar cache fetched_at** | `MarketBar.fetched_at` (yfinance / KIS 분봉) | `is_bar_stale` (← `staleness.py` 재사용) | #171 가드 강화 |
| **websocket last_message_at** | `DataFeedState.last_message_at` + `connected` + `reconnecting` | `is_feed_stale` | Phase 2 — WebSocket 통합 후 외부 입력 |

각 축은 동일한 `FreshnessStatus` DTO로 결과를 반환한다:

```python
FreshnessStatus(
    symbol="005930", source="quote",
    is_stale=True,
    age_seconds=120.0,
    last_seen_at=...,
    max_age_seconds=60,
    reason="quote data stale (120s > 60s threshold)",
    checked_at=...,
)
```

## 3. 기본 threshold

| 모드 | `stale_price_max_age_seconds` 권장 | 근거 |
|---|---|---|
| `SIMULATION` | 60 (default) 또는 더 완화 | 외부 시세 의존 적음 — 가드 자체가 약식 |
| `PAPER` | 60 (default) | KIS PAPER 호스트, 분봉 폴링 정상이면 충분 |
| `LIVE_SHADOW` | **30~60 권장** | 실 시세 read-only — 보수적으로 설정 |
| `LIVE_MANUAL_APPROVAL` | **15~30 권장** | 사용자 승인 단계 — 시세가 stale하면 큐 자체를 거부 |
| `LIVE_AI_*` | **10~15 권장** | AI 자동 실행 — 가장 보수적. 본 시점 비활성 (`ENABLE_AI_EXECUTION=false`) |

`stale_price_max_age_seconds=0` (또는 음수)이면 **시간 기준 검사 비활성**. `last_seen_at`이나 `connected` 같은 이산 신호는 계속 검사한다 (안전 측).

## 4. 신규 BUY 차단 조건

운영자가 freshness helper를 `route_order` pre-check에 wire하는 시점(별도 옵트인 PR)에 적용. 본 PR에서는 helper만 제공.

| 조건 | source | helper |
|---|---|---|
| `quote.timestamp` 미존재 | quote | `should_block_buy_for_quote(last_seen_at=None)` → block |
| `quote.timestamp` 가 `max_age_seconds` 초과 | quote | `should_block_buy_for_quote(...stale...)` → block |
| `MarketBar.fetched_at` 미존재 (캐시 비어있음) | bar | `should_block_buy_for_bar(...)` → block |
| `MarketBar.fetched_at` stale | bar | 동일 |
| WebSocket `connected=False` | feed | `should_block_buy_for_feed(...)` → block |
| WebSocket `reconnecting=True` | feed | 동일 (시간 기준과 무관, 즉시 차단) |
| `last_message_at` 미존재 | feed | 동일 |
| `last_message_at` stale | feed | 동일 |

각 helper는 `(block, reason, status)` 튜플을 반환한다 — 호출자가 audit row의 `reasons` 목록에 그대로 추가 가능.

### 사용 예 (Phase 2 옵트인 PR에서 wire 예정)

```python
# 본 PR에서는 호출하지 않음 — 정책과 helper만 제공.
from app.market.freshness import should_block_buy_for_feed

if order.side == OrderSide.BUY:
    block, reason, _ = should_block_buy_for_feed(
        symbol=order.symbol,
        feed=feed_health.snapshot(),
        max_age_seconds=settings.stale_price_max_age_seconds,
    )
    if block:
        # OrderAuditLog에 REJECTED + reason 기록 후 종료.
        ...
```

## 5. SELL / 청산 정책

**자동 차단하지 않는다.** SELL 신호는 위험 축소 목적인 경우가 많고, 시세가 stale해도 *현 포지션이 손실 확장 중*일 수 있어 무차별 차단이 더 위험하다.

| 시나리오 | 권장 정책 |
|---|---|
| stop-loss 강제 청산 | freshness 무시하고 진행. broker가 거부하면 audit REJECTED |
| take-profit 청산 | freshness 가드 약화 — `max_age_seconds`를 BUY보다 2~3배로 |
| Agent 권장 청산 | freshness 평가 후 운영자 승인 필요 (LIVE_MANUAL) |
| 시간 기반 자동 청산 | `app/virtual/auto_close.py`(#172) 흐름 그대로 — RiskManager 가드 정상 적용 |

**LIVE 활성화 전 명확한 정책 결정 필요** — 본 단계에서는 helper가 *BUY 전용*임을 명시 (`should_block_buy_for_*`). SELL용 helper는 운영 데이터 누적 후 별도 PR로 도입한다.

## 6. RiskManager 연계

기존 #143 가드는 그대로 유지된다 — 본 PR은 `RiskManager.evaluate_order` 본체를 변경하지 않는다.

| 가드 | 코드 위치 | 작동 |
|---|---|---|
| #143 quote stale | `RiskManager.evaluate_order` | `latest_price_timestamp` + `stale_price_max_age_seconds` → `REJECTED("latest price is stale (...)")` |
| #171 bar cache stale | `app/market/staleness.py` | 호출자가 `route_order` pre-check에서 사용 |
| **#20 통합 freshness** | `app/market/freshness.py` (본 PR) | quote / bar / feed 통합 helper. **호출자가 사용 결정** |

reason 문구 매트릭스:

| 사유 코드 / 표현 | 출처 |
|---|---|
| `latest price is stale (Xs > Ys threshold)` | RiskManager #143 |
| `quote data stale (Xs > Ys threshold)` | freshness.is_quote_stale |
| `quote data missing (no timestamp recorded)` | freshness.is_quote_stale |
| `bar:1m data stale (Xs > Ys threshold)` | freshness.is_bar_stale |
| `bar:1m data missing (no timestamp recorded)` | freshness.is_bar_stale |
| `data feed reconnecting` | freshness.is_feed_stale |
| `data feed disconnected` | freshness.is_feed_stale |
| `feed data missing (no timestamp recorded)` | freshness.is_feed_stale |
| `feed data stale (Xs > Ys threshold)` | freshness.is_feed_stale |

## 7. WebSocket 재연결 중 정책

| 영역 | 권장 동작 |
|---|---|
| **신규 BUY** | **금지** — `should_block_buy_for_feed(reconnecting=True)` → block 즉시 |
| **신규 SELL** | 케이스별 — stop-loss는 진행, take-profit은 보수적 보류 ([5절](#5-sell--청산-정책)) |
| **Agent 판단** | `WATCH`로 강등 권장 — 새 진입 신호는 만들지 않고 모니터링만 |
| **virtual order 생성** | `rejected_by_freshness` 사유로 audit REJECTED 권장 (Phase 2 통합 시) |
| **운영자 알림** | dashboard에 reconnect 배너 권장 (Phase 2 — frontend 카드) |

## 8. API (read-only)

`GET /api/market/freshness` — 운영자 / dashboard가 상태를 조회할 수 있는 read-only endpoint.

| Query | 의미 |
|---|---|
| `symbol` | 종목코드 (필수) |
| `source` | `quote` 또는 `bar` (기본 `quote`) |
| `last_seen_at` | quote 호출 시 broker 응답 timestamp (선택) |
| `max_age_seconds` | 임계값. 미지정 시 `settings.stale_price_max_age_seconds` |
| `interval` | bar 호출 시 필수 — `1m / 5m / 1h / 1d` |

응답은 `FreshnessStatus` DTO. 실 broker API 호출 0건 — 단순 상태 계산 + DB MarketBar 조회만 수행.

## 9. 향후 과제 (Backlog)

| 항목 | 트리거 |
|---|---|
| KIS / Kiwoom WebSocket 실 feed 상태 통합 (`DataFeedState` provider) | KIS WebSocket adapter PR (Phase 2) |
| Redis 기반 feed health 공유 (다중 worker 동기화) | LIVE 활성화 + 다중 host 운영 시 |
| frontend freshness status card (Dashboard 또는 StrategyRisk 탭) | UI 추가 요청 시 |
| stale 이벤트 audit log row (`OrderAuditLog`에 `rejected_by_freshness` reason carry) | LIVE 활성화 PR |
| `route_order` pre-check에 `should_block_buy_*` wire | LIVE 활성화 PR (별도 옵트인) |
| SELL 전용 freshness helper (BUY와 다른 정책) | 운영 데이터 누적 후 |
| `DataFeedState`를 reconciliation / dashboard 카드와 연동 | UI Phase 2 |

## 10. 안전 invariant (본 PR이 지키는 것)

- broker live order 호출 0건 — `freshness.py`는 broker import 0건.
- `RiskManager` / `PermissionGate` / `OrderExecutor` / `route_order` 분기 변경 0건.
- 기존 #143 stale guard 회귀 테스트 통과.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건.
- frontend secret 노출 0건.
- 외부 네트워크 호출 0건 (테스트는 in-memory SQLite + 고정 `now`).

## 관련 문서

- [`api_limits.md`](api_limits.md) — KIS quote 호출 한도 / 폴링 정책
- [`market_data_collector.md`](market_data_collector.md) — OHLCV 수집 + bar cache 영구화 (`fetched_at` 출처)
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — 가드 체인 (#143 stale guard)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그
- [`broker_selection.md`](broker_selection.md) — adapter별 freshness 신호 차이
