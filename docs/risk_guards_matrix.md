# Risk Guards Matrix

전체 RiskManager 가드 + 운영자 토글 + audit invariant를 한 페이지에 정리. 모든 가드는 단일 진입점 [`route_order()`](../backend/app/execution/order_router.py)에서 평가되며, [`PermissionGate.approve()`](../backend/app/permission/gate.py)의 re-eval에서도 동일하게 적용된다 (146 일관성).

## 평가 순서 (RiskManager.evaluate_order)

| # | 가드 | 동작 | PR |
|---|---|---|:--:|
| **0** | client_order_id idempotency | 같은 ID 두 번째 → 즉시 차단 | 140 |
| **0.5** | AI rate limit | (strategy, symbol) 윈도우 카운트 | 161 |
| **0.5** | Global rate limit | 모든 주문 윈도우 카운트 | 177 |
| **1** | Emergency stop | hard short-circuit (모든 reason 무시) | 060/153 |
| **1.1** | AI kill-switch | requested_by_ai=True 한정 hard short-circuit | 178 |
| **1.5** | Stale price | broker quote.timestamp 임계 초과 → REJECT | 143 |
| **2** | Symbol whitelist | order.symbol not in whitelist | 175 |
| **2.5** | Trading hours | KST 평일 09:00–15:30 외 거부 | 176 |
| **3** | AI confidence | requested_by_ai + signal_confidence < 임계 | 158 |
| **3.5** | AI reasoning | requested_by_ai + ai_decision_meta.reasons 빈 | 159 |
| **4** | max_order_notional | latest_price * qty > 절대 한도 | 001 |
| **5** | max_position_size_pct | notional > equity × pct | 174 |
| **6** | max_daily_loss | daily_realized_pnl ≤ -limit (KST 일자) | 145/166 |
| **7** | insufficient_cash | BUY 시 cash < notional | 001 |
| **8** | max_positions | BUY 신규 symbol + 보유 종목 ≥ 한도 | 001 |
| **9** | max_symbol_exposure | (현재 + 신규) > 종목별 한도 | 001 |
| **10** | max_total_exposure | sum(positions) + 신규 > 절대 한도 | 179 |
| **10.5** | max_total_exposure_pct | sum(positions) + 신규 > equity × pct | 179 |
| **11** | LIVE_SHADOW mode | 모드 자체로 REJECT | 001 |
| **12** | LIVE_MANUAL/AI_ASSIST early-return | enable_live_trading 가드 후 NEEDS_APPROVAL | 061 |
| **13** | AI execution gate | requested_by_ai + 모드 capability + flag | 152 |
| **14** | LIVE 가드 | LIVE_* 모드 + enable_live_trading=False | 001 |

> **Hard short-circuit** 가드(1, 1.1, 1.5)는 다른 reason 누적 안 함 — 단독 reason. 그 외는 reasons에 누적되어 마지막에 reason 1개라도 있으면 REJECTED.

## 운영자 토글 (in-memory + env)

| 기능 | RiskPolicy 필드 | env | RiskManager 메서드 |
|---|---|---|---|
| 긴급 정지 | `emergency_stop` (in-memory only) | — | `set_emergency_stop(bool)` |
| AI kill-switch | `policy.disable_ai_orders` | `DISABLE_AI_ORDERS` | `set_ai_disabled(bool)` |
| daily_realized_pnl | (내부 state) | — | `route_order` 매 호출마다 자동 갱신 (145) |

## RiskPolicy 한도 매트릭스

### 절대값 한도

| 항목 | 필드 | 기본값 | env |
|---|---|---:|---|
| 1회 주문 최대 명목 | `max_order_notional` | 1,000,000원 | `RISK_MAX_ORDER_NOTIONAL` |
| 일일 최대 손실 | `max_daily_loss` | 200,000원 | `RISK_MAX_DAILY_LOSS` |
| 종목별 최대 노출 | `max_symbol_exposure` | 1,500,000원 | `RISK_MAX_SYMBOL_EXPOSURE` |
| 총 노출 한도 | `max_total_exposure` | 0 (비활성) | `MAX_TOTAL_EXPOSURE` |
| 보유 종목 수 | `max_positions` | 5 | `RISK_MAX_POSITIONS` |

### 비율 한도 (자본 대비 자동 스케일)

| 항목 | 필드 | 기본값 | env |
|---|---|---:|---|
| 단일 주문 자본 % | `max_position_size_pct` | 0 (비활성) | `MAX_POSITION_SIZE_PCT` |
| 총 노출 자본 % | `max_total_exposure_pct` | 0 (비활성) | `MAX_TOTAL_EXPOSURE_PCT` |
| 종목별 노출 자본 % | `max_symbol_exposure_pct` | 0 (비활성) | `MAX_SYMBOL_EXPOSURE_PCT` |

### 자동화 가드

