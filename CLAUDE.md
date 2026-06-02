# CLAUDE.md — Auto Trader 작업 지침

> **이 파일은 핵심 지침만 담는다.** 과거 PR/기능 변경이력·백테스트 검증 연구기록 전체는
> [`docs/claude_md_changelog_archive.md`](docs/claude_md_changelog_archive.md)로 분리했다 (2026-06-02 정리, 원본 백업 `CLAUDE.md.bak`).
> 특정 모듈을 수정할 때는 아래 "모듈 인덱스"에서 해당 항목의 `docs/*.md`를 먼저 확인한다.

## 프로젝트 정체성

이 프로젝트는 국내주식 단타 자동매매를 위한 **리스크 제한형 연구 플랫폼**이다. 초기 목적은 실거래 수익 자동화가 아니라, 데이터 수집·백테스트·모의투자·Shadow Mode·수동승인·AI 보조를 거쳐 검증 가능한 자동매매 시스템을 구축하는 것이다.

## 절대 원칙

1. **AI가 브로커 주문 API를 직접 호출하는 코드를 만들지 않는다.**
2. **모든 주문은 반드시 `RiskManager → PermissionGate → OrderExecutor` 순서를 거친다.**
3. **기본 운용모드는 `SIMULATION` 또는 `PAPER`이며, `LIVE_AI_EXECUTION`은 기본 비활성화한다.**
4. **API Key, App Secret, 계좌번호, Anthropic/OpenAI Key는 절대 frontend에 저장하거나 커밋하지 않는다.**
5. **프론트엔드는 관제·승인·설정 UI이며, 실제 증권사/AI API 호출은 backend에서만 수행한다.**
6. **선물 기능은 주식 MVP 이후 별도 `FuturesBrokerAdapter`, `FuturesRiskManager`로 확장한다.**

각 원칙은 코드 단에서 강제된다 — 자세한 매핑은 [`docs/risk_policy.md`](docs/risk_policy.md), [`docs/agent_design.md`](docs/agent_design.md), [`docs/architecture.md`](docs/architecture.md).

## 운용모드

| 모드 | 설명 | 코드 위치 |
|---|---|---|
| `SIMULATION` | 가짜 데이터 + MockBroker | 기본값 |
| `PAPER` | 실 시세 + KIS 모의투자 (가상 자금) | `KIS_IS_PAPER=true` 필수 |
| `LIVE_SHADOW` | 실 계좌/시세 read-only, 주문 금지 | RiskManager가 모든 주문 REJECTED |
| `LIVE_MANUAL_APPROVAL` | 사용자 승인 후 주문 | PermissionGate 큐 |
| `LIVE_AI_ASSIST` | AI 후보 + 사용자 승인 | (구현 예정) |
| `LIVE_AI_EXECUTION` | 제한 조건 하 AI 실행 | 기본 비활성, 8개 옵트인 조건 (`promotion_policy.md`) |

운영자 가이드: [`docs/shadow_mode.md`](docs/shadow_mode.md), [`docs/paper_mode.md`](docs/paper_mode.md).

## 단일 주문 진입점

모든 주문 경로(HTTP `/api/broker/orders`, `LiveStrategyEngine.submit_tick`, `PermissionGate.approve`)는 결국 `app/execution/order_router.py::route_order`를 통과한다. 이 함수가:

1. broker로 시세/잔고/포지션 조회
2. `RiskManager.evaluate_order` 평가
3. `OrderAuditLog` 기록 (성공/거부/대기 모두)
4. 분기: REJECTED (400) / NEEDS_APPROVAL (PermissionGate 큐) / APPROVED (`OrderExecutor.execute`)

새 주문 경로를 추가할 때는 반드시 `route_order`를 통과하도록 한다.

### 핵심 아키텍처 불변식 (#34~#43)

주문경로를 코드 단에서 고정하는 불변식. 전체 원문·테스트 가드는 [archive](docs/claude_md_changelog_archive.md) 블록1 참조.

