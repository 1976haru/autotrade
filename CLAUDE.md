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

**#34 표준 진입점**: `RiskManager.check_order(order, context: RiskContext)`가 모든 호출자의 표준 메서드다. `evaluate_order`는 backwards compat alias로 유지. `OrderExecutor.execute`는 `audit.decision ∈ {APPROVED, NEEDS_APPROVAL}`만 broker.place_order로 진행 — 그 외는 `UnauthorizedOrderError`로 즉시 차단 (마지막 backstop). 자세한 contract: [`docs/risk_manager_contract.md`](docs/risk_manager_contract.md).

**#35 PositionLimitRule**: 1회 주문 / 종목별 / 총 노출 / 보유 종목 수 한도는 `app/risk/position_limits.py`의 `PositionLimitRule`이 단일 진실 — RiskManager가 위임 호출. `build_preview()`로 잔여 capacity 사전 시뮬 가능. 선물은 별도 (`FuturesRiskPolicy`). 자세한 정책: [`docs/position_limit_policy.md`](docs/position_limit_policy.md).

**#37 3-Level Kill Switch**: `emergency_stop`을 `OFF/LEVEL_1/LEVEL_2/LEVEL_3` 단계로 분리 (`app/risk/emergency_stop.py`). LEVEL_2는 미체결 취소 후보 표시, LEVEL_3는 청산 후보 표시 — **자동 청산 / 자동 취소 절대 금지** (read-only candidate list만, 운영자 수동 승인). `POST /risk/emergency-stop`이 `level` 필드 수용 (enabled=True + level 미지정은 LEVEL_1). 자세한 정책: [`docs/emergency_stop_policy.md`](docs/emergency_stop_policy.md).

**#38 OrderGuard**: 중복 주문 / 쿨타임 / 미체결 같은 방향 차단은 `app/risk/order_guard.py`의 `OrderGuard`가 담당 — RiskManager 평가 *전* `route_order`에서 호출되는 pre-trade guard. fingerprint(symbol+side+qty+type+price_bucket+strategy+mode+chain)로 중복 식별. 같은 `client_order_id`는 RETRY_REPLAY(안전), 다른 key 같은 fingerprint는 DUPLICATE(차단). 모든 cooldown / window 필드 default 0 = 비활성. 자세한 정책: [`docs/order_guard_policy.md`](docs/order_guard_policy.md).

**#39 AI Permission Gate**: AI 주문 권한을 5단계(FULL_STOP/RECOMMEND_ONLY/APPROVAL_REQUIRED/VIRTUAL_EXECUTION/LIMITED_LIVE_EXECUTION) × 5행동 매트릭스로 분리 (`app/risk/ai_permission_gate.py`). **AI API Key는 주문 권한이 아니다** — 본 모듈은 api_key/secret 입력을 받지 않으며 (테스트 가드), broker도 import하지 않는다. 권한은 mode + 안전 flag + 운영자 승인으로만 결정. `GET /api/risk/ai-permission/status`로 현재 level + 매트릭스 read-only 조회. 자세한 정책: [`docs/ai_permission_gate.md`](docs/ai_permission_gate.md).

**#40 OrderExecutor 단일 진입점**: `OrderExecutor.execute`만이 `broker.place_order()`를 호출하는 *유일한* 코드. `app/execution/order_executor.py`(신규 alias)에서 `OrderExecutor` + `OrderSource` enum + `derive_order_source` helper를 노출. 16개 API 라우트 + 12개 strategy/filter/agent/explainability/risk/permission 모듈에 `broker.place_order(` 호출 0건 — paramaterized grep 테스트로 강제. 모든 audit row에 `source` (STRATEGY/AI/MANUAL/OPERATOR_OVERRIDE/UNKNOWN) carry. 자세한 contract: [`docs/order_executor_contract.md`](docs/order_executor_contract.md).

**#41 Manual Approval은 초기 LIVE 단계의 필수 게이트**: 처음 실거래는 PendingApproval 큐를 거쳐 *운영자가 명시 승인*해야만 broker로 진행된다. PermissionGate.approve는 broker 호출 *전*에 RiskManager 재검증 — 실패 시 status=PENDING 유지 + attempts에 사유 누적. `Settings.approval_ttl_seconds`로 stale 결재 자동 EXPIRED. ApprovalOut에 expires_at / seconds_until_expiry / attempt_count / last_attempt_reasons / request_source(AI/STRATEGY/MANUAL/LIQUIDATION/RISK_OVERRIDE) 필드 carry. 자세한 정책: [`docs/manual_approval_policy.md`](docs/manual_approval_policy.md).

**#42 PaperTrader는 live broker를 호출하지 않는다**: `app/execution/paper_trader.py`의 `PaperTrader`는 OrderExecutor wrapper로 broker 인스턴스가 paper-safe인지 검증(`assert_paper_broker`) 후 위임 — `is_live_broker(broker)`이면 `NotPaperBrokerError`로 즉시 차단. `PaperBrokerKind`(MOCK/KIS_PAPER) + `Settings.paper_broker_kind`로 운영자 선택. `KIS_PAPER`는 `KIS_IS_PAPER=true` 강제. `GET /api/paper/status`로 paper 상태 + 안전 flag read-only 조회. **모의투자 체결 품질은 실제와 다를 수 있다** — LIVE 활성화 전 reconciliation 필수. 자세한 정책: [`docs/paper_trading_policy.md`](docs/paper_trading_policy.md).

