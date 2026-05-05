# Architecture

## 목표

기존 React UI는 관제·설정·승인 화면으로 유지하고, 실제 자동매매 엔진은 backend에 둔다. 모든 주문은 단일 가드 체인(`RiskManager → PermissionGate → OrderExecutor`)을 거치며, AI/선물/실거래는 명시적 옵트인 후에만 활성화된다.

## 전체 구조

```text
┌─────────────────────────────┐
│  frontend (React/Vite, PWA) │  11 탭
│  - Dashboard / Strategy·Risk│
│  - Bot / Approvals / Chart  │
│  - Backtest / AuditLog      │
│  - AISignal / LiveEngine    │
│  - Futures(stub) / Settings │
└──────────────┬──────────────┘
               │ REST (httpx)
               ▼
┌──────────────────────────────────────────────────────────────┐
│  backend (FastAPI + SQLAlchemy 2.0 + Alembic)                │
│                                                              │
│  routes ─┬─ /api/status, /api/risk/*                         │
│          ├─ /api/broker/* (price/balance/positions/orders)   │
│          ├─ /api/approvals/*  (PermissionGate queue)         │
│          ├─ /api/backtest/run, /runs/{id}                    │
│          ├─ /api/market/bars   (cache → adapter)             │
│          ├─ /api/strategies/*  (LiveStrategyEngine)          │
│          ├─ /api/ai/analyze   (Anthropic, audit only)        │
│          └─ /api/audit/*       (orders/ai/backtests)         │
│                                                              │
│  단일 진입점:                                                │
│      POST /api/broker/orders ─┐                              │
│      LiveStrategyEngine.tick ─┼─► route_order()              │
│      PermissionGate.approve ──┘                              │
│                                                              │
│  route_order:                                                │
│    broker.get_price/balance/positions                        │
│    → RiskManager.evaluate_order                              │
│    → OrderAuditLog (always)                                  │
│    → REJECTED / NEEDS_APPROVAL / APPROVED                    │
│                                                              │
│  REJECTED        → 400, audit committed                      │
│  NEEDS_APPROVAL  → 202, PendingApproval row                  │
│  APPROVED        → OrderExecutor.execute → broker.place_order│
│                                                              │
│  background:                                                 │
│    FillPoller (opt-in) — 주기적으로 RECEIVED 주문            │
│    상태를 broker.get_order_status로 갱신                     │
└──────────────┬──────────────┬───────────────┬───────────────┘
               │              │               │
               ▼              ▼               ▼
       ┌──────────────┐ ┌──────────┐ ┌──────────────────┐
       │ BrokerAdapter│ │ Market   │ │ Anthropic Claude │
       │              │ │ Data     │ │ (read-only AI)   │
       │ • MockBroker │ │ Adapter  │ │                  │
       │ • KIS (paper │ │          │ │ AI는 broker      │
       │   + shadow)  │ │ • Mock   │ │ 호출 권한 없음   │
       │ • KIS live*  │ │ • yfinance│ │                  │
       │ • Kiwoom*    │ │          │ │ AnalysisLog audit│
       │ • Futures(✗) │ │ + BarCache│ │                  │
       └──────────────┘ └──────────┘ └──────────────────┘
                * = stub (LIVE_MANUAL_APPROVAL PR 대기)
```

## 핵심 모듈

| 모듈 | 위치 | 역할 | 상태 |
|---|---|---|---|
| 운용모드 | `app/core/modes.py` | OperationMode enum + capability 표 | ✓ |
| 설정 | `app/core/config.py` | env-based 안전 플래그 | ✓ |
| Risk | `app/risk/risk_manager.py` | notional/cash/positions/exposure + mode-aware 분기 | ✓ |
| Broker (interface) | `app/brokers/base.py` | BrokerAdapter ABC | ✓ |
| MockBroker | `app/brokers/mock_broker.py` | 메모리 시세/체결 시뮬 | ✓ |
| KIS broker | `app/brokers/kis.py` + `kis_client.py` | get_price / balance / positions / order_status / place_order(paper) | ✓ |
| KIS live order | (same) | place_order(`is_paper=False`) | 🛑 stub |
| Permission | `app/permission/gate.py` | NEEDS_APPROVAL 큐, 승인/거부/취소 | ✓ |
| Executor | `app/execution/executor.py` | broker.place_order + audit 갱신 | ✓ |
| OrderRouter | `app/execution/order_router.py` | risk → audit → 분기 단일 함수 | ✓ |
| FillPoller | `app/execution/fill_poller.py` | 백그라운드 체결 갱신 (opt-in) | ✓ |
| Market | `app/market/{base,mock,yfinance_adapter,cache}.py` | OHLCV 어댑터 + DB 캐시 | ✓ |
| Backtest | `app/backtest/{engine,types,loaders}.py` | 결정론적 봉 단위 백테스트 | ✓ |
| Strategies | `app/strategies/{base,concrete/,live_engine}.py` | Strategy ABC + SmaCrossover + LiveStrategyEngine | ✓ |
| AI | `app/ai/{client,service}.py` | Anthropic 호출 + 점수 파싱 | ✓ |
| Futures | `app/futures/{base,mock,risk,types}.py` | 인터페이스 + stub | 🛑 stub |
| DB models | `app/db/models.py` | OrderAuditLog / BacktestRun / MarketBar / AiAnalysisLog / PendingApproval | ✓ |