- **#34 표준 진입점** — `RiskManager.check_order(order, context)`가 모든 호출자의 표준 메서드(`evaluate_order`는 alias). `OrderExecutor.execute`는 `audit.decision ∈ {APPROVED, NEEDS_APPROVAL}`만 broker로 진행, 그 외 `UnauthorizedOrderError`. [`docs/risk_manager_contract.md`](docs/risk_manager_contract.md)
- **#35 PositionLimitRule** (`app/risk/position_limits.py`) — 1회/종목별/총노출/보유수 한도의 단일 진실, RiskManager가 위임. 선물은 `FuturesRiskPolicy` 별도. [`docs/position_limit_policy.md`](docs/position_limit_policy.md)
- **#37 3-Level Kill Switch** (`app/risk/emergency_stop.py`) — OFF/L1/L2/L3. **자동 청산·자동 취소 절대 금지** (read-only candidate list만). [`docs/emergency_stop_policy.md`](docs/emergency_stop_policy.md)
- **#38 OrderGuard** (`app/risk/order_guard.py`) — RiskManager 평가 *전* `route_order`에서 호출되는 pre-trade 중복/쿨타임 가드. fingerprint 식별, 모든 cooldown/window default 0=비활성. [`docs/order_guard_policy.md`](docs/order_guard_policy.md)
- **#39 AI Permission Gate** (`app/risk/ai_permission_gate.py`) — 5단계×5행동 매트릭스. **AI API Key는 주문 권한이 아니다** (key/secret 입력 안 받음, broker import 0건). [`docs/ai_permission_gate.md`](docs/ai_permission_gate.md)
- **#40 OrderExecutor 단일 진입점** (`app/execution/order_executor.py`) — `OrderExecutor.execute`만이 `broker.place_order()`를 호출하는 *유일한* 코드. 모든 audit에 `source` carry (정적 grep 가드). [`docs/order_executor_contract.md`](docs/order_executor_contract.md)
- **#41 Manual Approval** — 첫 실거래는 PendingApproval 큐 + *운영자 명시 승인* 필수. `PermissionGate.approve`는 broker 호출 *전* RiskManager 재검증. [`docs/manual_approval_policy.md`](docs/manual_approval_policy.md)
- **#42 PaperTrader** (`app/execution/paper_trader.py`) — OrderExecutor wrapper, `assert_paper_broker` 후 위임. `is_live_broker(broker)`면 `NotPaperBrokerError`. [`docs/paper_trading_policy.md`](docs/paper_trading_policy.md)
- **#43 LIVE_SHADOW ShadowTrade** — 모든 주문 `REJECTED` + would-have 정보를 `ShadowTrade` row로 기록. `actual_broker_order_sent` invariant False. [`docs/live_shadow_trade_policy.md`](docs/live_shadow_trade_policy.md)

## 작업 방식

- 큰 기능은 작은 PR 단위로 쪼갠다.
- 새 기능은 테스트를 함께 추가한다 (backend pytest, frontend vitest).
- 금융 관련 로직은 수익률보다 **손실 방어와 감사 로그**를 우선한다.
- **랜덤 시뮬레이션 결과를 실제 성과로 표현하지 않는다.**
- 실제 주문 코드 작성 전 MockBroker, 테스트, 실패 케이스를 먼저 구현한다.
- LIVE / 선물 / AI 자동실행 활성화 PR은 운영자 명시 옵트인 후에만 머지.

### P0 모듈 테스트 정책 (#65)

돈이 걸릴 수 있는 자동매매 시스템이므로 다음 4개 모듈은 **테스트 없이 완료
처리하지 않는다**. P0 매핑/시나리오 매트릭스는 [`docs/unit_test_coverage_map.md`](docs/unit_test_coverage_map.md).