**#43 LIVE_SHADOW ShadowTrade 추정 기록**: `LIVE_SHADOW`에서 RiskManager는 모든 주문을 `REJECTED`로 종결(가드 변경 0건)하지만, 그 위에 `app/db/models.py::ShadowTrade` row가 추가되어 *would-have* 정보(LIVE_SHADOW 운영 게이트 외 reason 0건이면 `APPROVED`) + 추정 체결가(`latest_price` proxy, slippage_bps=0)를 영구화. `actual_broker_order_sent`는 invariant False — `route_order`가 LIVE_SHADOW + REJECTED에서만 row 작성하며, 어떤 코드 경로도 True로 set하지 않음. `/api/shadow/trades` + `/api/shadow/summary`는 DB SELECT only(broker/AI client import 금지, 정적 grep 가드). Dashboard `ShadowSummaryCard`가 카운트 + invariant 0 + 추정 슬리피지 + “실제 주문 아님” 배지를 노출. **추정 체결은 실 체결과 다를 수 있다** — orderbook depth / 부분체결 / 호가 공백 / 슬리피지 미반영. 자세한 정책: [`docs/live_shadow_trade_policy.md`](docs/live_shadow_trade_policy.md).

**Agent Memory (read-only 학습 저장소, *주문 신호 아님*)**: Agent / 운영자가 과거 손실 원인 / 전략 변경 이력 / 위험 사례 / 운영자 메모를 *검색 가능*한 형태로 보관하는 학습 저장소. `agent_memory` 테이블(alembic 0020) + `app/agents/agent_memory.py` 모듈. **본 메모리는 *주문 신호가 아니다*** — 검색 결과로 직접 BUY/SELL/HOLD 결정 생성 금지, RiskManager / PermissionGate / OrderExecutor 우회 금지. `MemoryRecord.is_order_signal=False` 불변(`__post_init__` ValueError). `MemoryType` 8종(daily_report / risk_incident / strategy_research / backtest_review / agent_decision / operator_note / loss_post_mortem / lesson_learned — BUY/SELL/HOLD 0개), `SourceKind` 7종, `MemorySeverity` (INFO/WARN/HIGH/CRITICAL). **민감정보 저장 0건** — `sanitize_text()`가 INSERT *전* 민감 패턴(API key / Anthropic / OpenAI / KIS app_key / app_secret / access_token / 한국 계좌번호 / 신용카드 / 주민등록번호 / JWT / 이메일 / 한국 휴대전화) 검사 후 적중 시 `SecretLeakError`로 raise(fail-closed, redaction 아님). `sanitize_dict` / `sanitize_tags` 재귀 적용, ingest helpers(`memory_from_daily_report_markdown` / `memory_from_strategy_research_report` / `memory_from_risk_audit_report`) 모두 sanitize 통과 후 저장. 모듈은 broker / OrderExecutor / route_order / `app.permission.*` / `app.ai.assist.*` / 외부 HTTP / AI SDK import 0건(정적 grep 가드), `OrderRequest` import / 생성 / annotation 0건, `submit_candidate(` / `route_order(` 호출 0건. `DELETE` 미사용 — `archived` flag로 audit 보존. 검색은 keyword(LIKE) / tag(JSON contains) / strategy / symbol / mode / severity / memory_type AND filter, vector / semantic search는 후속 PR. `/api/agents/memory/{search,GET {id},POST,POST /{id}/archive,POST /from-daily-report,POST /from-strategy-research,POST /from-risk-audit}` endpoint — 모든 입력 sanitize, 민감정보 발견 시 400 + `secret_leak_blocked`. 45개 신규 backend 테스트 + 15개 frontend 테스트(`AgentMemoryCard` — "주문 신호 아님 · 과거 학습 기록" 배지 + 검색/필터/상세/archive/운영 메모 추가, "API key/Secret/계좌번호/개인정보 입력 금지" 안내, BUY/SELL/HOLD/즉시 주문/Place Order/승인 큐 보내기 버튼 0개 invariant). 자세한 정책: [`docs/agent_memory.md`](docs/agent_memory.md).

**#57 Daily Report Agent (advisory only, *투자 조언 아님*)**: 장 종료 후 OrderAuditLog / VirtualOrder / FuturesOrderAuditLog / AgentDecisionLog / EmergencyStopEvent / PendingApproval / BacktestRun을 read-only로 분석해 `reports/daily_YYYY-MM-DD.md` 자료를 생성하는 advisory Agent (`app/agents/daily_report_agent.py`). 12 섹션 markdown(중요 고지 / 오늘 요약 / 손익 / 시간대별 / 전략별 / Agent 판단 / 리스크 이벤트 / 승인 큐 / 손실 원인 / 내일 주의점 / 개선 후보 / Action Items / 부록) + 15종 `LossCauseCategory`(data_stale / order_rejected / emergency_stop / ai_overconfidence / duplicate_burst / cooldown_block / loss_limit_breach / margin_risk / liquidation_risk / volume_liquidity / strategy_condition / high_volatility / broker_error / unknown — BUY/SELL/HOLD 0개). **본 리포트는 *투자 조언이 아니라* 자동매매 시스템 운영·검증·개선 자료** — markdown에 "투자 조언이 아니라" / "시스템 운영" / "별도 검증" disclaimer 포함 강제(테스트로 lock), "매수 추천" / "매도 추천" / "지금 매수" / "지금 매도" / "추천 종목" 문구 0건(정적 grep 가드). `is_order_signal=False` / `auto_apply_allowed=False` 불변(`__post_init__` ValueError). 모듈은 broker / OrderExecutor / route_order / `app.permission.*` / `app.ai.assist` / 외부 HTTP / AI SDK import 0건, DB는 read-only SELECT만(INSERT/UPDATE/DELETE 0건, 정적 grep 가드), `OrderRequest` import / 생성 / annotation 0건. `DailyReportAgent`는 #51 `AgentBase` 호환 (role=REPORT_WRITER). CLI `scripts/generate_daily_report.py` (--date / --output-dir / --include-virtual / --include-futures / --dry-run) + `/api/agents/daily-report/{preview,generate}` endpoint — preview는 파일 작성 X, generate는 reports/에 markdown만 작성 (broker 호출 0건, audit row 0건, DB write 0건). `reports/`는 `.gitignore`에 등록 — 운영 로그는 git 미커밋. 47개 신규 backend 테스트 (CLI subprocess 통합 포함). 자세한 정책: [`docs/daily_report_agent.md`](docs/daily_report_agent.md), [`docs/daily_report_policy.md`](docs/daily_report_policy.md).

