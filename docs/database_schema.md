# Database Schema

체크리스트 #17 점검 — 본 문서는 현재 구현된 SQLAlchemy 모델과 Alembic migration을 한 번에 매핑하고, 체크리스트 원문 테이블명과의 차이를 명시한다. **기존 테이블 drop/rename 없이** 현재 시스템을 깨지 않는 방향으로 정합성과 인덱스 정책을 정리한다.

## 목적

- 현재 DB 스키마의 단일 진실 — 어떤 테이블이 있고, 어디서 쓰이며, 인덱스/유니크 제약은 무엇인가.
- 체크리스트 #17 원문 테이블 (`symbols`, `watchlists`, `ticks`, `candles`, `orders`, `trades`, `positions`, `strategy_runs`, `risk_events`, `agent_reports`)과 본 프로젝트 구현 테이블의 매핑.
- 인덱스 정책 (시간계열 / 심볼 / 모드 / 결정 / 체인 id 축).
- PostgreSQL 운영 전환 가이드 — 현재는 SQLite 개발, 운영은 PG 권장.
- 대량 데이터(틱/오더북) 정책과 미구현 장기 운영 테이블의 후보 자리.

## 현재 DB 기술 스택

| 레이어 | 구성 |
|---|---|
| ORM | SQLAlchemy 2.0 (`Mapped[...]` typed columns, `app/db/base.py::Base`) |
| Migration | Alembic (`backend/alembic/`, head=`0014`) |
| 개발 DB | SQLite (`sqlite:///./data/auto_trader.db` 기본, `DATABASE_URL`로 override) |
| 운영 DB (권장) | PostgreSQL — `DATABASE_URL=postgresql+psycopg://...` |
| Session | `app/db/session.py::SessionLocal` + FastAPI `Depends(get_db)` |
| Startup migration | `apply_migrations()` — backend lifespan에서 `alembic upgrade head` 자동 실행 |

`app/db/session.py`는 SQLite 경로일 때만 `check_same_thread=False`를 적용하고, 그 외(PG/MySQL)는 connect_args 없이 동일 코드로 동작한다 — DB 교체는 `DATABASE_URL` 변경만으로 가능.

## 현재 테이블 목록

총 **12개 테이블** (Alembic head=`0016`).