| 항목 | 필드 | 기본값 | env |
|---|---|---:|---|
| 연속 REJECTED 시 자동 stop | `auto_stop_consecutive_rejections` | 0 (비활성) | `AUTO_STOP_CONSECUTIVE_REJECTIONS` |
| 일일 최대 주문 횟수 (KST) | `max_orders_per_day` | 0 (비활성) | `MAX_ORDERS_PER_DAY` |

### 시간/window 한도

| 항목 | 필드 | 기본값 | env |
|---|---|---:|---|
| 시세 stale 최대 age | `stale_price_max_age_seconds` | 60s | `STALE_PRICE_MAX_AGE_SECONDS` |
| AI rate limit window | `ai_rate_limit_window_seconds` | 60s | `AI_RATE_LIMIT_WINDOW_SECONDS` |
| AI rate limit max | `ai_rate_limit_max_count` | 0 (비활성) | `AI_RATE_LIMIT_MAX_COUNT` |
| Global rate limit window | `global_rate_limit_window_seconds` | 60s | `GLOBAL_RATE_LIMIT_WINDOW_SECONDS` |
| Global rate limit max | `global_rate_limit_max_count` | 0 (비활성) | `GLOBAL_RATE_LIMIT_MAX_COUNT` |
| Approval TTL | `approval_ttl_seconds` | 0 (비활성) | `APPROVAL_TTL_SECONDS` |

### AI 가드

| 항목 | 필드 | 기본값 | env |
|---|---|---:|---|
| AI confidence 임계 | `min_ai_confidence` | 0 (비활성) | `MIN_AI_CONFIDENCE` |
| AI reasoning 강제 | `enforce_ai_reasoning` | true | `ENFORCE_AI_REASONING` |
| AI 주문 kill-switch | `disable_ai_orders` | false | `DISABLE_AI_ORDERS` |

### 운영 가드

| 항목 | 필드 | 기본값 | env |
|---|---|---|---|
| Symbol whitelist | `symbol_whitelist` | (빈 set, 비활성) | `SYMBOL_WHITELIST` (CSV) |
| 한국 시장 시간 강제 | `enforce_market_hours` | false | `ENFORCE_MARKET_HOURS` |
| 실거래 허용 | `enable_live_trading` | false | `ENABLE_LIVE_TRADING` |
| AI 자동실행 허용 | `enable_ai_execution` | false | `ENABLE_AI_EXECUTION` |
| 선물 실거래 허용 | `enable_futures_live_trading` | false | `ENABLE_FUTURES_LIVE_TRADING` |

## Submit ↔ Approve 일관성 (146/160)

`PermissionGate.approve()`의 re-eval에서도 모든 위 가드가 적용된다:
- ✅ stale_price (146)
- ✅ daily_realized_pnl 갱신 (146)
- ✅ AI confidence + reasoning (160)
- 모든 절대값/비율 한도 — RiskManager.evaluate_order 그대로 호출

submit 시점 통과한 주문이라도 approve 시점에 한도 변경 / stale 시세 / daily loss 누적 시 차단.

## audit invariant (134~140 + 151/152/168/169)

매 OrderAuditLog row가 보유하는 정보:
- `mode` / `decision` / `reasons` / `executed` (000)
- `trade_reason` (134) — strategy_signal / stop_loss / take_profit / manual / ai_recommendation / time_exit
- `strategy` (138) — registry 키
- `signal_strength` / `signal_confidence` (139)
- `client_order_id` (140) — idempotency 키
- `ai_decision_meta` (152) — AI 발신 주문의 decision context
- `archived` (168) — hot/cold 분리 flag

선물 주문은 별도 `FuturesOrderAuditLog` (169) — `contract` / `leverage` / `liquidation_price` / `forced_liquidation` 등 선물 고유 필드.

## 다층 안전 가드

CLAUDE.md 절대 원칙을 코드 단에서 강제하는 다중 방어 (architecture.md 참조):
- **RiskManager** — 위 14단계 평가
- **PermissionGate** — NEEDS_APPROVAL 큐 + 167 TTL
- **OrderExecutor** — 단일 함수로 broker 호출 + audit 갱신
- **KIS adapter** — `place_order(is_paper=False)` `NotImplementedError`
- **Factory** — `get_broker()` 가드
- **Engine** — `LiveStrategyEngine.submit_tick`이 거부 시 logical position 롤백
- **Futures** — 라이브 평가 영구 REJECTED + 가상 broker만 작동

## 관련 문서

- [`risk_policy.md`](risk_policy.md) — RiskPolicy 필드 정의 (역사적 진화 포함)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 기준
- [`ai_virtual_execution_report.md`](ai_virtual_execution_report.md) — AI 가드 158~165
- [`futures_simulation_report.md`](futures_simulation_report.md) — 선물 가상 환경
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 시 변경 매트릭스