**#56 Execution Recommender Agent (proposal-only, *직접 주문 금지*)**: AI Assist 흐름의 *핵심* — 매수 / 매도 *제안*만 만들고 절대 직접 주문하지 않는 advisory Agent (`app/agents/execution_recommender.py`). `ExecutionProposal` frozen dataclass는 *주문 요청 객체가 아니다* — `is_order_intent=False` / `can_execute_order=False` 불변(`__post_init__` ValueError). `recommend_proposals(input) -> RecommendResult` (순수 분석, broker 호출 0건), `precheck_proposal(proposal, *, risk, broker, mode)` (RiskManager 사전검사, audit row 0건 — read-only quote/balance/positions만 조회), `submit_proposal(proposal, ...)` (기존 sanctioned `app.ai.assist.submit_candidate` #44에 *완전히* 위임 — 본 모듈은 route_order / OrderExecutor / broker class를 *직접* import하지 않음). 정적 grep 가드: `from app.brokers.kis|mock_broker` import 0건, `from app.execution.executor|order_router` import 0건, `OrderRequest` import / 생성 / annotation 0건, `broker.place_order(` / `broker.cancel_order(` / `await broker.place_order` / `await broker.cancel_order` 호출 0건, `route_order(` 직접 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건. `PrecheckOutcome` enum (APPROVED/NEEDS_APPROVAL/REJECTED/BLOCKED/REDUCED — BUY/SELL/HOLD 0개). 만료된 제안은 precheck/submit 모두 거부 (precheck=`REJECTED`, submit=`RuntimeError`/HTTP 410). `ExecutionRecommenderAgent`는 #51 `AgentBase` 호환. `/api/agents/execution-recommender/{recommend,precheck,submit}` endpoint — recommend/precheck DB write 0건, submit는 LIVE_AI_ASSIST 모드 + AI Permission Gate(#39) 통과 시에만 ai.assist 흐름 진입(다른 모드에서는 403). 43개 신규 backend 테스트 + 15개 frontend 테스트(`ExecutionRecommenderCard` — "주문 아님 · 승인 필요" 배지 + "위험 사전검사" / "승인 대기 후보로 보내기" 버튼만, "매수 실행" / "즉시 주문" / "Place Order" 버튼 0개 invariant). 자세한 정책: [`docs/execution_recommender_agent.md`](docs/execution_recommender_agent.md).

**#55 Strategy Researcher Agent (advisory only, *자동 반영 금지*)**: `BacktestRun` + 메트릭(#24) + walk-forward(#25) + Monte Carlo(#26) + data quality(#21) + strategy promotion gate(#27)를 read-only로 분석해 *전략 개선 후보*를 markdown 리포트로 *제안*하는 advisory Agent (`app/agents/strategy_researcher.py`). 19종 finding(low_profit_factor / negative_expectancy / high_max_drawdown / high_consecutive_losses / hourly_pnl_imbalance / walk_forward_fail / single_fold_dominance / overfit_risk_high / monte_carlo_ruin_high / monte_carlo_fat_tail / data_quality_poor / promotion_blocked 등) + 10종 suggestion category(PARAMETER_TUNE / RISK_TIGHTEN / TIMEFRAME_FILTER / DATA_QUALITY / OVERFIT_GUARD / SHRINK_SIZE / ADD_FILTER / RE_RUN_TEST / PROMOTION_BLOCK / SHADOW_VALIDATE — BUY/SELL/HOLD 0개) 산출. **본 Agent는 어떤 제안도 *자동으로 코드 / 파라미터에 반영하지 않는다*** — `StrategyResearchReport.auto_apply_allowed=False` 불변(dataclass `__post_init__` ValueError 가드). 모든 제안은 운영자 검토 → 별도 PR → 별도 백테스트 → walk-forward → paper/shadow → live 절차 필요. `is_order_signal=False` 불변, `ResearchSeverity` enum(HEALTHY/CAUTION/WARNING/CRITICAL)에 BUY/SELL/HOLD 0개. 모듈은 broker / OrderExecutor / route_order / `app.strategies.*` / `app.permission.*` / approval queue(`submit_candidate(`) / 외부 HTTP / AI SDK import 0건, DB는 read-only SELECT만(INSERT/UPDATE/DELETE 0건, 정적 grep 가드). `.save_params(` / `.apply_params(` / `.update_params(` / `policy.max_*=` mutation 0건. `StrategyResearcherAgent`는 #51 `AgentBase` 호환. `/api/agents/strategy-researcher/{recent,report/{run_id},mock}` read-only endpoint (broker 호출 0건, audit row 0건, DB write 0건). 52개 신규 backend 테스트 + 20개 frontend 테스트(`StrategyResearcherCard` — "자동 반영 안 됨 · PR 검토 필요" 배지 + 자동 적용 / 파라미터 저장 / 코드 수정 / Apply parameter / BUY/SELL/HOLD 버튼 0개 invariant — 후속 행동은 "Backtest 다시 실행" / 새로고침 / markdown 미리보기만). 자세한 정책: [`docs/strategy_researcher_agent.md`](docs/strategy_researcher_agent.md).