1. **RiskManager** (`app/risk/risk_manager.py`) ↔ `tests/test_risk_manager.py`
2. **OrderGuard** (`app/risk/order_guard.py`) ↔ `tests/test_order_guard.py`
3. **StrategyBase** (`app/strategies/base.py`) ↔ `tests/test_strategy_base_contract.py`
4. **BacktestEngine** (`app/backtest/engine.py`) ↔ `tests/test_backtest_engine.py` + `tests/test_backtest_execution_costs.py`

추가 규칙:
- 실거래 / LIVE 관련 코드(예: `is_paper=False` 분기, `ENABLE_LIVE_TRADING=true`
  활성화 경로)는 *테스트 없이 머지 금지*.
- 외부 API 의존 테스트는 mock / fake / NoOp / dry_run 사용 — 실 KIS /
  Anthropic / Telegram 호출 0건.
- stress / slow / network 테스트는 `*-ci-nightly.yml` 등 별도 워크플로로
  분리해 일반 CI flakiness를 방지.

### Staging 환경 정책 (#67)

- staging은 *운영과 별개* 환경으로, 신규 기능 smoke 테스트 + mock/paper/
  shadow 검증만 가능하다.
- staging에서 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` 을 **true로 설정 금지** —
  `docker-compose.staging.yml`에 "false" 문자열로 하드코딩, 실행 가이드는
  [`docs/staging_environment.md`](docs/staging_environment.md).
- 실 API key / Secret / 계좌번호를 `docker-compose.staging.yml` / `.env.
  staging.example`에 입력 금지. `.env.staging`(gitignore)에서만 주입.

## 안전 플래그

env 변수로 모든 위험 동작을 차단한다. 자세한 매트릭스는 [`docs/promotion_policy.md`](docs/promotion_policy.md).

| 변수 | 기본 | 효과 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | RiskManager 분기, broker 라우팅 |
| `ENABLE_LIVE_TRADING` | `false` | LIVE_* 모드에서 실거래 차단 |
| `ENABLE_AI_EXECUTION` | `false` | LIVE_AI_EXECUTION에서 AI 자동 실행 차단 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | 선물 모듈 거래 차단 |
| `KIS_IS_PAPER` | `true` | KisClient host + tr_id, KisBrokerAdapter.place_order 가드 |
| `MARKET_DATA_PROVIDER` | `mock` | 시장 데이터 소스 (`mock`/`yfinance`/`kis`). **`kis` 일 때만** KIS 실시간 시세로 Paper Auto 가 KIS 모의주문 전송 가능; `mock`/`yfinance` 는 전송 차단 |
| `ENABLE_FILL_POLLING` | `false` | 백그라운드 체결 갱신 |
| `STALE_PRICE_MAX_AGE_SECONDS` | `60` | RiskManager step 1.5 — 시세 timestamp가 N초 초과 oldness이면 hard-reject (143) |

## 다층 안전 가드

CLAUDE.md 절대 원칙을 코드 단에서 강제하는 다중 방어:

- **RiskManager** — notional/cash/positions/exposure + 운용모드 분기
- **PermissionGate** — NEEDS_APPROVAL 큐, 사용자 승인 필요, 이미 결정된 항목 재결정 차단
- **OrderExecutor** — 단일 함수로 broker 호출 + audit 갱신
- **KIS adapter** — `place_order(is_paper=False)` `NotImplementedError`
- **Factory** — `get_broker()`가 PAPER 모드 + `KIS_IS_PAPER=false`면 시작 거부
- **Engine** — `LiveStrategyEngine.submit_tick`이 거부 시 logical position 롤백
- **Futures** — 외부 모듈 임포트 0건, 모든 메서드 `NotImplementedError`

## 코드 구조 요약

```text
backend/app/
├─ api/routes_*.py        # FastAPI endpoints (status, risk, broker, approvals,
│                         #   backtest, market, strategies, ai, audit, virtual,
│                         #   futures, reconciliation)
├─ brokers/               # BrokerAdapter ABC + Mock + KIS
├─ market/                # MarketDataAdapter ABC + Mock + yfinance + BarCache
├─ risk/risk_manager.py   # 평가 + mode-aware 분기
├─ permission/gate.py     # 승인 큐
├─ execution/             # order_router (단일 진입점) + executor + fill_poller
├─ strategies/            # Strategy ABC + concrete + LiveStrategyEngine
├─ backtest/              # BacktestEngine + types + CSV loader
├─ ai/                    # AiClient (Anthropic) + service
├─ futures/               # 모든 모듈 stub (활성화 비활성)
├─ reconciliation/        # broker view vs audit view drift 감지 (212)
├─ db/                    # SQLAlchemy 2.0 + Alembic
└─ core/                  # config, modes, rate_limiter (정의만)