## 주요 데이터 흐름

### 1. 주식 주문 (HTTP)

```
POST /api/broker/orders
   ↓
get_broker() → Mock | KIS  (운용모드에 따라)
   ↓
route_order:
   • broker.get_price/balance/positions
   • RiskManager.evaluate_order (mode-aware)
   • OrderAuditLog (decision=APPROVED|REJECTED|NEEDS_APPROVAL)
   ↓
REJECTED       → HTTP 400
NEEDS_APPROVAL → HTTP 202 + PendingApproval row
APPROVED       → OrderExecutor.execute → broker.place_order
                 → audit 갱신 (executed=True, broker_status, ...)
                 → HTTP 200 + OrderResult
```

### 2. 백테스트 (HTTP)

```
POST /api/backtest/run
  body: { strategy, params, bars[] OR (symbol, start, end, interval) }
   ↓
build_strategy(name, params)
   ↓
[market mode] BarCache.get → cache hit OR adapter.get_bars + cache.save
[bars mode  ] use provided bars
   ↓
BacktestEngine.run(bars, strategy)
   → 봉 단위 walk + Strategy.on_bar
   → BUY/SELL signal 감지 + 단일 long position simulation
   ↓
BacktestRun row + trades_json
   → HTTP 200 + summary + trades
```

### 3. LiveStrategyEngine (HTTP)

```
POST /api/strategies/configure {strategy, params, quantity}
   → 모듈-level 싱글톤 인스턴스 생성

POST /api/strategies/tick {bar, submit?}
   ↓
engine.run_tick(bar)
   → strategy.on_bar(history) → Signal
   → intended_order (BUY/SELL/HOLD에 따라)
   ↓
[submit=true] route_order(intended_order, ...)
              → 동일 가드 체인 (Risk/Permission/Executor)
              → REJECTED 시 engine.rollback_intent (포지션 상태 복원)
              → HTTP 200 + signal + intended_order + routing
[submit=false] HTTP 200 + signal + intended_order  (큐/실행 없음)
```

### 4. 승인 큐 (LIVE_MANUAL_APPROVAL)

```
POST /api/broker/orders (mode=LIVE_MANUAL_APPROVAL)
   ↓ route_order → RiskManager → NEEDS_APPROVAL
   ↓ PermissionGate.submit → PendingApproval row
   → HTTP 202 + approval_id

GET  /api/approvals          (운영자가 큐 확인 - PENDING만)
GET  /api/approvals/history  (?status=APPROVED|REJECTED|CANCELLED&limit&offset)
POST /api/approvals/{id}/approve
   → OrderExecutor → broker.place_order → audit 갱신
   → PendingApproval.status = APPROVED
POST /api/approvals/{id}/reject
   → PendingApproval.status = REJECTED (broker 호출 없음, 능동 거부)
POST /api/approvals/{id}/cancel
   → PendingApproval.status = CANCELLED (broker 호출 없음, 중립적 폐기)
```

## 다층 안전 가드

CLAUDE.md 절대 원칙(특히 "AI는 broker 직접 호출 금지", "모든 주문은 가드 체인 통과")을 코드 레벨에서 강제한다.