**#54 Risk Auditor Agent (advisory only, *직접 토글 금지*)**: `OrderAuditLog` / `EmergencyStopEvent` / `AgentDecisionLog`을 read-only로 분석하는 장중 안전 감독 Agent (`app/agents/risk_auditor.py`). 12종 위험 이벤트(daily_loss_breach / repeated_order_failure / duplicate_order_burst / data_stale / ai_overconfidence / ai_low_confidence_burst / emergency_stop_flapping / agent_warn_burst / margin_risk / futures_liquidation_risk / broker_error_burst / abnormal_rejection_rate) 감지 후 `RiskAuditorReport` 산출 — `audit_level`(GREEN/YELLOW/ORANGE/RED) + `risk_score` (0-100 clamped) + `pause_trading_recommended` / `emergency_stop_recommended` + `recommended_stop_reason` + 운영자 요약. **본 Agent는 emergency_stop을 *직접 토글하지 않는다*** — 운영자에게 *권고만* 한다(중지권한은 운영자 우선). `risk.emergency_stop = True` / `.set_emergency_stop(` 호출 0건 (정적 grep 가드, 단 docstring 설명은 허용). `is_order_signal=False` 불변(dataclass `__post_init__` ValueError 가드), `AuditLevel` enum에 BUY/SELL/HOLD 0개. 모듈은 broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건, DB는 read-only SELECT만(INSERT/UPDATE/DELETE 0건, 정적 grep 가드). `RiskAuditorAgent`는 #51 `AgentBase` 호환. `/api/agents/risk-auditor/{report,mock}` read-only endpoint (broker 호출 0건, audit row 0건, DB write 0건). 41개 신규 backend 테스트 + 17개 frontend 테스트(`RiskAuditorCard` — "주문 신호 아님 · 안전 리포트" 배지 + emergency_stop 토글 버튼 0개 invariant — Kill Switch UI는 #37 Risk 탭에서만). 자세한 정책: [`docs/risk_auditor_agent.md`](docs/risk_auditor_agent.md).

**#53 News / Trend Agent (context-only, 후보 필터 전용)**: `theme_signals` 테이블(#22)을 read-only로 요약하는 advisory Agent (`app/agents/news_trend_agent.py`). top_themes / rising_keywords / related_candidates / caution_themes / overheating_warnings를 산출. **주문 신호가 아님** — `NewsTrendOutput.is_order_signal=False` 불변(dataclass `__post_init__` ValueError 가드), `NewsTrendAction` enum에 BUY/SELL/HOLD 값 0개(MONITOR/RESEARCH/CAUTION/OVERHEAT_WARN/NO_DATA만). 모든 provider는 default disabled(`GoogleTrendsAlphaProvider`, `NewsProvider`, `DisclosureProvider` — 빈 list 반환). 외부 HTTP client(httpx/requests/urllib3/pytrends) import 0건 (정적 grep 가드). DB는 read-only SELECT만 (INSERT/UPDATE/DELETE 0건, 정적 grep 가드). `used_for_order=True` row 발견 시 *경고만* — 주문에 사용 X (invariant 위반 의심으로 운영자에게 surface). 과열 경고: score≥90 + signal_count≥5. `NewsTrendAgent`는 #51 `AgentBase` 호환. `/api/agents/news-trend` read-only endpoint. 25개 신규 backend 테스트 + 14개 frontend 테스트(`NewsTrendCard` — "주문 신호 아님 · 후보 필터 전용" 배지 + BUY/SELL/HOLD 버튼 0개 invariant). 자세한 정책: [`docs/news_trend_agent.md`](docs/news_trend_agent.md).

**#52 Market Observer Agent (context-only)**: 장중 시장 환경 snapshot을 생성하는 read-only Observer (`app/agents/market_observer.py`). 시장지수 / 거래대금 / 변동성 / 섹터·테마 흐름 / 급등락 카운트 / 데이터 freshness를 입력으로 받아 `MarketObserverOutput` 생성 — `risk_level`(LOW/MEDIUM/HIGH/BLOCKED) + `recommended_stance`(AGGRESSIVE/NORMAL/DEFENSIVE/WATCH_ONLY/PAUSE_NEW_BUY) + 3줄 요약 + reasons + market_regime carry. **본 Agent는 주문 신호를 만들지 *않는다*** — `is_order_signal=False` 불변(dataclass `__post_init__` ValueError 가드), `recommended_stance` enum에 BUY/SELL/HOLD 값 0개 (advisory 분위기 가이드만). 모듈은 broker / OrderExecutor / route_order / 외부 네트워크 호출 import 0건 (정적 grep 가드). 데이터 부족 시 UNKNOWN / WATCH_ONLY로 friendly fallback (예외 X). `MarketObserverAgent`는 #51 `AgentBase` 호환 — registry에 옵션 등록 가능. `/api/agents/market-observer` read-only endpoint (broker 호출 0건, audit row 0건). 31개 신규 backend 테스트 + 13개 frontend 테스트(`MarketObserverCard` — "주문 신호 아님" 배지 + BUY/SELL/HOLD 버튼 0개 invariant). 자세한 정책: [`docs/market_observer_agent.md`](docs/market_observer_agent.md).

