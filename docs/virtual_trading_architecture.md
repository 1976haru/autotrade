# Virtual Trading Architecture

가상 자동매매 시스템의 모듈 / 데이터 / 가드 체인 매핑. 라이브 broker 연결 0건
이 invariant. 실거래 활성화는 [`docs/live_activation_blockers.md`](live_activation_blockers.md) 참조.

## 시스템 다이어그램

```text
┌──────────────────────────────────────────────────────────────┐
│                       Frontend (Vite + React)                │
│   Tabs: Dashboard / Approvals / AuditLog / LiveEngine /       │
│         Backtest / StrategyRisk / AISignal / BotControl /     │
│         Settings / Futures / MarketChart                      │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTP (services/backend/client.js)
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                         │
│                                                              │
│  Routes (api/routes_*):                                      │
│    risk / approvals / broker / audit / backtest / market /   │
│    strategies / ai / futures (가상)                          │
│                                                              │
│  ┌─────────────────────── 주문 단일 진입점 ─────────────────┐ │
│  │  app/execution/order_router.py::route_order()           │ │
│  │   ├─ Step 0: client_order_id idempotency (140)          │ │
│  │   ├─ Step 1: broker.get_price/balance/positions         │ │
│  │   ├─ Step 1.5: stale price (143)                        │ │
│  │   ├─ Daily realized PnL refresh (145)                   │ │
│  │   ├─ RiskManager.evaluate_order (notional/cash/...)     │ │
│  │   │    ├─ APPROVED → OrderExecutor.execute              │ │
│  │   │    ├─ NEEDS_APPROVAL → PermissionGate.submit (큐)   │ │
│  │   │    └─ REJECTED → audit + return                     │ │
│  │   └─ 모든 분기에서 OrderAuditLog 1행 영구화              │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  주문 진입 경로:                                              │
│   - HTTP /api/broker/orders                                  │
│   - LiveStrategyEngine.submit_tick (전략 신호)                │
│   - VirtualAiAgent.propose_and_route (AI 가상)                │
│   - PermissionGate.approve (재평가 후 OrderExecutor)          │
│                                                              │
│  Virtual stack (147~155):                                    │
│   - app/virtual/order_ledger.py  (VirtualOrder lifecycle)    │
│   - app/virtual/fill_engine.py   (체결 시뮬)                 │
│   - app/virtual/position_engine.py (포지션 / PnL / close eval)│
│                                                              │
│  Futures stack (151):                                        │
│   - app/futures/{simulation,mock,risk,types}.py              │
│   - MockFuturesBroker (가상 broker)                          │
│   - FuturesRiskManager.evaluate_virtual_order (가상 평가)    │
│   - FuturesRiskManager.evaluate_order (라이브 — 항상 REJECT) │
│                                                              │
│  AI stack (152):                                             │
│   - app/ai/virtual_agent.py      (VIRTUAL_AI_EXECUTION)      │
│   - app/ai/client.py             (Anthropic SDK 래퍼, opt)   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
                ┌─────────────────┐
                │   SQLAlchemy    │
                │   + Alembic     │
                └─────────────────┘
                          │
                          ▼ 0001 → 0011
                ┌─────────────────────────────────┐
                │   SQLite / Postgres            │
                │ - order_audit_log              │
                │ - pending_approval             │
                │ - ai_analysis_log              │
                │ - emergency_stop_event         │
                │ - backtest_run                 │
                │ - market_bar                   │
                │ - virtual_order  (148 신설)    │
                └─────────────────────────────────┘

       Mock broker / Mock market / Mock futures broker
       — 외부 네트워크 호출 0건
```

## 모듈 트리

```text
backend/app/
├─ api/routes_*.py        # FastAPI endpoints
├─ ai/
│  ├─ client.py           # AnthropicAiClient (read-only 분석용)
│  ├─ service.py
│  └─ virtual_agent.py    # 152: VirtualAiAgent (VIRTUAL_AI_EXECUTION)
├─ brokers/
│  ├─ base.py             # BrokerAdapter ABC, OrderRequest (ai_decision_meta 포함)
│  ├─ mock_broker.py      # MockBrokerAdapter (가상 주식)
│  └─ kis.py              # KisBrokerAdapter — place_order(LIVE)는 NotImplementedError
├─ market/                # MockMarketData, yfinance adapter
├─ risk/
│  ├─ risk_manager.py     # 주식 RiskManager (143/145 가드)
│  ├─ daily_pnl.py        # 145
│  └─ emergency_reasons.py # 153
├─ permission/gate.py     # 070 + 146 가드 일관성
├─ execution/
│  ├─ order_router.py     # 단일 진입점
│  ├─ executor.py
│  └─ fill_poller.py
├─ strategies/            # Strategy ABC + concrete + scoreboard (137/144/147)
├─ backtest/              # BacktestEngine
├─ virtual/
│  ├─ order_ledger.py     # 148: VirtualOrder lifecycle
│  ├─ fill_engine.py      # 149: 시뮬 체결
│  └─ position_engine.py  # 150: 포지션 / PnL / close eval
├─ futures/
│  ├─ types.py            # FuturesQuote/Position/Balance/...
│  ├─ simulation.py       # 151: 산식 (margin/liquidation/...)
│  ├─ mock.py             # 151: MockFuturesBroker (in-memory)
│  └─ risk.py             # 151: FuturesRiskManager (live=REJECT, virtual=eval)
├─ db/                    # SQLAlchemy + Alembic
└─ core/                  # config, modes (VIRTUAL_AI_EXECUTION 추가), rate_limiter
```

## 데이터 흐름 — 가상 주식 주문