| 테이블 | 모델 | 첫 등장 migration | 목적 (요약) |
|---|---|---|---|
| `order_audit_log` | `OrderAuditLog` | 0001 | 모든 주문 결정·체결·AI 메타 단일 진실 |
| `backtest_run` | `BacktestRun` | 0001 | 백테스트 실행 입력·지표·체결 |
| `pending_approval` | `PendingApproval` | 0001 | LIVE_MANUAL_APPROVAL 승인 큐 |
| `ai_analysis_log` | `AiAnalysisLog` | 0001 | AI 분석 요청/응답 + 토큰/모드 |
| `market_bar` | `MarketBar` | 0001 | OHLCV 봉 캐시 (yfinance / KIS quote) |
| `emergency_stop_event` | `EmergencyStopEvent` | 0002 | 긴급 정지 토글 이력 |
| `virtual_order` | `VirtualOrder` | 0009 | 가상 주문 7-state 라이프사이클 |
| `futures_order_audit_log` | `FuturesOrderAuditLog` | 0013 | 선물 주문 별도 감사 로그 |
| `agent_decision_log` | `AgentDecisionLog` | 0014 | 10-Agent Council 결정 영구화 |
| `watchlist` | `Watchlist` | 0015 | 운영자 universe 그룹 (#18) |
| `watchlist_item` | `WatchlistItem` | 0015 | watchlist 종목 행 (#18) |
| `theme_signals` | `ThemeSignal` | 0016 | 테마/뉴스/트렌드 후보 필터 (#22) — 주문 신호 아님 |

자세한 컬럼 정의: `backend/app/db/models.py`.

## 체크리스트 원문 테이블 매핑표

체크리스트 #17 원문은 일반적인 자동매매 플랫폼 표준 테이블 10개를 나열한다. 본 프로젝트는 동일 의도를 다른 이름/구조로 이미 충족하거나, 후속 단계(#18 등)로 분리한다.

| 원문 (#17) | 본 프로젝트 매핑 | 상태 | 비고 |
|---|---|---|---|
| `symbols` | (현재 없음) — broker / market / watchlist에서 symbol **문자열**로만 사용 | **미구현 (의도적)** | 종목 마스터 테이블 후보 (`symbol_master`). KRX 전체 마스터는 데이터 갱신 빈도/소유권 결정 후 도입 |
| `watchlists` | `watchlist` + `watchlist_item` | **구현 완료** (#18, 0015) | 200개 한도 강제 ([`watchlist_policy.md`](watchlist_policy.md)) |
| `ticks` | (현재 없음) | **Phase 2 후보** | 대량 데이터 — TimescaleDB 또는 파티셔닝 결정 후. MVP는 `MarketBar`(분봉)로 충분 |
| `candles` | `market_bar` | **구현 완료** | OHLCV + (symbol, interval, timestamp) UNIQUE |
| `orders` | `order_audit_log` (결정) + `virtual_order` (라이프사이클) | **구현 완료** | 두 축으로 분리 — audit는 평면 기록, virtual_order는 상태 전이 |
| `trades` | `order_audit_log.filled_*` 필드 + `futures_order_audit_log` (선물) | **구현 완료** | 별도 trades 테이블 없음 — 같은 audit row가 결정 + 체결을 함께 보유 |
| `positions` | (영구 테이블 없음) — broker 호출(`get_positions`) + `virtual.position_engine` 메모리 + `reconciliation/position_checker` audit 기반 재구성 | **미구현 (런타임 계산)** | LIVE 활성화 시 `position_snapshot` 후보 — 일별 마감 스냅샷 |
| `strategy_runs` | `backtest_run` | **부분 구현** | 백테스트만 영구화. LIVE strategy run은 `LiveStrategyEngine` 메모리 + `OrderAuditLog.strategy` 컬럼으로 사후 재구성 |
| `risk_events` | `emergency_stop_event` + `order_audit_log.decision`/`reasons` | **구현 완료** | 토글 이력 + 거부 사유 모두 audit row |
| `agent_reports` | `ai_analysis_log` (단발 분석) + `agent_decision_log` (Council 체인) | **구현 완료** | 두 테이블 — 단일 호출 vs 다 agent chain 분리 |

→ 원문 10개 중 6개 구현 완료 / 1개 #18 별도 진행 / 3개 운영 단계 옵트인 후보. 본 PR에서 신규 테이블 생성 없음.

## 핵심 테이블 설명

각 테이블의 목적, 주요 컬럼, 인덱스, 사용처, 실거래 의미, 보강 필요 여부.

### `order_audit_log`

| 항목 | 내용 |
|---|---|
| 목적 | 모든 주문 요청 → 리스크 결정 → 브로커 체결을 **한 행**에 기록 (단일 진실) |
| 주요 컬럼 | `mode`, `symbol`, `side`, `quantity`, `decision`, `reasons`, `requested_by_ai`, `client_order_id`(140 idempotency), `ai_decision_meta`(152), `archived`(168), `executed`, `broker_order_id`, `filled_quantity`, `avg_fill_price` |
| 인덱스 | `created_at`, `mode`, `symbol`, `decision`, `strategy`, `client_order_id`, `archived` |
| Unique constraint | (없음) — `client_order_id` idempotency는 호출 단(`order_router`)에서 SELECT로 검증 |
| 사용 모듈 | `app/execution/order_router.py`, `app/api/routes_audit.py`, `app/reconciliation/position_checker.py` |
| 실거래 전 의미 | LIVE 활성화 핵심 — 모든 결정/체결이 여기에 남아야 사후 분석 가능 |
| 향후 보강 | LIVE 도달 시 `client_order_id` UNIQUE constraint 추가 검토 (현재는 SELECT 가드) |

### `backtest_run`

| 항목 | 내용 |
|---|---|
| 목적 | 단일 백테스트 실행에 대한 입력 + 지표 + 체결 (`trades_json`) |
| 주요 컬럼 | `strategy`, `params`, `initial_cash`, `bars_processed`, `total_pnl`, `win_count`, `loss_count`, `max_drawdown`, `data_*`, `trades_json` |
| 인덱스 | `created_at`, `strategy` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/api/routes_backtest.py`, `app/backtest/engine.py` |
| 실거래 전 의미 | 전략 효과 검증 결과 보존, `/api/strategies/scoreboard`에 합산됨 |
| 향후 보강 | `data_symbol` 인덱스는 현재 미적용 — symbol별 백테스트 비교가 늘면 추가 검토 |

### `pending_approval`

| 항목 | 내용 |
|---|---|
| 목적 | LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST 모드의 사용자 승인 대기 큐 |
| 주요 컬럼 | `audit_id`(FK→order_audit_log), `status`(PENDING/APPROVED/REJECTED/CANCELED/EXPIRED), `decided_by`, `note`, `attempts`(070 재평가 이력) |
| 인덱스 | `created_at`, `audit_id`, `symbol`, `status` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/permission/gate.py`, `app/api/routes_approvals.py` |
| 실거래 전 의미 | LIVE 진입 시 사용자 승인 게이트 — bypass 절대 금지 |
| 향후 보강 | TTL 자동 만료(167)는 이미 active. 대량 적체 시 archival 컬럼 검토 |

### `ai_analysis_log`

| 항목 | 내용 |
|---|---|
| 목적 | AI 분석 요청/응답/토큰 (실패도 기록) |
| 주요 컬럼 | `ticker`, `mode`(123), `text`, `model`, `input_tokens`, `output_tokens`, `score`, `error` |
| 인덱스 | `created_at`, `ticker` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/ai/service.py`, `app/api/routes_ai.py`, `app/ai/agent_stats.py` |
| 실거래 전 의미 | AI 비용 추적 + AI 거부/오류 분포 분석 |
| 향후 보강 | model별 인덱스는 빈도 낮아 미적용. 필요해지면 `model` index 검토 |

### `market_bar`

| 항목 | 내용 |
|---|---|
| 목적 | OHLCV 봉 캐시 — yfinance / KIS quote 중복 호출 회피 |
| 주요 컬럼 | `symbol`, `interval`, `timestamp`, `open/high/low/close/volume`, `fetched_at` |
| 인덱스 | `symbol`, `interval`, `timestamp` (3개 단일 인덱스) |
| Unique constraint | `uq_market_bar_key` (symbol, interval, timestamp) |
| 사용 모듈 | `app/market/bar_cache.py`, `app/backtest/loader.py` |
| 실거래 전 의미 | 백테스트 데이터 일관성 보장 |
| 향후 보강 | LIVE 분봉 폴링 도입 시 `(symbol, interval, timestamp DESC)` 복합 인덱스 검토. 보관기간 정책 결정 (cold storage) |

### `emergency_stop_event`

| 항목 | 내용 |
|---|---|
| 목적 | 긴급정지 토글 이력 — in-memory 토글의 영구 audit |
| 주요 컬럼 | `enabled`, `decided_by`, `note`, `reason_code`(153) |
| 인덱스 | `created_at`, `reason_code` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/api/routes_risk.py`, `app/risk/risk_manager.py` |
| 실거래 전 의미 | 사고 분석 시 누가 언제 어떤 사유로 켰는지 재구성 |
| 향후 보강 | reason_code별 분포 endpoint(208)는 이미 active. 추가 인덱스 불필요 |

### `agent_decision_log`

| 항목 | 내용 |
|---|---|
| 목적 | 10-Agent Council 결정 영구화 — chain_id로 의사결정 사슬 재구성 |
| 주요 컬럼 | `agent_name`, `symbol`, `mode`, `decision`, `confidence`, `reasons`, `meta`, `chain_id` |
| 인덱스 | `created_at`, `agent_name`, `symbol`, `mode`, `decision`, `chain_id` (6개) |
| Unique constraint | (없음) |
| 사용 모듈 | `app/ai/agents/council.py`, `app/api/routes_ai.py` (`/api/ai/agent-decisions*`) |
| 실거래 전 의미 | LIVE_AI_* 활성화 전 의사결정 투명성 핵심 |
| 향후 보강 | 인덱스 구성 이미 풍부 — 추가 불필요 |

### `futures_order_audit_log`

| 항목 | 내용 |
|---|---|
| 목적 | 선물 주문/청산/강제청산 별도 감사 (주식 audit과 스키마 다름) |
| 주요 컬럼 | `contract`, `leverage`, `liquidation_price`, `forced_liquidation`, `margin_delta` |
| 인덱스 | `created_at`, `mode`, `contract`, `decision`, `forced_liquidation` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/futures/mock.py`, `app/api/routes_futures.py` |
| 실거래 전 의미 | LIVE 선물은 영구 차단. 본 테이블은 시뮬 audit만 |
| 향후 보강 | `symbol` 동의어 필드는 `contract` — 별도 추가 불필요 |

### `virtual_order`

| 항목 | 내용 |
|---|---|
| 목적 | 가상 주문 7-state 라이프사이클 (NEW/ACCEPTED/PARTIALLY_FILLED/FILLED/CANCELLED/REJECTED/EXPIRED) |
| 주요 컬럼 | `audit_id`(FK), `status`, `structured_reason`, `strategy`, `mode`, `filled_*`, `note` |
| 인덱스 | `created_at`, `audit_id`, `symbol`, `status`, `strategy`, `mode` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/virtual/order_ledger.py`, `app/virtual/fill_engine.py`, `app/virtual/position_engine.py` |
| 실거래 전 의미 | 가상 자금 단일 진실. LIVE에서도 보존 — 사후 비교 가능 |
| 향후 보강 | 인덱스 충분 — 추가 불필요 |

### `watchlist` (#18)

| 항목 | 내용 |
|---|---|
| 목적 | 운영자가 수동으로 등록한 universe 그룹. Strategy / Agent의 후보군 — 주문 신호 아님 ([`watchlist_policy.md`](watchlist_policy.md)) |
| 주요 컬럼 | `name`, `description`, `is_active`(전역 1개만 활성) |
| 인덱스 | `created_at`, `name`, `is_active` |
| Unique constraint | (없음) |
| 사용 모듈 | `app/watchlist/service.py`, `app/api/routes_watchlists.py` |
| 실거래 전 의미 | RiskManager / PermissionGate / OrderExecutor 분기에 영향 없음 — universe 조회만 |
| 향후 보강 | 다중 운영자 분리 시 `owner_id` FK 검토 |

### `watchlist_item` (#18)

| 항목 | 내용 |
|---|---|
| 목적 | watchlist 안의 종목 행 |
| 주요 컬럼 | `watchlist_id`(FK CASCADE), `symbol`, `name`, `market`, `sector`, `note` |
| 인덱스 | `created_at`, `watchlist_id`, `symbol` |
| Unique constraint | `uq_watchlist_item_symbol` (watchlist_id, symbol) — 그룹 내 중복 방지 |
| 사용 모듈 | 동일 |
| 실거래 전 의미 | 동일 — universe 후보군. 200개 한도는 코드(`WATCHLIST_MAX_ITEMS`)에서 강제 |
| 향후 보강 | KRX 종목 마스터(`symbol_master`)와 cross-reference 시 추가 인덱스 검토 |

## 인덱스 정책

본 프로젝트가 이미 따르는 원칙. **새 테이블/컬럼 추가 시 동일 원칙으로 검증**.

| 원칙 | 적용 대상 | 본 프로젝트 현황 |
|---|---|---|
| 시간계열 테이블은 `created_at` 또는 `timestamp` 인덱스 필수 | 모든 audit 계열 | ✓ 9개 테이블 모두 `created_at` indexed (market_bar는 `timestamp`) |
| `(symbol, interval, timestamp)` UNIQUE — 봉 데이터 중복 방지 | `market_bar` | ✓ `uq_market_bar_key` |
| `symbol` 인덱스 — 종목 단위 조회 | order/virtual/agent/audit | ✓ 모두 indexed |
| `status` / `decision` 인덱스 — 상태 필터 | order/virtual/approval/agent/futures | ✓ 모두 indexed |
| `mode` 인덱스 — 운용모드 필터 | order/virtual/agent/futures | ✓ 모두 indexed (audit/AI는 가드 #123) |
| `agent_name`, `chain_id` 인덱스 — Agent 분포·체인 조회 | `agent_decision_log` | ✓ 둘 다 indexed |
| `archived` 인덱스 — hot/cold 분리 | `order_audit_log` | ✓ 168 도입 |
| `reason_code` 인덱스 — 분포 endpoint(208) | `emergency_stop_event` | ✓ 0011 도입 |
| `client_order_id` 인덱스 — idempotency 검증(140) | `order_audit_log` | ✓ 0008 도입 |
| `forced_liquidation` 인덱스 — 강제청산 필터 | `futures_order_audit_log` | ✓ 0013 도입 |
| **중복 인덱스 만들지 말 것** | 모든 테이블 | UNIQUE constraint가 자동 생성하는 인덱스 위에 동일 컬럼 단일 인덱스를 또 만들지 않는다 |
| **장기 운영 데이터 없는 컬럼에 인덱스 추가 금지** | 모든 테이블 | endpoint 사용 패턴이 명확해진 후에만 추가 |
| 대량 tick / orderbook은 인덱스보다 **파티셔닝 또는 보관 정책** 우선 | 미래 `market_tick` | Phase 2 — TimescaleDB hypertable 또는 PG native 파티셔닝 결정 후 |

검증 — `python -c "from app.db.models import *; ..."`로 임포트하면 SQLAlchemy가 모델 정의에서 인덱스 카탈로그를 만들고, `alembic upgrade head` 실행 결과 SQLite/PG 모두에서 동일 인덱스가 생성된다.

## Migration Timeline

| Rev | 날짜 | 의미 |
|---|---|---|
| 0001 | 2026-05-05 | initial schema — 5개 테이블 (`order_audit_log`, `backtest_run`, `pending_approval`, `ai_analysis_log`, `market_bar`) |
| 0002 | 2026-05-05 | `emergency_stop_event` 신규 테이블 |
| 0003 | 2026-05-06 | `pending_approval.attempts` 컬럼 (070 재평가 이력) |
| 0004 | 2026-05-07 | `ai_analysis_log.mode` 컬럼 (123 mode별 분석) |
| 0005 | 2026-05-08 | `order_audit_log.trade_reason` (134 진입/청산 사유) |
| 0006 | 2026-05-09 | `order_audit_log.strategy` 인덱스 컬럼 (138) |
| 0007 | 2026-05-10 | `order_audit_log.signal_strength + signal_confidence` (139) |
| 0008 | 2026-05-11 | `order_audit_log.client_order_id` 인덱스 컬럼 (140 idempotency) |
| 0009 | 2026-05-12 | `virtual_order` 신규 테이블 (148) |
| 0010 | 2026-05-13 | `order_audit_log.ai_decision_meta` JSON 컬럼 (152) |
| 0011 | 2026-05-14 | `emergency_stop_event.reason_code` 인덱스 컬럼 (153) |
| 0012 | 2026-05-15 | `order_audit_log.archived` 인덱스 컬럼 (168) |
| 0013 | 2026-05-16 | `futures_order_audit_log` 신규 테이블 (169) |
| 0014 | 2026-05-17 | `agent_decision_log` 신규 테이블 (185) |
| 0015 | 2026-05-18 | `watchlist` + `watchlist_item` 신규 테이블 (#18) |
| 0016 | 2026-05-20 | `theme_signals` 신규 테이블 (#22) — used_for_order invariant |

체인은 단일 linear (병렬 head 없음). `python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; ..."`로 head 검증 — 본 PR 작성 시 `heads=['0014']`.

## PostgreSQL 운영 전환 가이드

### 현재 호환성

- `app/db/session.py`는 SQLite 전용 옵션(`check_same_thread=False`)을 SQLite URL일 때만 적용. PG/MySQL URL은 추가 설정 없이 동작.
- 모델 컬럼 타입은 PG/SQLite 양쪽 호환 (`Integer`, `String(N)`, `DateTime`, `Boolean`, `Float`, `JSON`, `Text`).
- Alembic upgrade는 `op.create_table` / `op.create_index` 표준 API만 사용 — PG/SQLite 모두 동작.

### 전환 절차

1. PG 서버 준비 + 빈 DB 생성 (`createdb auto_trader`).
2. `psycopg[binary]` 또는 `psycopg2-binary`를 `requirements.txt`에 추가 (현재는 SQLite 전용이라 미포함).
3. `DATABASE_URL=postgresql+psycopg://user:pwd@host:5432/auto_trader`를 `backend/.env`에 설정.
4. backend 시작 — lifespan이 `alembic upgrade head` 자동 실행.
5. SQLite → PG 데이터 이행이 필요하면 `pgloader` 또는 별도 export/import 스크립트 (본 단계에는 비필요).

### PG 운영 시 주의

- JSON 컬럼은 PG에서 `jsonb`가 아닌 `json` 타입이 된다 (`sa.JSON()`이 dialect default 매핑). 운영 단계에서 jsonb 인덱스가 필요하면 `JSONB`로 명시 마이그레이션 별도 추가.
- 시간 컬럼은 모두 naive `DateTime` (UTC 기준 저장 — `_utcnow()` 헬퍼). 운영 PG에서 `timestamptz`로 마이그레이션이 필요하면 별도 PR.
- Connection pool은 SQLAlchemy 기본값 — LIVE 운영에서 worker 수 결정 후 `pool_size` / `max_overflow` 튜닝.

### 호환성 원칙

**SQLite와 PostgreSQL 모두 호환되게 작성**한다 — PG-only feature(`array`, `jsonb`, `tstzrange`, partial index 일부 등)는 본 PR에서 도입하지 않는다. 도입이 필요해지면 `op.execute(...)`로 PG branch만 실행 + SQLite는 무시하는 패턴으로 별도 PR.

## 대량 데이터 정책

| 데이터 | 예상 볼륨 | 본 프로젝트 정책 |
|---|---|---|
| 분봉(`market_bar`) | 종목당 1일 ~390 (1m) — 운영 시 100 종목 × 30일 ≈ 1.2M rows | 현재 인덱스로 충분. 운영 6개월 후 보관 정책(cold storage) 검토 |
| 틱 (`market_tick` 미구현) | 1종목·정규장 5만~수십만 row — 100종목이면 일 천만+ | **MVP 미도입** — TimescaleDB hypertable 또는 PG 파티셔닝 결정 후 별도 PR |
| 호가 (orderbook 미구현) | 더 큼 — 초당 수백 row × 종목 수 | **MVP 미도입** — 수집 자체가 인프라 결정 사항 |
| AI 호출 로그 | 일 수십~수백 행 (운영 비용 통제) | 현재 인덱스로 충분 |
| Agent decision | chain당 ~10 행 — 1일 chain 수십 가정 시 수백 행 | 현재 인덱스로 충분 |
| Audit (order/virtual/futures) | LIVE 활성화 전 일 수백 행 | `archived` 컬럼(168)으로 hot/cold 분리 가능 |

대량 데이터 도입 시 원칙 — **인덱스보다 파티셔닝 또는 보관 정책을 먼저** 결정. 인덱스 폭발은 쓰기 성능을 망친다.

## 아직 미구현인 장기 운영 테이블

운영 LIVE 단계에서 검토 후 도입 — 본 PR에서 신규 생성 없음.

| 후보 테이블 | 목적 | 트리거 | 매핑 |
|---|---|---|---|
| `symbol_master` | KRX 종목 마스터 (코드/이름/시장/상장폐지) | 운영자가 watchlist를 종목 마스터 기반으로 운용할 때 | 원문 `symbols` |
| ~~`watchlist` + `watchlist_item`~~ | 운영자 관심 종목 그룹 | ~~체크리스트 #18~~ — **0015에서 도입 완료** | 원문 `watchlists` |
| `market_tick` | 틱 데이터 — TimescaleDB hypertable 후보 | 분봉으로 부족한 마이크로 분석 필요 시 | 원문 `ticks` |
| `position_snapshot` | 일별 마감 포지션 스냅샷 | LIVE 활성화 — broker view vs audit view drift(212) 보강 | 원문 `positions` |
| `live_strategy_run` | LIVE strategy 실행 메타 (시작/종료/PnL) | LIVE_MANUAL_APPROVAL 운영 데이터 누적 후 | 원문 `strategy_runs` 보강 |
| `audit_archive` | hot 테이블 분리 (현재는 `archived` flag) | 168이 행 수 한도에 도달 시 | 운영 효율 |

## 체크리스트 #17 완료 판정

| 요구 | 상태 |
|---|---|
| PostgreSQL 스키마 설계 | **충족** — 모델/마이그레이션이 PG 호환, `DATABASE_URL`로 교체 |
| 원문 10개 테이블 매핑 | **완료** — 6 구현 / 1 #18로 이관 / 3 운영 옵트인 후보 |
| SQLAlchemy 모델 + Alembic migration 존재 | **완료** — 9 모델 / 14 migration linear chain (head=0014) |
| 테이블 생성 + 기본 인덱스 | **완료** — 모든 audit 계열 `created_at` + 도메인별 컬럼 인덱스 |
| 틱/분봉 시간 인덱스 | **완료 (분봉)** — `market_bar.timestamp` indexed + UNIQUE(symbol, interval, timestamp). 틱은 미도입 (대량 데이터 정책 별도) |
| 기존 테이블 drop/rename 0건 | **준수** — 본 PR은 문서만, 코드 변경 없음 |
| 기존 API contract 변경 0건 | **준수** |
| 주문/리스크/PermissionGate/OrderExecutor 변경 0건 | **준수** |

→ 체크리스트 #17 **PASS**. `symbols` / `ticks` / `positions` 등 미구현 테이블은 운영 단계 또는 별도 체크리스트(#18)에서 도입.

## 향후 작업

| 항목 | 트리거 |
|---|---|
| ~~`watchlist` + `watchlist_item` 테이블 도입~~ | ~~체크리스트 #18~~ — **완료 (0015)** |
| KRX `symbol_master` 도입 | 운영자가 watchlist를 종목 마스터로 관리하는 시점 |
| `market_tick` 도입 + TimescaleDB / 파티셔닝 결정 | 분봉 분석으로 부족한 마이크로 신호가 필요해진 시점 |
| `position_snapshot` 일별 스냅샷 | LIVE 활성화 후 reconciliation drift 누적 분석 |
| PG `jsonb` 마이그레이션 | JSON 컬럼 인덱스가 필요해지는 시점 (mode/strategy 분포 외) |
| `timestamptz` 마이그레이션 | 다중 시간대 운영 시점 |
| `client_order_id` UNIQUE constraint | LIVE place_order 활성화 PR (현재는 SELECT 가드) |
| Audit 분리 archive 테이블 | `archived` 행 수가 hot 쿼리 부담 임계 도달 시 |
| Connection pool 튜닝 (`pool_size` / `max_overflow`) | LIVE 운영 worker 수 결정 후 |

## 관련 문서

- [`architecture.md`](architecture.md) — 가드 체인 + 라우트 surface
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — RiskManager 평가 순서 + 27 가드
- [`api_limits.md`](api_limits.md) — DB와 직접 무관하지만 폴링/audit 연동 영향
- [`broker_selection.md`](broker_selection.md) — broker별 audit 매핑
- [`final_completion_summary.md`](final_completion_summary.md) — 데이터 모델 요약 (마이그레이션 별 의미)