**#51 Agent architecture (6 roles, advisory only)**: Agent 권한을 Observer / Analyst / Risk Auditor / Strategy Researcher / Report Writer / Execution Recommender 6개 역할로 분리. `app/agents/base.py`의 `AgentBase` ABC + `AgentOutput` dataclass + `AgentRole` / `AgentDecision` enum. `AgentOutput.is_order_intent`와 `can_execute_order`는 *항상 False* (dataclass `__post_init__` ValueError 가드). **ExecutionRecommender도 직접 주문 금지** — approval queue 후보 *payload*만 생성하며, 큐 등록은 caller(예: `app.ai.assist.submit_candidate` #44)가 별도 흐름에서 수행. `payload.is_order_intent=False` 명시로 주문 객체와 구분. `app/agents/roles.py`에 6개 deterministic mock 전략 + `build_default_registry()`. `app.agents.base` / `app.agents.roles` 둘 다 broker / OrderExecutor / route_order / kis / mock_broker / permission.gate import 0건 (정적 grep 가드). `AgentContext`는 broker 인스턴스 / API key / Secret 필드 0개 (테스트로 lock). 신규 API: `/api/agents/{architecture,catalog,mock-run}` read-only — broker 호출 0건, audit row 0건. 41개 신규 테스트 + 기존 167개 agent 테스트 무회귀. 자세한 정책: [`docs/agent_architecture.md`](docs/agent_architecture.md).

**#50 Futures UI hidden by default + safety screen**: Futures 탭은 `frontend/src/config/features.js`의 `FEATURES.futuresTab`(`VITE_ENABLE_FUTURES_TAB` env, default **false**)로만 navigation에 노출 — backend `ENABLE_FUTURES_LIVE_TRADING`과 *별개*인 *UI 노출 전용* flag. PC TopNav는 flag=true 시에만 futures 노출, **모바일 BottomNav는 flag=true여도 직접 노출 안 함** (`mobileExclude=true` — 사용자 혼동 방지). URL/state 강제 접근 시 `<FuturesDisabledNotice />`로 안전 안내 화면. `<Futures />`는 7개 안전 섹션(혼동 방지 banner / disabled banner + 4 badges / 6-item risk warning / 6-row safety matrix / `FuturesMarginRiskCard` / `FuturesOrderAuditCard` / disabled order area / 8-step activation checklist) — 모든 주문 버튼 `disabled` 고정, "활성화"/"주문 실행 시작" 같은 enabling 라벨 활성 button 0개 (테스트로 lock). `getNavTabs()` / `getMobileNavTabs()` / `isTabVisible()` 함수형 export로 매 호출 시 flag 평가, `TABS` proxy로 backwards compat. 33개 신규 테스트 + 1개 기존 테스트 정책 갱신 (TopNav). backend 변경 0건 — 본 PR은 frontend feature flag / navigation / 안전 UI / 문서 / 테스트만. 자세한 정책: [`docs/futures_ui.md`](docs/futures_ui.md).

**#49 FuturesStrategyBase (주식 Strategy와 별개) + mock 전략 3종**: 선물 전략 인터페이스를 `app/futures/strategies/base.py`에 정식 분리. `FuturesStrategyBase`는 주식 `Strategy`/`StrategyBase`(#28)를 *상속하지 않는다* (MRO 분리, `test_futures_strategy_base_does_not_inherit_stock_strategy`로 lock). `FuturesSignalAction` enum (OPEN_LONG/OPEN_SHORT/CLOSE_*/HEDGE/ROLLOVER/REDUCE_SIZE/WATCH/NO_SIGNAL) — 주식과 달리 양방향 진입 명시. `FuturesContractSizingHint`(`contracts`는 *계약 수* — **본 PR mock phase에서 ≤ 1 강제**, dataclass `__post_init__` ValueError), `FuturesExitPlan`(% + ticks + `liquidation_buffer_pct` referencing #48), `FuturesRolloverPlan`(close+open advisory plan — broker 호출 트리거 0건). `FuturesSignal.is_order_intent`는 항상 False (dataclass 가드 — True 시 ValueError). 3개 mock 전략 추가: `FuturesTrendFollowingStrategy`(SMA crossover → OPEN_LONG/OPEN_SHORT/WATCH), `FuturesVolatilityBreakoutStrategy`(Bollinger band 돌파 + 고변동성 시 REDUCE_SIZE), `FuturesHedgeStrategy`(equity 노출 ≥ 임계 시 HEDGE advisory). 모든 전략에서 만기 ≤ 5일이면 신규 진입을 WATCH로 강등 + `FuturesRolloverPlan` carry. 본 모듈은 broker / OrderExecutor / route_order / mock broker import 0건 (정적 grep 가드). 자동 롤오버 *주문* 발신 0건 — `_maybe_rollover` 헬퍼는 plan dataclass만 반환. `ENABLE_FUTURES_LIVE_TRADING` / `ENABLE_AI_EXECUTION` flag 변경 0건 — 본 PR은 contract / mock / 문서 / 28개 테스트만. 자세한 contract: [`docs/futures_strategy_contract.md`](docs/futures_strategy_contract.md).