frontend/src/
├─ components/tabs/       # 11개 탭
│  ├─ Dashboard / StrategyRisk / BotControl / Approvals
│  ├─ MarketChart / Backtest / AuditLog / AISignal
│  └─ LiveEngine / Futures / Settings
├─ store/                 # 각 탭의 hook (useLiveEngine 등)
└─ services/backend/      # API client (단일 fetch wrapper)

docs/
├─ architecture.md         # 전체 구조
├─ promotion_policy.md     # 단계별 승격
├─ risk_policy.md          # 평가 순서 + 결정 매트릭스
├─ agent_design.md         # AI/code 분리
├─ shadow_mode.md          # LIVE_SHADOW 운영 가이드
├─ paper_mode.md           # PAPER 운영 가이드
├─ broker_selection.md     # 어댑터 비교 + 추가 체크리스트
└─ api_limits.md           # 호출 제한 정책
```

## 현재 단계 (참고)

- ✓ 주식 MVP 안정화 단계: SIMULATION + PAPER + LIVE_SHADOW 운영 가능
- ⏳ 다음: `LIVE_MANUAL_APPROVAL` 라우팅 (KIS LIVE place_order/cancel_order 활성화)
- 🛑 미진행: `LIVE_AI_*`, 선물 LIVE — 별도 옵트인 PR

자세한 단계 정의는 [`docs/promotion_policy.md`](docs/promotion_policy.md).

## 모듈 인덱스

기능별 상세(전체 불변식·테스트 수·실측 백테스트 기록)는 [`docs/claude_md_changelog_archive.md`](docs/claude_md_changelog_archive.md) 및 각 `docs/*.md`에 있다. **공통 불변식**: 아래 advisory/검증 모듈은 모두 `is_live_authorization=False`, 주문/실전 전환을 *직접 수행하지 않으며*, broker/OrderExecutor/route_order import 0건, 안전 플래그를 변경하지 않는다.

- **Agent (advisory only, 주문신호 아님)** — #51 Agent architecture(6 roles), #52 Market Observer, #53 News/Trend, #54 Risk Auditor, #55 Strategy Researcher, #56 Execution Recommender, #57 Daily Report, Agent Memory. 각 `docs/*_agent.md`, [`docs/agent_architecture.md`](docs/agent_architecture.md), [`docs/agent_memory.md`](docs/agent_memory.md)
- **Futures (simulation only, LIVE 영구 비활성)** — #46 Scope, #47 BrokerAdapter contract, #48 margin/leverage/liquidation, #49 StrategyBase, #50 UI hidden, #76 Promotion Policy. [`docs/futures_scope.md`](docs/futures_scope.md), [`docs/futures_promotion_policy.md`](docs/futures_promotion_policy.md)
- **Governance gates (live promotion *검토*용 — 자동 허가 아님)** — #72 Paper Gate, #73 Live Manual Gate, #74 AI Assist Gate, #75 AI Execution Activation Gate, #44/5-04 Paper Gate 성과기준, #45/5-05 Live 전환 감사로그, #70~#72/9-0x 실매매 OFF·Paper/Live 분리·Live Capital Review. [`docs/paper_gate_policy.md`](docs/paper_gate_policy.md), [`docs/live_manual_gate.md`](docs/live_manual_gate.md), [`docs/ai_execution_gate.md`](docs/ai_execution_gate.md), [`docs/live_trading_off_policy.md`](docs/live_trading_off_policy.md)
- **실전 전환 게이트 스택 (5-0x)** — #41 Paper≠Live capital 분리, #42 Manual Approval 전용 Gate, #43 Canary Gate(최소금액/1일1건). 모든 단계 통과해도 `is_live_authorization=False`. [`docs/capital_allocation_policy.md`](docs/capital_allocation_policy.md), [`docs/live_manual_approval_gate.md`](docs/live_manual_approval_gate.md), [`docs/live_canary_gate.md`](docs/live_canary_gate.md)
- **Risk/analytics advisory** — #77 Alpha Decay, #78 Correlation Guard, #79 Loss Tagging, #80 Pre-market Check(+#91 확장), #81 Strategy Registry metadata, #92 Release Readiness, #93 Security Scan, #94 Signal Alpha Decay, #95 Portfolio Correlation, #96 Loss Root Cause. 각 `docs/*.md`
- **성과/설명 대시보드 (advisory, read-only)** — #49~#52/6-0x 성과·AI판단 설명·복기 피드백·판단품질 게이트. [`docs/agent_performance_explainability_dashboard.md`](docs/agent_performance_explainability_dashboard.md), [`docs/post_trade_feedback_quality_score.md`](docs/post_trade_feedback_quality_score.md)
- **백테스트·전략검증 연구기록 (read-only, 실측 기록 — archive 참조)** — #46~#48/6-0x(Council 백테스트·Walk-forward·스트레스), BUILD-01/02A/02B, STRATEGY-VALIDATION-01, REAL-DATA-*, INTRADAY-DATA-01/02, REAL-INTRADAY-TEST-01, KIS-INTRADAY-100/1Y/ROBUST/60D/FORWARD/DECOMPOSITION, WF-6M-50SYMBOLS·ROOT-CAUSE. **연구/백테스트 결과이며 실전매매 권고가 아님.** 상세: [archive](docs/claude_md_changelog_archive.md), [`docs/strategy_potential_validation.md`](docs/strategy_potential_validation.md) 등
- **EXE/Desktop/Installer** — #53/7-01 sidecar 상태, #54/7-02 Universe 표시, #55/7-03 portfolio 소스, #56/7-04 로그 뷰어, #57/7-05 버전 표시, #63/8-01 preflight, #68/8-06 운영자 매뉴얼, #69/8-07 runbook, #86~#89 Tauri 패키징·KIS Paper one-click, INSTALL-UX-FIX-01, Desktop Release Workflow(수동 trigger·Windows only·LIVE flag true 0건). [`docs/desktop_packaging.md`](docs/desktop_packaging.md), [`docs/user_manual.md`](docs/user_manual.md), [`docs/runbook.md`](docs/runbook.md)
- **KIS 실시간 시세 ↔ Paper Auto Loop** — CONNECT-KIS-REALTIME-PRICE-TO-PAPER-AUTO-LOOP-V2: 4모드(VIRTUAL_ONLY/KIS_REALTIME_DRYRUN/KIS_REALTIME_PAPER_AUTO/SMOKE_TEST). `price_source="kis"`일 때만 KIS 모의주문 전송, mock 시세 전송 차단. `KIS_IS_PAPER=true`·`broker_order_type=KIS_PAPER` 유지. archive 블록1 참조

## 변경 시 동기화

다음 변경은 본 문서도 같이 업데이트해야 한다 (PR 리뷰에서 요구):

- 새 운용모드 추가
- 안전 플래그 추가/변경
- `route_order` 시그니처 또는 가드 체인 변경
- 새 broker adapter, market adapter 추가
- 새 docs 추가
- 절대 원칙 변경 — 흔치 않으나 발생 시 PR에서 별도 논의

신규 기능/PR의 상세 변경이력은 본 문서가 아니라 [`docs/claude_md_changelog_archive.md`](docs/claude_md_changelog_archive.md) 또는 해당 `docs/*.md`에 기록한다.
