# CLAUDE.md — Auto Trader 작업 지침

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

## 작업 방식

- 큰 기능은 작은 PR 단위로 쪼갠다.
- 새 기능은 테스트를 함께 추가한다 (backend pytest, frontend vitest).
- 금융 관련 로직은 수익률보다 **손실 방어와 감사 로그**를 우선한다.
- **랜덤 시뮬레이션 결과를 실제 성과로 표현하지 않는다.**
- 실제 주문 코드 작성 전 MockBroker, 테스트, 실패 케이스를 먼저 구현한다.
- LIVE / 선물 / AI 자동실행 활성화 PR은 운영자 명시 옵트인 후에만 머지.

## 안전 플래그

env 변수로 모든 위험 동작을 차단한다. 자세한 매트릭스는 [`docs/promotion_policy.md`](docs/promotion_policy.md).

| 변수 | 기본 | 효과 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | RiskManager 분기, broker 라우팅 |
| `ENABLE_LIVE_TRADING` | `false` | LIVE_* 모드에서 실거래 차단 |
| `ENABLE_AI_EXECUTION` | `false` | LIVE_AI_EXECUTION에서 AI 자동 실행 차단 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | 선물 모듈 거래 차단 |
| `KIS_IS_PAPER` | `true` | KisClient host + tr_id, KisBrokerAdapter.place_order 가드 |
| `MARKET_DATA_PROVIDER` | `mock` | 시장 데이터 소스 |
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
│                         #   futures)
├─ brokers/               # BrokerAdapter ABC + Mock + KIS
├─ market/                # MarketDataAdapter ABC + Mock + yfinance + BarCache
├─ risk/risk_manager.py   # 평가 + mode-aware 분기
├─ permission/gate.py     # 승인 큐
├─ execution/             # order_router (단일 진입점) + executor + fill_poller
├─ strategies/            # Strategy ABC + concrete + LiveStrategyEngine
├─ backtest/              # BacktestEngine + types + CSV loader
├─ ai/                    # AiClient (Anthropic) + service
├─ futures/               # 모든 모듈 stub (활성화 비활성)
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

## 변경 시 동기화

다음 변경은 본 문서도 같이 업데이트해야 한다 (PR 리뷰에서 요구):

- 새 운용모드 추가
- 안전 플래그 추가/변경
- `route_order` 시그니처 또는 가드 체인 변경
- 새 broker adapter, market adapter 추가
- 새 docs 추가
- 절대 원칙 변경 — 흔치 않으나 발생 시 PR에서 별도 논의