**#48 Futures margin/leverage/liquidation rules**: `FuturesRiskManager.evaluate_virtual_order`(#151)의 inline 가드를 `app/futures/margin_rules.py`의 명시적 Rule 3종으로 분리 — `LeverageLimitRule`(`policy.max_leverage`와 `contract.leverage_max` 중 작은 값 효력), `FuturesMarginRule`(initial margin / `max_margin_used` / maintenance margin buffer advisory WARN), `LiquidationRiskRule`(distance ≤ 3% → BLOCK, 3-7% → WARN, > 7% → PASS — 임계는 `FuturesRiskPolicy` default로 향후 조정 가능). 기존 reason substring("leverage", "max_leverage", "margin_available", "max_margin_used", "contracts", "daily futures loss") 그대로 보존 — 기존 `test_futures_simulation.py` 31/31 호환. `FuturesRiskCheckResult`에 `warnings`/`metrics` 필드 추가 (default 빈 값 — backwards compat). `/api/futures/margin/preview`는 세 Rule을 read-only로 호출 — broker 호출 0건, audit row 0건 (`test_api_margin_preview_does_not_create_audit_or_orders` lock). Futures 탭 `FuturesMarginRiskCard`가 사전 시뮬 UI를 노출. **자동 강제청산 *주문* 발신 0건** — Rule들은 위험 *계산* 전용, `force_liquidate_if_needed(` / `.force_liquidate(` 호출 정적 grep 가드. live `evaluate_order` 항상 REJECTED 유지, `ENABLE_FUTURES_LIVE_TRADING=False` default 유지. 자세한 정책: [`docs/futures_margin_risk.md`](docs/futures_margin_risk.md).

**#47 FuturesBrokerAdapter 공식 contract (주식 BrokerAdapter와 별개)**: 선물 broker 인터페이스를 `app/brokers/futures_base.py`에 정식 분리. `FuturesBrokerAdapter`는 주식 `BrokerAdapter`를 *상속하지 않는다* (MRO 별개 — `test_futures_broker_adapter_does_not_inherit_from_stock_broker`로 lock). `FuturesOrder`(`FuturesOrderRequest` + audit 필드 strategy/signal_*/ai_decision_meta/trade_reason/client_order_id), `FuturesContractSpec`(code/underlying/expiry/multiplier/tick_size/tick_value_krw/leverage_max/currency/market_hours), `FuturesMarginSnapshot`(maintenance_margin_required + margin_call) 신규. 만기/롤오버 helper(`days_to_expiry` / `is_contract_expiring_soon` / `should_rollover`)는 *advisory bool/int*만 반환 — 자동 롤오버 / 자동 주문 트리거 0건. 본 모듈은 KIS / kis_client / mock_broker / OrderExecutor / route_order 어떤 것도 import하지 않음 (정적 grep 가드). `MockFuturesBroker`(legacy)는 동일 ABC re-export로 backwards compat. **주식 RiskManager / `PositionLimitRule`(#35)을 선물에 적용하지 않는다** — 선물은 `FuturesRiskManager`(`FuturesRiskPolicy.max_contracts/max_margin_used/max_leverage`)가 담당. `ENABLE_FUTURES_LIVE_TRADING=False` default 유지 + `FuturesRiskManager.evaluate_order` 항상 REJECTED + LIVE 어댑터 코드 0건 — 본 PR은 contract만, LIVE 어댑터는 별도 옵트인 PR. 자세한 contract: [`docs/futures_broker_contract.md`](docs/futures_broker_contract.md).

**#46 Futures Scope (Simulation Only, 국내/해외선물 비교)**: 선물 기능의 1차 범위는 *실거래가 아니라* `MockFuturesBroker` + `FuturesSimulationEngine` 기반 **가상 시뮬레이션**임을 [`docs/futures_scope.md`](docs/futures_scope.md)에 고정. 국내선물/옵션(KOSPI200) vs 해외선물(CME 등) 12개 항목 비교표 + 1차 도입 후보는 *국내 모의환경 우선, 해외선물 후순위*. 선물 LIVE 활성화는 9단계 blocker 체크리스트(주식 MVP 완료 / 모의 4주+ / 1차 시장 *하나만* 선택 / `FuturesAIExecutionGate` 추가 / 캘린더 + 롤오버 + 증거금 reconciliation / 운영자 별도 opt-in PR — `docs/live_activation_blockers.md` §3.1)를 모두 통과해야 한다. AI 자동매매는 선물에서 더 강한 권한 게이트 필요 — `AIExecutionGate`(#45) 위에 futures-specific 한도 추가. `ENABLE_FUTURES_LIVE_TRADING=False` default 유지, `FuturesRiskManager.evaluate_order` 항상 REJECTED, 실제 futures adapter 0개 — 본 문서/링크 정리만, 코드 변경 없음.