```text
Strategy.on_bar(bars)
  │
  ▼ Signal (BUY/SELL/HOLD)
LiveStrategyEngine.submit_tick(bar)
  │
  ▼ OrderRequest (strategy / signal_quality / ...)
route_order(requested_by_ai=False, mode=SIMULATION/LIVE_*)
  │
  ├ idempotency 검사 (client_order_id)
  ├ broker.get_price/balance/positions  (Mock or KIS)
  ├ stale price 검사
  ├ daily PnL 갱신
  ├ RiskManager.evaluate_order
  │   ├ APPROVED → OrderExecutor.execute → broker.place_order
  │   │           audit.executed=True, broker_status=FILLED
  │   ├ NEEDS_APPROVAL → PermissionGate.submit (PendingApproval 행)
  │   └ REJECTED → audit only, broker 호출 없음
  │
  ▼ OrderAuditLog 1행 영구화 (모든 분기)
```

## 데이터 흐름 — 가상 AI (152)

```text
VirtualAiAgent.propose_stub(symbol, last_close, prev_close, confidence)
  │
  ▼ AiProposal {symbol, side, quantity, confidence, reasons, extra_meta}
  │
  ▼ to_order_request() →
    OrderRequest {
      trade_reason="ai_recommendation",
      strategy="ai_virtual",
      signal_strength=confidence, signal_confidence=confidence,
      ai_decision_meta={confidence, reasons, rejected_by_guard, ...}
    }
  │
  ▼ propose_and_route(...)
  │
  ▼ route_order(requested_by_ai=True, mode=VIRTUAL_AI_EXECUTION)
  │
  ▼ (가드 체인은 일반 주식 흐름과 동일)
  │
  ▼ OrderAuditLog (requested_by_ai=True, ai_decision_meta JSON 영구화)
```

VIRTUAL_AI_EXECUTION 모드는 `live_order=False`라 `can_place_live_order` =
False — broker live endpoint 라우팅이 capability 단에서 차단된다.

## 데이터 흐름 — 가상 선물 (151)

```text
caller (테스트 또는 향후 FuturesEngine)
  │
  ▼ FuturesOrderRequest {contract, side, quantity, ...}
  │
  ▼ FuturesRiskManager.evaluate_virtual_order(...)
  │   ├ leverage / max_contracts / margin / max_margin / daily_loss 검사
  │   └ APPROVED 또는 REJECTED + reasons
  │
  ▼ MockFuturesBroker.place_order(...)
  │   ├ 슬리피지 적용 → fill_price
  │   ├ initial_margin / fee 산출
  │   ├ 신규 진입 / 동일 방향 추가 / 반대 방향 청산 분기
  │   ├ liquidation_price 산출 → FuturesPosition에 영구화
  │   └ FuturesOrderResult (FILLED / REJECTED / RECEIVED)
  │
  ▼ broker.force_liquidate_if_needed(contract)  (mark price drop 시)
  │   └ should_force_liquidate(pos, mark) → 자동 청산 → realized_pnl_today
```

라이브 경로 `FuturesRiskManager.evaluate_order(...)`는 본 PR에서 어떤 flag
조합으로도 항상 REJECTED. 라이브 활성화는 별도 옵트인 PR.

## 가드 체인 — 진입점별 일관성

| 진입점 | RiskManager | PermissionGate | OrderAuditLog | 비고 |
|---|---|---|---|---|
| HTTP `/api/broker/orders` | ✅ | 모드에 따라 | ✅ | route_order |
| LiveStrategyEngine.submit_tick | ✅ | 모드에 따라 | ✅ | 같은 route_order |
| VirtualAiAgent.propose_and_route | ✅ | 모드에 따라 | ✅ | requested_by_ai=True |
| PermissionGate.approve (재평가) | ✅ (146 가드 일관성) | 본인 | ✅ (executed flag) | broker 재호출 |
| MockFuturesBroker.place_order | (FuturesRiskManager는 caller가 호출) | (해당 없음) | (선물용 audit 미구현 — backlog) | 가상 환경 전용 |
| simulate_fill (149) | (caller가 ACCEPTED 상태로 통과시킴) | — | (VirtualOrder 자체가 ledger) | 시뮬 |

선물 audit는 현재 OrderAuditLog와 분리 — 별도 audit 테이블이 backlog.

## 안전 invariant 단정문

1. **단일 주문 진입점** — `route_order()` 외 broker 직접 호출 경로 0건. 본 invariant는 코드 단의 모든 호출자(HTTP, Engine, AI agent)가 본 함수만 거치도록 강제됨.
2. **AI 가드 체인 우회 0** — VirtualAiAgent는 route_order만 호출. AiClient.analyze는 read-only 분석용 (주문 안 만든다).
3. **선물 라이브 영구 차단** — `FuturesRiskManager.evaluate_order`는 항상 REJECTED. `MockFuturesBroker`는 라이브 endpoint 미사용.
4. **외부 네트워크 호출 (테스트 시점)** — 본 세션의 모든 테스트는 in-memory broker / market / fakedAI 사용. 실제 KIS / Anthropic API 호출 0건.
5. **모든 거부 사유 audit 기록** — emergency_stop / stale_price / max_daily_loss / notional 등 어떤 거부도 OrderAuditLog 1행 + reasons 누적.

## 관련 문서

- [`docs/risk_policy.md`](risk_policy.md)
- [`docs/promotion_policy.md`](promotion_policy.md)
- [`docs/strategies.md`](strategies.md)
- [`docs/futures_simulation_report.md`](futures_simulation_report.md)
- [`docs/ai_virtual_execution_report.md`](ai_virtual_execution_report.md)
- [`docs/stress_test_report.md`](stress_test_report.md)
- [`docs/live_activation_blockers.md`](live_activation_blockers.md)
- [`docs/backlog.md`](backlog.md)
- [`CLAUDE.md`](../CLAUDE.md)