### Risk 단계
- `RiskManager.evaluate_order`: notional/cash/positions/exposure 한도 + 운용모드 분기
- LIVE_SHADOW: 모든 주문 REJECTED
- LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST: NEEDS_APPROVAL
- LIVE_AI_EXECUTION + AI 요청: `enable_ai_execution=False`면 REJECTED

### Permission 단계 (NEEDS_APPROVAL 시)
- PendingApproval 행에 OrderRequest 스냅샷 + 메타 저장
- 사용자만 승인/거부/취소 가능 — REJECTED는 능동적 거부, CANCELLED는 신호 만료/중립적 폐기
- 이미 결정된 항목 재결정 차단 (409 Conflict)

### Executor 단계 (APPROVED 시)
- 단일 함수 `OrderExecutor.execute`로 broker 호출 + audit 갱신 일원화
- 호출자가 commit 시점 결정 (다단계 트랜잭션 지원)

### 어댑터 단계 (KIS 한정)
- `KisBrokerAdapter.place_order`: `is_paper=False`이면 NotImplementedError (LIVE 라우팅 미구현)
- `KisBrokerAdapter.cancel_order`: NotImplementedError (write op)
- `get_broker()` 팩토리: `DEFAULT_MODE=PAPER` + `KIS_IS_PAPER=False`이면 시작 거부

### 선물
- `MockFuturesBroker`: 모든 메서드 NotImplementedError
- `FuturesRiskManager`: `enable_futures_live_trading=False`면 모든 주문 REJECTED
- 외부 모듈에서 임포트 0건 (DI 비연결)

## 감사 로그

모든 의사결정은 DB에 영속화된다. CLAUDE.md "감사 로그 우선" 원칙.

| 테이블 | 기록 시점 | 주요 필드 |
|---|---|---|
| `order_audit_log` | 모든 주문 (성공/거부/대기) | mode, decision, reasons, executed, broker_order_id, filled_quantity |
| `pending_approval` | NEEDS_APPROVAL 시 | audit_id FK, snapshot, status, decided_by/at |
| `backtest_run` | 백테스트 1회 | strategy, params, 지표, trades_json, data_source |
| `ai_analysis_log` | AI 호출 (성공/오류/미설정) | ticker, score, model, tokens, error |
| `market_bar` | 시장 데이터 캐시 | (symbol, interval, timestamp) UniqueConstraint |

`/api/audit/*` 라우트로 모두 조회 가능. frontend 📜 로그 탭에 시각화.

## 안전 플래그 일람

| 변수 | 기본값 | 적용 위치 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | get_broker 분기, RiskManager mode-aware |
| `ENABLE_LIVE_TRADING` | `false` | RiskManager LIVE_* 가드 |
| `ENABLE_AI_EXECUTION` | `false` | RiskManager AI 가드 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | FuturesRiskManager (선물 모듈 활성화) |
| `KIS_IS_PAPER` | `true` | KisClient host + tr_id, KisBrokerAdapter.place_order |
| `MARKET_DATA_PROVIDER` | `mock` | get_market_data 분기 |
| `ENABLE_FILL_POLLING` | `false` | FillPoller 시작 |

자세한 매핑은 [`promotion_policy.md`](promotion_policy.md) 마지막 표.

## 관련 문서

- [`shadow_mode.md`](shadow_mode.md) — LIVE_SHADOW 운영자 가이드 (read-only 검증)
- [`paper_mode.md`](paper_mode.md) — PAPER 운영자 가이드 (KIS 모의투자 주문)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 기준 + 환경 플래그 매트릭스
- [`risk_policy.md`](risk_policy.md) — 손실 한도/노출 한도 정책
- [`broker_selection.md`](broker_selection.md) — 브로커 선정 비교
- [`agent_design.md`](agent_design.md) — AI 보조 모듈 설계 노트

## 향후 작업

| 단계 | 추가될 코드 |
|---|---|
| Live Manual | `KisBrokerAdapter.place_order(is_paper=False)`, `cancel_order`, get_broker LIVE 분기 |
| AI Assist | AI 분석 응답 → OrderRequest 변환기 |
| AI Execution | LIVE_AI_EXECUTION 모드 활성화 (별도 옵트인) |
| Futures Phase 1 | `FuturesRiskManager.evaluate_order` 실제 평가 (증거금/만기/등락률) |
| Realtime feed | LiveStrategyEngine.start/stop (WebSocket 또는 폴링) |
| Strategy v2 | ORB / VWAP / Confluence 등 추가 전략 |