**#45 AIExecutionGate (LIVE_AI_EXECUTION 안전 게이트, default 비활성)**: `app/risk/ai_execution_gate.py`의 `evaluate_ai_execution(input, policy)`이 RiskManager(#34) / AiPermissionGate(#39) / OrderGuard(#38) 위에 추가되는 *최종* AI-specific 보수적 게이트 (12개 가드: mode/enable_ai_execution/enable_live_trading/confidence/quality/explanation/exit plan/notional/symbol whitelist/KST window/daily count/upstream gates). 기본 정책 `AIExecutionPolicy()`는 어떤 입력에서도 BLOCKED (`enable_ai_execution=False` + 빈 whitelist + `is_canary_mode=True` default) — `test_default_policy_blocks_any_input`이 invariant lock. 모든 가드 통과 + canary True → `CANARY_ONLY` (broker 주문 X, audit_note "AI execution canary only; no broker order sent"); canary False는 ALLOW지만 본 PR 코드 경로에서 도달 불가능. `/api/ai-execution/{evaluate,policy}`는 read-only (broker 호출 0건, audit row 0건). UI `AiExecutionPolicyCard`는 button/input/select 0개 — 활성화 토글 의도적 미제공 (테스트로 강제). 본 PR에서 `ENABLE_AI_EXECUTION` / `ENABLE_LIVE_TRADING` / live order 코드 추가 0건. Audit 계약은 기존 `OrderAuditLog` + `ai_decision_meta.ai_execution_gate_result`(JSON) 매핑으로 carry — DB 마이그레이션 0건. 자세한 정책: [`docs/ai_execution_policy.md`](docs/ai_execution_policy.md).

**#44 LIVE_AI_ASSIST AI 제안 + 사람 승인**: AI는 `app/ai/assist.py::AICandidate`로 매수/매도 *후보*만 만들고, `submit_candidate`가 `AiPermissionGate.evaluate_ai_permission(SUBMIT_FOR_APPROVAL)`(#39) → `route_order(requested_by_ai=True, mode=LIVE_AI_ASSIST)` → RiskManager 사전검사 → `PendingApproval` 큐 등록까지의 단일 진입점. AI는 broker / OrderExecutor / route_order 인스턴스를 import하지 않으며 (`app/ai/assist.py` + `routes_ai_assist.py` 정적 grep 가드), 승인 시점에는 기존 `PermissionGate.approve`가 broker 상태로 RiskManager 재검증(#070). `audit.trade_reason="ai_assist"` + `ai_decision_meta.source="AI_ASSIST"` sentinel로 결재 카드가 양면(supporting/opposing reasons + risk_note)을 노출하며, AI Permission Gate가 차단하면 audit row조차 작성되지 않음 (emergency_stop / disable_ai_orders). `/api/ai/assist/submit` + `/pending` + `/summary` 신설, 기존 `/api/approvals` contract는 변경 없음. **AI는 제안만, 주문은 사람 승인 후** — LIVE_AI_EXECUTION은 별도 옵트인 (`promotion_policy.md` 8개 조건). 자세한 정책: [`docs/ai_assisted_trading_policy.md`](docs/ai_assisted_trading_policy.md).

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

**#72 Paper Gate**: Paper 모드 4주 운용 결과를 promotion_policy 기준으로
평가하는 코드 단 게이트 — `app/governance/paper_gate.py::evaluate_paper_gate`.
PASS 기준: ≥28일 + ≥100건 + expectancy > 0 + PF ≥ 1.2 + MDD ≤ 15% + 손실한도
위반 0 + audit 누락 0 + stale/duplicate 위반 0. CLI는
`scripts/evaluate_paper_gate.py`, API는 `POST /api/governance/paper-gate/evaluate`.
**PASS는 Live Manual Approval *검토 가능*을 의미하며 실거래 자동 허가가
*아니다*** — `PaperGateResult.is_live_authorization=False` 불변 (dataclass
`__post_init__` ValueError 가드). 본 모듈은 broker / OrderExecutor /
route_order / paper_trader / 외부 HTTP / AI SDK import 0건, DB는 read-only
SELECT만 (INSERT/UPDATE/DELETE 0건, 정적 grep 가드). 자세한 정책:
[`docs/paper_gate_policy.md`](docs/paper_gate_policy.md).

**#73 Live Manual Gate**: `LIVE_MANUAL_APPROVAL` 모드 진입 readiness를 코드 단으로
평가 — `app/governance/live_manual_gate.py::evaluate_live_manual_gate`. PASS 기준:
Paper Gate PASS + Promotion Gate PASS + user explicit opt-in + approval_required=True
+ AI execution disabled + FUTURES live disabled + 1회 주문 ≤ 5만원 + 일일 손실 ≤ 1만원
+ 보유 ≤ 3개 + system_errors=0 + audit_missing=0 + approval_bypass_attempts=0.
API: `POST /api/governance/live-manual-gate/evaluate` +
`GET /api/governance/live-manual-gate/period-summary` (운영 로그 요약 helper
`summarize_live_manual_period`). UI: `LiveManualGateCard`. **PASS는 진입 *검토 가능*을
의미하며 실거래 자동 허가가 *아니다*** —
`LiveManualGateResult.is_live_authorization=False` 불변 (dataclass `__post_init__`
ValueError 가드), "실거래 활성화" / "Place Order" 같은 enabling 버튼 0개 (frontend
테스트로 lock). 본 모듈은 broker / OrderExecutor / route_order / paper_trader /
외부 HTTP / AI SDK / `app.core.config.get_settings` import 0건 (evaluator는 안전
플래그 *현재값*을 입력 DTO로 받음 — 직접 settings를 읽지 않아 운영자 입력 ↔ 실제값
혼선 방지), DB는 read-only SELECT만, `settings.enable_*_trading =` mutate 0건
(정적 grep 가드). LIVE 활성화 자체는 별도 옵트인 PR + 사용자 명시 승인 필요.
자세한 정책: [`docs/live_manual_gate.md`](docs/live_manual_gate.md).

**#74 AI Assist Gate**: `LIVE_AI_ASSIST` 모드의 AI 제안 품질을 *read-only*로
검증 — `app/governance/ai_assist_gate.py::evaluate_ai_assist_gate`. PASS 기준:
≥100 제안 + ≥28일 + expectancy > 0 + 손실율 ≤ 55% + Risk 거절율 ≤ 60% +
운영자 거절율 ≤ 50% + confidence calibration ≥ 0.5 + audit drift = 0 +
긴급정지 ≤ 2회. 12개 failure reason 태그(low_confidence / data_stale /
price_gap / liquidity / risk_limit / operator_rejected / approval_expired /
emergency_stop / regime_mismatch / news_or_theme_overheated /
duplicate_or_cooldown / uncategorized — BUY/SELL/HOLD 0개) 분포 carry.
API: `POST /api/governance/ai-assist-gate/evaluate`, CLI:
`scripts/evaluate_ai_assist_gate.py`, UI: `AIAssistGateCard`. **본 리포트는
*투자 조언이 아니라 시스템 검증 자료*** —
`AIAssistGateResult.is_investment_advice=False` 불변 (dataclass `__post_init__`
ValueError 가드). **PASS는 `LIVE_AI_EXECUTION` 자동 허가가 *아니다*** —
`is_live_authorization=False` / `is_order_signal=False` 불변, AI 자동매매
활성화는 `AIExecutionGate`(#45) + 별도 옵트인 PR + 사용자 명시 승인 필요.
본 모듈은 broker / OrderExecutor / route_order / paper_trader /
`app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` /
`requests` / `app.core.config.get_settings` import 0건 (evaluator는 입력 DTO만 사용),
DB는 read-only SELECT만, `settings.enable_*_trading =` mutate 0건 (정적 grep
가드), UI에 "AI 자동매매 활성화" / "LIVE_AI_EXECUTION 활성화" /
"ENABLE_AI_EXECUTION" / "AI 자동 실행" / "Place Order" 버튼 0개 (frontend
테스트로 lock). 자세한 정책: [`docs/ai_assist_gate.md`](docs/ai_assist_gate.md).

**#75 AI Execution Activation Gate**: `LIVE_AI_EXECUTION` 모드 *활성화*
readiness를 코드 단으로 평가하는 *최종* 게이트 —
`app/governance/ai_execution_gate.py::evaluate_ai_execution_gate` (#45 order-time
`AIExecutionGate`와는 *별개* 파일 / 책임). READY_FOR_REVIEW 조건: Paper Gate +
Promotion Gate + AI Assist Gate + Live Manual Gate 모두 PASS + 운영자 explicit
opt-in + Live Manual 운영 ≥28일 + AI Assist 운영 ≥28일 + RiskManager /
OrderGuard / AI Permission Gate / AuditLog / KillSwitch / Circuit Breaker 모두
활성 + 1회 주문 ≤ 3만원 + 일일 손실 ≤ 5천원 + 일일 주문 ≤ 10건 + 동시 보유
≤ 2개 + 종목 whitelist 1~5개 + 거래 시간 (KST 09:30~14:30) 명시 + AI
confidence ≥75 + signal quality ≥70 + system_errors=0 + audit_missing=0 +
approval_bypass_attempts=0. API: `POST /api/governance/ai-execution-gate/evaluate`
+ `GET /api/governance/ai-execution-gate/policy`. UI: `AIExecutionGateCard`.
**READY_FOR_REVIEW는 *실제 활성화가 아니다*** —
`AIExecutionActivationGateResult.is_live_authorization=False` 불변 (dataclass
`__post_init__` ValueError 가드), 활성화는 별도 옵트인 PR + 사용자 명시 승인 +
`ENABLE_AI_EXECUTION=true` 전환 + 초소액 canary + 즉시 kill switch 가능 모두
필요. **선물 AI Execution은 본 게이트가 *영구* 허용하지 않는다** —
`futures_allowed=False` 불변 (True 생성 시 ValueError, `futures_target=True`
또는 `enable_futures_live_trading=True` 입력 시 즉시 BLOCKED). 본 모듈은 broker /
OrderExecutor / route_order / paper_trader / `app.ai.assist` / `app.ai.client` /
`anthropic` / `openai` / `httpx` / `requests` / `app.core.config.get_settings`
import 0건 (evaluator는 안전 플래그를 *입력 DTO*로만 받음), DB write 0건,
`settings.enable_*_trading =` mutate 0건 (정적 grep 가드), UI에 "AI 자동매매
켜기" / "ENABLE_AI_EXECUTION 토글" / "활성화 토글" / "주문 시작" / "Place Order"
라벨 버튼 0개 (frontend 테스트로 lock). 자세한 정책:
[`docs/ai_execution_gate.md`](docs/ai_execution_gate.md).

## 변경 시 동기화

다음 변경은 본 문서도 같이 업데이트해야 한다 (PR 리뷰에서 요구):

- 새 운용모드 추가
- 안전 플래그 추가/변경
- `route_order` 시그니처 또는 가드 체인 변경
- 새 broker adapter, market adapter 추가
- 새 docs 추가
- 절대 원칙 변경 — 흔치 않으나 발생 시 PR에서 별도 논의
