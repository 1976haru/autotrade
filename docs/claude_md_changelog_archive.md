# CLAUDE.md 변경이력 아카이브

> 이 파일은 `CLAUDE.md`가 178KB로 비대해져 분리한 **과거 PR/기능 변경이력 + 백테스트·전략검증 연구기록**이다.
> - 정리일: 2026-06-02 (원본 백업: `CLAUDE.md.bak`).
> - **안전 규칙·절대 원칙·핵심 주문경로(route_order)·안전 플래그·다층 안전가드는 `CLAUDE.md` 본문에 그대로 유지된다.** 이 파일은 상세 이력 보존용.
> - 각 항목은 자기 `docs/*.md` 정책 문서를 별도로 가진다. 특정 모듈을 수정할 때 해당 항목과 docs 링크를 참조한다.

---

## 블록 1 — 핵심 주문경로 가드(#34~#43) · Agent 15종(#44~#57) · Futures(#44~#50) · 검증/백테스트 연구기록

> ※ #34~#43 핵심 주문경로 가드의 **요약 불변식**은 CLAUDE.md 본문 "핵심 아키텍처 불변식"에 있다. 아래는 전체 원문.

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

**#46 / 6-01 4전략 + Agent Council 백테스트 (Paper 성능 분석 전용)**: ORB / Momentum / Gap / VWAP 4 전략의 vote 와 Agent Council 의 final_action 을 *과거 OHLCV* 로 검증해 전략별/Council 별 성과지표를 산출하는 백테스트 — `app/backtest/strategy_council_backtest.py`. 기존 `agent_council.py` 의 deterministic evaluator(`evaluate_orb/momentum/gap/vwap`) + `run_agent_council` 을 *그대로 재사용*(broker 호출 0건). 각 bar 에서 `StrategyMarketInput` 구성 → 4 전략 vote + Council final_action → 신호 시점 종가 대비 **forward return** 을 horizon(5/10/30/60 bar + 당일 close)별 산출. **SELL 은 신규 숏으로 해석하지 않는다** — "보유 청산 신호 / 하락 방향 판단 평가"(down_hit_rate)로 *분리*, BUY 손익 풀에 섞지 않음. 성과지표(win_rate / 평균수익·손실 / 손익비 / profit_factor / MDD / 연속손실 / expectancy)는 `app/backtest/metrics.py` 순수 함수 재사용. market_regime / time_phase 버킷 + selected_strategies/confidence/quality_score carry + **Agent Council vs best single expectancy 비교**(`council_better_than_best_single`). 데이터 부족 → `BACKTEST_INSUFFICIENT_DATA`(조용히 멈추지 않음). CLI `scripts/run_strategy_optimization.py`(--input CSV / --output JSON / --markdown / --risk-profile / --horizons, exit 0 정상·1 데이터부족·2 입력오류) → `reports/backtest/` 에 JSON+MD(gitignore). fixture `backend/tests/fixtures/backtest_ohlcv.py`(상승/하락/횡보/gap up·down/ORB 돌파/저거래량/중복 timestamp/부족) + `sample_ohlcv.csv`. **백테스트 결과만으로 live promotion 허가 0건** — `BacktestReport.is_order_signal=False`/`is_live_authorization=False`/`auto_apply_allowed=False`/`contains_secret=False` 불변(dataclass 가드), 리포트/스크립트에 "수익 보장"/"실전 전환 승인" 문구 0건(테스트 lock). broker/OrderExecutor/단일 주문 라우터/paper_trader/KIS 어댑터/anthropic/openai/httpx/requests import 0건, broker 주문/취소/route 호출 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. 38개 신규 테스트(모듈 29 + CLI 9) + 회귀 147(agent_council/strategy_performance/paper_gate_performance/secret_exposure) 무회귀. 자세한 정책: [`docs/strategy_council_backtest.md`](docs/strategy_council_backtest.md).

**#47 / 6-02 Walk-forward 과최적화 방지 검증 (Paper 분석 전용)**: #46 의 4전략 + Agent Council 백테스트가 *특정 기간에만 맞는 착시(overfit)* 인지 검증 — `app/backtest/walk_forward_validation.py`. 과거 OHLCV 를 *날짜(KST date) 단위* 로 시간 순서 분리(THREE_WAY 60/20/20 + ROLLING window 슬라이딩), **미래 데이터를 train 에 섞지 않음**. 각 segment 를 #46 `run_strategy_council_backtest` 로 독립 평가(broker 호출 0건). train 대비 validation/test **유지율(retention = seg_exp/train_exp)** 산출(train≤0/None→측정불가 None, seg None→0.0 보수적), **overfit_suspected**(train 양(+)인데 OOS 유지율<임계 또는 음전), **stability_score**(0.6·retention + 0.4·OOS 양(+)비율), **성과 붕괴 구간 탐지**(유지율<collapse_threshold 또는 expectancy 음전), market_regime/time_phase **안정성**(OOS 승률 mean/min/max/spread + consistent), **Agent Council vs best single(OOS) 비교**. reason_code: WALK_FORWARD_{INSUFFICIENT_DATA / SPLIT_CREATED / VALIDATION_DEGRADATION / TEST_DEGRADATION / OVERFIT_SUSPECTED / PERFORMANCE_COLLAPSE / STABLE}. CLI `scripts/run_strategy_council_walk_forward.py`(--mode THREE_WAY/ROLLING / --train-pct / window-days / step-days, exit 0·1·2) → `reports/backtest/` JSON+MD(gitignore). fixture `walk_forward_{stable,overfit,insufficient}_records`. **walk-forward 결과만으로 live promotion 허가 0건** — `WalkForwardReport.is_order_signal=False`/`is_live_authorization=False`/`auto_apply_allowed=False`/`broker_order_sent=False`/`contains_secret=False` 불변(dataclass 가드), "수익 보장"/"실전 전환 승인" 문구 0건(테스트 lock). broker/OrderExecutor/단일 주문 라우터/paper_trader/KIS 어댑터/anthropic/openai/httpx/requests import 0건, broker 주문/취소/route 호출 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. **3-04 walk-forward(`walk_forward_runner.py` / `run_walk_forward_validation.py`, 6전략 파라미터)와는 별개 파일/목적** — 충돌 회피 위해 본 PR 은 `walk_forward_validation.py`(council) / `run_strategy_council_walk_forward.py` 명명. 24개 모듈 테스트 + 8개 CLI 테스트 + 회귀(strategy_council_backtest/walk_forward/walk_forward_runner/secret_exposure) 무회귀. 자세한 정책: [`docs/strategy_council_walk_forward.md`](docs/strategy_council_walk_forward.md).

**#48 / 6-03 Agent / Risk Gate 스트레스 테스트 (Paper 검증 전용)**: 실전과 유사한 악조건 12종(SLIPPAGE_HIGH / PARTIAL_FILL / ORDER_REJECTED / UNFILLED_TIMEOUT / PRICE_STALE / MARKET_CRASH / GAP_DOWN_OPEN / GAP_UP_SPIKE / DATA_LOCK / PORTFOLIO_DRIFT / DAILY_LOSS_LIMIT / REPEATED_REJECTION)을 합성 입력으로 재현하고 **기존 안전 가드가 정상 동작하는지** 검증 — `app/stress_test/agent_stress_test.py`. **가드를 재구현하지 않고 기존 함수를 그대로 호출**: `RiskManager.check_order/evaluate_order`(stale price hard-reject·시장 regime BLOCK_NEW_BUY·emergency_stop·일일 손실한도 → REJECTED), `loss_limits.DailyLossLimitRule`(block_buy), `kis_paper.order_quality.build_order_quality_log`(슬리피지 bps·부분체결·거절·미체결 분류), `reconciliation.compare_positions`(drift), `agent_council.run_agent_council`(악조건 시 BUY 미발생). 판정 PASS(가드 보호)/WARN(advisory 플래그)/FAIL(보호 실패) + `risk_gate_triggered`/`kill_switch_triggered`(emergency_stop 실검증)/`kill_switch_should_trigger`(일일 손실·반복 거절 *권고* — 자동 토글 아님). order_quality 정합성 검증: 부분체결≠완전체결, 거절≠submitted, 미체결≠filled. `--strict` 면 WARN→FAIL 격상. CLI `scripts/run_agent_stress_test.py`(--scenario ALL/단일 / --strict / --seed, exit 0 PASS·WARN / 1 FAIL / 2 설정오류) → `reports/stress/` JSON+MD(gitignore). **실제 주문 0건** — `StressTestReport`/`ScenarioResult.broker_order_sent=False`/`is_order_signal=False`/`is_live_authorization=False`/`auto_apply_allowed=False`/`contains_secret=False` 불변(dataclass 가드), "수익 보장"/"실전 전환 승인" 문구 0건(테스트 lock). KIS/mock broker 어댑터·단일 주문 라우터·OrderExecutor·paper_trader import 0건, broker 주문/취소/route 호출 0건, broker 인스턴스 생성 0건, anthropic/openai/httpx/requests import 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. 26개 모듈 테스트 + 9개 CLI 테스트 + 회귀(test_stress/secret_exposure/event_integrity/kis_paper_sell_e2e/risk_gate_buy_exposure_e2e) 무회귀. 자세한 정책: [`docs/agent_stress_test.md`](docs/agent_stress_test.md).

**#49 / 6-04 + #52 / 6-07 성과 대시보드 + AI 판단 설명 가능성 (read-only 표시)**: (A) 성과 개선 포인트 — 전략별 승률/손익비/profit_factor/MDD/**expectancy** + 체결 실패율/거절률/부분체결률 + **차단 사유 TOP** + Agent Council vs best single; (B) AI 판단 설명 — entry_reason/counter_reason/exit_plan/risk_flags/risk_veto_result/exit_plan_validation/sell_reason/최종 판단 이유. 백엔드 read-only helper 2종: `app/agents/order_quality_metrics.py::aggregate_order_quality`(episode `order_quality_summary`(P-24)+council 에서 실패율/거절률/부분체결률/blocked_reasons_top 집계, 우선순위 거절>veto>exit_plan 실패>HOLD reason_code), `app/agents/decision_explanation.py::build_decision_explanation`(council 의 *이미 있는* 필드에서 설명 유도 — 새 판단 로직 0, 누락 시 fallback 문구로 구버전 episode 호환). endpoint `GET /api/agents/order-quality-metrics`, `POST /api/agents/decision-explanation`, `GET /api/agents/decision-explanation/{id}` (read-only, DB write 0건). 프론트엔드 신규 카드 `PerformanceMetricsSummary`(expectancy+실패율+차단 사유, 기존 P-28 `PerformanceDashboard` 보강) + `AgentDecisionExplanationCard`(설명, `decision` prop client-side 유도 또는 최신 episode fetch) + `utils/decisionExplanation.js`(백엔드 미러) — AISignal 탭 마운트. **모두 read-only — 매수/매도/실전/승인/Place Order 버튼 0개, 입력 form 0개**(테스트로 lock), "분석/설명 전용"/"주문 버튼 아님"/"실전 전환 승인 무관"/"수익 보장 아님" 문구 노출. 응답 invariant `is_order_signal=False`/`is_live_authorization=False`/`contains_secret=False`. broker/OrderExecutor/단일 주문 라우터/paper_trader/KIS 어댑터/anthropic/openai/httpx/requests import 0건, broker 주문/취소/route 호출 0건, DB write 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건, Agent 판단 로직 변경 0건(설명/표시만). 24개 backend 테스트 + 19개 frontend 테스트(설명 11 + 성과 8) + 회귀(strategy_performance_analytics/secret_exposure/PerformanceDashboard/DecisionEpisodeCard) 무회귀. 자세한 정책: [`docs/agent_performance_explainability_dashboard.md`](docs/agent_performance_explainability_dashboard.md).

**#50 / 6-05 + #51 / 6-06 복기 피드백 루프 + 판단 품질 고도화 (advisory, HOLD 게이트)**: (A) PostTradeReview 피드백 루프 — `app/agents/post_trade_feedback.py::build_feedback_loop`(`summarize_episodes` 집계에서 WINNING/LOSING_SETUP/OVER_ENTRY/LATE_EXIT/EARLY_EXIT/POOR_RISK_REWARD/LOW_DATA_QUALITY/MARKET_REGIME_MISMATCH 태그 + 반복 실패(≥3회) 시 threshold 추천 후보). **자동 적용 금지** — `auto_apply_allowed=False`/`requires_operator_approval=True` 불변, `feedback_penalty_for()` 는 다음 판단 quality 입력으로만 사용(설정 변경 0). (B) quality_score 고도화 — `app/agents/decision_quality.py::compute_decision_quality`(signal_consistency 0.30 + data_reliability 0.20 + risk 0.20 + regime_fit 0.15 + exit_plan 0.15 가중합 - feedback_penalty, 0~100 clamp + grade A~F). **무조건 매수 방지** — `run_agent_council` 의 기존 게이트(가중투표→장세→confidence/quality→RiskOfficer veto→exit_plan 검증→보유 청산) *뒤* 에 별도 quality gate 추가: `final_action==BUY` & `enhanced<min_quality` → HOLD 강등, `pre_quality_action` 보존, `reason_code=QUALITY_SCORE_LOW_HOLD`. **기존 RiskOfficer/exit_plan 정책 비우회**(그 뒤 단계), 기존 `quality_score` 필드 유지(호환) + `quality_gate_result`/`pre_quality_action` 추가 carry(episode.council 영구화, 마이그레이션 0). 데이터 부재(None)는 과도 감점 안 함(중립 70)→누락만으로 BUY 강제 HOLD 안 함→기존 council 테스트 무회귀. endpoint `GET /api/agents/feedback-loop`, `POST /api/agents/decision-quality`(read-only). 프론트엔드 `PostTradeFeedbackCard`(태그+추천+"자동 적용 안 됨/운영자 승인 필요") + `DecisionQualityScoreCard`(score/grade/breakdown/penalties+"quality 낮으면 HOLD") — AISignal 탭 마운트, **주문/실전/승인 버튼 0개·입력 form 0개**. **복기/quality_score 만으로 live promotion·is_live_authorization·주문 강제 생성 0** — broker/OrderExecutor/단일 주문 라우터/paper_trader/KIS 어댑터/anthropic/openai/httpx/requests import 0건, broker 주문/취소/route 호출 0건, DB write 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. 42개 backend 테스트(feedback 20 + quality 22) + 17개 frontend 테스트(feedback 8 + quality 9) + 회귀 417 무회귀. 자세한 정책: [`docs/post_trade_feedback_quality_score.md`](docs/post_trade_feedback_quality_score.md).

**#53 / 7-01 EXE Backend / Sidecar / 진단 상태 정합성**: EXE 실행 시 사용자가 정상/오류 상태를 혼동하지 않도록 `backend_api_reachable` / `sidecar_status`(RUNNING/STARTING/STOPPED/UNKNOWN) / `diagnostics_status`(OK/DEGRADED/FAIL/UNKNOWN) / `db_status`(OK/FAIL/UNKNOWN) / `kis_paper_readiness`(READY/BLOCKED/UNKNOWN) 를 **분리된 표준 enum** 으로 표시. **"Backend 연결됨" 과 "연결 실패" 동시 표시 금지** — 단일 진실은 *frontend 의 fetch 결과*이며, `frontend/src/utils/exeStatus.js::normalizeExeStatus` 가 `backend_api_reachable=false` 시 diagnostics/db/kis 를 모두 `UNKNOWN` 으로 강등(모순 원천 차단, `isContradictoryStatus` + XOR 테스트로 lock). backend `GET /api/system/exe-status`(read-only, `routes_system.py`)는 boolean/enum/timestamp 만 emit — broker/OrderExecutor/route_order 호출 0건, DB write 0건, Secret/API key/계좌번호 원문 0건, `is_live_authorization=false`/`contains_secret=false` 불변. UI `BackendSidecarStatusCard`(Settings 탭 상단) — 매수/매도/실거래/Place Order 버튼 0개, 입력 form 0개 (테스트로 lock). 13개 backend 테스트 + 39개 frontend 테스트(helper 26 + card 13 추정). 자세한 절차: [`docs/exe_backend_sidecar_status_check.md`](docs/exe_backend_sidecar_status_check.md).

**#57 / 7-05 앱 버전 / 빌드 commit 표시**: 사용자가 실행 중인 EXE 가 최신 main 기준인지 확인할 수 있도록 app version / channel / git commit / branch / build time / dirty 를 Settings 탭 `AppVersionCard` 에 표시. **Frontend(권위 소스)**: `vite.config.js` 가 build-time 에 git 메타데이터를 `import.meta.env.VITE_*` 로 baking (우선순위 env→git→unknown, git 실패해도 빌드 안 깨짐), `utils/buildInfo.js` 가 정규화. **Backend sidecar**: `GET /api/system/build-info`(`app/system/build_info.py`, 우선순위 build_stamp→env→git→unknown). `scripts/generate_build_stamp.py` 가 빌드 시 `app/system/build_stamp.py`(gitignore) 생성 → PyInstaller `--collect-submodules app` 로 bundle → packaged EXE 도 정확. AppVersionCard 가 frontend↔backend commit 일치 여부 안내. **`.env` 에 build metadata 저장 0건**. broker/OrderExecutor/route_order 호출 0건, Secret/API key/계좌번호 원문 0건, `is_live_authorization=false`/`contains_secret=false` 불변, 매수/매도/실거래/Place Order 버튼·입력 form 0개 (테스트로 lock). desktop-release.yml "Compute build metadata" step 이 VITE_*/AUTOTRADE_* 를 GITHUB_ENV 로 export. 9개 backend 테스트 + frontend(buildInfo + AppVersionCard). 자세한 절차: [`docs/exe_build_version_check.md`](docs/exe_build_version_check.md).

**#63 / 8-01 EXE Preflight Smoke Test**: EXE 빌드 전후 기본 작동 여부를 한 번에 점검하는 read-only smoke test — health / 안전 flag / KIS credentials / DB / auto loop / Agent Council / Decision Episode / build·version / update status 를 PASS/WARN/FAIL 로 분류. **CLI** `scripts/exe_smoke_test.py`(urllib, exit 0=PASS/WARN · 1=FAIL · 2=unreachable) + **endpoint** `GET /api/system/preflight`(`app/system/preflight.py`: `build_checks(inputs)` 순수 함수 + `gather_inputs` 방어적 수집) + **UI** `PreflightSmokeCard`. **본 검사는 주문을 발생시키지 않는다** — read-only GET 만, broker/OrderExecutor/route_order 호출 0건, DB read-only SELECT 만, POST/주문 endpoint 0건. 치명 FAIL: backend 도달 불가 / DB FAIL / ENABLE_LIVE/AI/FUTURES=true / KIS_IS_PAPER=false / is_live_authorization=true / secret·계좌 원문 탐지 / 비-paper broker. **MARKET_CLOSED 는 FAIL 이 아니라 WARN**(장 닫힘 정상). CLI 가 응답을 client 측에서 2차 secret 스캔(원문 0건, 마스킹). Secret/API key/계좌번호 원문 출력 0건, `is_live_authorization=false`/`contains_secret=false` 불변, UI 매수/매도/실거래/Place Order 버튼·입력 form 0개 (테스트로 lock). 26개 backend 테스트 + frontend(PreflightSmokeCard). 자세한 절차: [`docs/exe_preflight_smoke_test.md`](docs/exe_preflight_smoke_test.md).

**#56 / 7-04 오류/이벤트 로그 뷰어**: EXE 장중 문제 원인을 한 화면에서 파악하는 read-only 로그 뷰어 — RuntimeEvent / AgentDecisionEpisode(AI 판단) / OrderAuditLog(KIS 주문)을 단일 모양 `{timestamp,source,severity,reason_code,message,symbol,action,broker_order_no,episode_id}` 으로 병합해 최근 100건 반환. **endpoint** `GET /api/system/logs?source=&severity=&q=&limit=`(`app/system/log_viewer.py::collect_recent_logs`, `Depends(get_db)` read-only SELECT) + **UI** `RuntimeEventLogViewer`(Settings 탭). source(ALL/RUNTIME_EVENT/AGENT_DECISION/KIS_ORDER)·severity·keyword 필터 + 복사. **민감정보 마스킹**: `redact_text()` 가 message/reason_code free-text 의 sk-/sk-ant-/ghp_/xox/Bearer/JWT/KIS app key/access_token=/계좌번호(8-2)/신용카드/주민번호 패턴을 `[REDACTED]` 로 치환(*드롭이 아니라 마스킹*) — 구조화 식별자(broker_order_no/episode_id/symbol/action)는 그대로. UI 복사 직전 `containsSecretDeep` 2차 스캔 → 적중 시 클립보드 미기록. broker/OrderExecutor/route_order 호출 0건, DB write 0건, Secret/API key/계좌번호/access_token 원문 0건, **매수/매도/실거래/Place Order/주문 재시도·재전송 버튼 0개**(입력은 검색 1개뿐), `is_live_authorization=false`/`contains_secret=false` 불변 (테스트로 lock). 16개 backend 테스트 + frontend(RuntimeEventLogViewer). 자세한 절차: [`docs/exe_runtime_event_log_viewer.md`](docs/exe_runtime_event_log_viewer.md).

**#55 / 7-03 Paper/KIS 포트폴리오 데이터 소스 통일**: Dashboard / Settings / Agent 화면의 현금·총자산·포지션 값을 *어떤 source* 에서 왔는지 + 조회 상태와 함께 표시해, **API 실패를 0원으로 오해하지 않도록** 통일. 신규 `app/portfolio/portfolio_snapshot.py::PortfolioSourceSnapshot` + `build_portfolio_source_report`. 표준 source 4종(`PAPER_SIMULATED` 내부 Paper 모의 / `KIS_PAPER_ACCOUNT` KIS 모의 계좌 조회 성공 / `UNAVAILABLE` 조회 실패·자격 미설정·API 오류 / `MIXED_BLOCKED` source 혼합 차단) · status 7종(OK/STALE/ERROR/CREDENTIALS_MISSING/API_UNAVAILABLE/NOT_CONFIGURED/UNKNOWN) · reason_code 10종(PORTFOLIO_SOURCE_PAPER_SIMULATED / PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT / PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK / PORTFOLIO_CREDENTIALS_MISSING / PORTFOLIO_KIS_PAPER_NOT_CONFIGURED / PORTFOLIO_MIXED_SOURCE_BLOCKED 등). **API 실패 시 0원 fallback 금지** — `__post_init__` 가 status 실패/미설정 계열 또는 source UNAVAILABLE/MIXED_BLOCKED 인데 cash/total_asset/position_count 가 None 이 아니면 `ValueError` (조회 실패를 0원/숫자로 표시 원천 차단), `zero_fallback_used` 항상 False 불변. **실제 0원**(status=OK, cash=0)과 **조회 실패**(cash=None → UI "확인 불가")를 구분. **한 카드의 현금/총자산/포지션은 같은 source** — Paper 모의 포트폴리오와 KIS 모의 계좌를 *섞지 않고* 별도 섹션으로 표시(`assert_single_source` 가 합산 시도 시 MIXED_BLOCKED). 본 PR 은 KIS 모의 잔고 조회 콜백을 *주입하지 않으므로*(broker 호출 금지) KIS snapshot 은 `NOT_CONFIGURED`(잔고 0원 아님). endpoint `GET /api/auto-paper/portfolio-source`(routes_auto_paper, KIS 자격 *존재 여부* 만 readiness 로 판정·원문 0건, DB SELECT only) + UI `PortfolioSourceCard`(Dashboard 상단, source/status/reason/현금/총자산/포지션/마지막 갱신 + "조회 실패는 0원 아님" 경고). 기존 `PortfolioCard.jsx` 의 `cash ?? 0` 위험 패턴을 `?? null` + "확인 불가" 로 수정. broker/OrderExecutor/route_order/paper_trader/`app.ai.*`/`anthropic`/`openai`/`httpx`/`requests`/`get_settings` import 0건(정적 grep 가드), `is_live_authorization=False`/`is_order_signal=False`/`contains_secret=False`/`is_paper_only=True` 불변, secret/계좌번호 원문 0건(position 화이트리스트 필드만 carry), 매수/매도/실전/Place Order 버튼·입력 form 0개. KIS 주문 경로 변경 0건, 안전 flag 변경 0건. 27개 backend 테스트 + frontend(PortfolioSourceCard 11 + PortfolioCard 0원 fallback 회귀 1). 자세한 정책: [`docs/portfolio_source_consistency.md`](docs/portfolio_source_consistency.md).

**#54 / 7-02 기본 Universe 50개 상태 표시**: 사용자 관심종목이 없어도 자동매매 후보군이 비지 않도록 기본 Universe 50개 fallback + 상태 표시. 기존 `app/universe/default_universe.py`(`FALLBACK_MARKET_CAP_TOP50` 정확히 50개) 위에 status 레이어 `app/universe/universe_status.py::build_universe_status` 추가 — 6자리 한국 종목코드 *유효성 검증* + invalid 제거 + 순서보존 dedup + `symbols_preview`(앞 N개, 전체 50 안 펼침) + `reason_code`. universe_source: USER_WATCHLIST / DEFAULT_UNIVERSE_50 / FALLBACK_DEFAULT_UNIVERSE_50 / EMPTY. reason_code: NO_USER_WATCHLIST / USER_WATCHLIST_OK / NO_VALID_SYMBOLS / NO_UNIVERSE_SYMBOLS. **사용자 관심종목 우선**(있으면 fallback 미사용), 전부 invalid → 기본 50 대체, 최종 0개 → `NO_UNIVERSE_SYMBOLS`(조용히 멈추지 않게 명시). endpoint `GET /api/auto-paper/universe-status`(routes_auto_paper, 사용자 watchlist 읽어 status 산출, DB SELECT only) + UI `UniverseStatusCard`(Settings 탭, source/count/preview/fallback/사유). **기본 Universe 는 후보군 확보용 — 투자 추천 아님**(`is_investment_advice=False`/`is_order_signal=False`/`contains_secret=False` 불변). broker/OrderExecutor/route_order/KIS endpoint import 0건, 안전 flag 변경 0건, 매수/매도/실전/강제주문 버튼·입력 form 0개. 17개 backend 테스트 + frontend(UniverseStatusCard). 자세한 절차: [`docs/default_universe_50_check.md`](docs/default_universe_50_check.md).

**#68 / 8-06 초보자 운영자 매뉴얼**: 코딩을 모르는 사용자가 문서만 보고 EXE 실행 · KIS 모의 설정 · 안전 점검 · 오류 확인 · 장중 테스트 준비를 할 수 있는 [`docs/user_manual.md`](docs/user_manual.md) (28개 섹션). 안전 flag(LIVE/AI/FUTURES=false, KIS_IS_PAPER=true) · 장 닫힌 날/열린 날 분리 · 자주 나오는 메시지 표 · 문제 보고 양식 · 실전 전환 전 조건 포함. **문서 작업만 — 코드/로직 변경 0건.** 자격은 `<YOUR_...>` placeholder 만(실제 secret/계좌 예시 0건), "수익 보장"/"자동 실전 전환" 류 문구 0건, "실전 전환은 별도 승인 필요" 명시. `backend/tests/test_user_manual_safety.py`(11개) 가 금지 문구/secret-like/account-like 부재 + 필수 문구 존재를 정적 검증(금지 리터럴은 테스트 소스에 남기지 않도록 동적 조립). README docs index 에 링크 추가.

**#69 / 8-07 장애 대응 Runbook**: 장중 오류 발생 시 사용자가 빠르게 원인·조치를 찾는 [`docs/runbook.md`](docs/runbook.md) (18개 섹션, "먼저 볼 화면 → 의미 → 즉시 조치 → 전달할 정보" 구조). 16종 오류 코드(NO_MARKET_DATA / MARKET_CLOSED / CREDENTIALS_MISSING / BLOCKED_BY_KIS_READINESS / ORDER_REJECTED / FILL_POLLING_FAIL / PORTFOLIO_DRIFT / BACKEND_FAIL / SIDECAR_STOPPED / DB_FAIL / PRICE_STALE / UPDATE_FAILED / VERSION_MISMATCH / SECRET_EXPOSURE_SUSPECTED / RISK_FLAGS_EXCEEDED / EXIT_PLAN_INVALID) 대응 표 + 가장 먼저 할 5단계 + 장중 즉시 중단 기준 + 문제 보고 양식 + Claude Code 전달 금지 정보 + 복구 후 체크리스트. **문서 작업만 — 코드/로직 변경 0건.** 실제 secret/계좌 예시 0건, "수익 보장"/"자동 실전 전환" 류 문구 0건, 실거래 OFF·KIS_IS_PAPER true 안전값 안내. `backend/tests/test_runbook_safety.py`(12개) 가 금지 문구/secret-like/account-like 부재 + 필수 오류코드·양식 존재를 정적 검증(금지 리터럴 동적 조립). README docs index 에 링크 추가.

**#41 / 5-01 (P-20) Paper capital ≠ Live capital 분리**: Paper 모의 자금 설정(시드머니/종목당 투자금/일일 매수 한도/최대 보유/risk profile)이 *실전 주문 한도/자금*으로 오용되는 사고를 코드·정책·테스트·문서로 차단. `app/permission/live_capital_guard.py`(read-only advisory 가드)가 ① live 주문 payload 에 paper capital 필드가 섞이면 감지 + `paper_capital_ignored=True`(한도 계산에 *사용하지 않음*), ② live capital 미검토/미승인이면 차단. reason_code 고정: `PAPER_CAPITAL_NOT_LIVE_CAPITAL` / `LIVE_CAPITAL_REVIEW_REQUIRED` / `LIVE_ORDER_NOTIONAL_NOT_CONFIGURED` / `LIVE_CAPITAL_PERMISSION_DENIED` / `PAPER_MODE_PAPER_CAPITAL_OK`. **본 모듈은 실전 활성화 기능이 아니다** — `live_capital_approved`/`is_live_authorization` 항상 False, `ENABLE_LIVE_TRADING` flag 하나만으로 주문 허용 안 됨(별도 Live Capital Review + Manual Approval + Symbol Whitelist + Max Notional 필요), 실제 live 주문 경로/ KIS live endpoint/주문 생성 0건. broker/OrderExecutor/route_order/외부 HTTP/AI SDK import 0건, 안전 flag 변경 0건, paper capital 을 live notional 계산에 사용 0건. 정책: [`docs/capital_allocation_policy.md`](docs/capital_allocation_policy.md), 테스트: `backend/tests/test_paper_live_capital_separation.py`(24) + `test_capital_allocation_policy.py`(48).

**#42 / 5-02 실전 주문 = Manual Approval 전용 Gate**: 실전(LIVE) 주문을 환경변수 하나로 켤 수 없게 고정 — `app/permission/live_manual_approval_gate.py::evaluate_live_manual_approval_gate`(read-only advisory). 실전 주문 요청(KIS_LIVE / kis_is_paper=false / live_order_requested / LIVE_* 모드)은 다음을 *모두* 통과해야 검토 가능: Live Capital Review + Manual Approval + Operator Approval(operator+사유+시각) + Symbol Whitelist(+종목 포함) + Max order notional + Daily live limit. **`ENABLE_LIVE_TRADING`/`ENABLE_AI_EXECUTION` 만으로는 절대 우회 불가**, Paper 승인/자금은 live 승인으로 재사용 불가(`PAPER_APPROVAL_NOT_LIVE_APPROVAL`). reason_code: `LIVE_MANUAL_APPROVAL_REQUIRED` / `OPERATOR_APPROVAL_REQUIRED` / `SYMBOL_WHITELIST_REQUIRED` / `SYMBOL_NOT_WHITELISTED` / `MAX_ORDER_NOTIONAL_REQUIRED` / `DAILY_LIVE_LIMIT_REQUIRED` / `LIVE_CAPITAL_REVIEW_REQUIRED` / `PAPER_APPROVAL_NOT_LIVE_APPROVAL` / `NOT_A_LIVE_ORDER` / `LIVE_MANUAL_GATE_REVIEW_READY`. **모든 조건 충족(approved=True)이라도 `is_live_authorization`/`broker_order_sent`/`order_created` 는 항상 False, `broker_order_no=None`** (dataclass `__post_init__` 가드) — gate 통과 = *검토 readiness* 일 뿐 실제 주문 0건. Paper/KIS_PAPER 경로 무회귀(NOT_A_LIVE_ORDER). broker/OrderExecutor/route_order/KIS live endpoint/외부 HTTP/AI SDK import 0건, 안전 flag 변경 0건, Secret/계좌번호 carry 0건. 정책: [`docs/live_manual_approval_gate.md`](docs/live_manual_approval_gate.md), 테스트: `test_live_manual_approval_gate.py`(20) + `test_live_manual_approval_policy.py`(9).

**#43 / 5-03 실전 Canary Gate (최소금액/최소종목/1일 1건)**: 제한적 실전 canary 검토 시에도 무제한으로 열리지 않도록 `app/permission/live_canary_gate.py::evaluate_live_canary_gate`(read-only advisory)가 #42 Manual Approval Gate 전체 통과 + Paper Gate(#72) PASS + `can_review_live_canary` + **LIVE_AI_EXECUTION 비활성** + **1일 1건**(`daily_order_count_limit==1`) + 최소/최대 주문금액(+ 최대 ≤ `CANARY_MAX_ALLOWED_ORDER_NOTIONAL_KRW=30000`) + daily live notional limit + risk profile=CONSERVATIVE + canary window 활성을 *모두* 요구. reason_code: `CANARY_PAPER_GATE_REQUIRED` / `CANARY_REVIEW_NOT_AVAILABLE` / `CANARY_AI_EXECUTION_BLOCKED` / `CANARY_DAILY_ORDER_LIMIT_REQUIRED` / `CANARY_DAILY_ORDER_LIMIT_EXCEEDED` / `CANARY_MIN_ORDER_NOTIONAL_REQUIRED` / `CANARY_MAX_ORDER_NOTIONAL_REQUIRED` / `CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH` / `CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED` / `CANARY_RISK_PROFILE_REQUIRED` / `CANARY_WINDOW_REQUIRED` / `CANARY_REVIEW_READY` (+ #42 codes pass-through). **canary_ready=True 여도 `is_live_authorization`/`broker_order_sent`/`order_created` 항상 False, `broker_order_no=None`, `enable_ai_execution_allowed=False`**(dataclass `__post_init__` 가드) — gate 통과 = 검토 readiness 일 뿐 주문 0건. **LIVE_AI_EXECUTION 은 canary gate 통과 전 불가.** Paper/KIS_PAPER 무회귀, 성과 기준은 기존 Paper Gate 참조만(완화 0). broker/OrderExecutor/route_order/KIS live endpoint import 0건, 안전 flag 변경 0건. 정책: [`docs/live_canary_gate.md`](docs/live_canary_gate.md), 테스트: `test_live_canary_gate.py`(24) + `test_live_canary_gate_policy.py`(9). (#42 위에 stack.)

**#44 / 5-04 Paper Gate 성과 기준 (28일/100건)**: 실전 전환 *검토* 전 Paper/KIS 모의 성과 표본·품질을 판정하는 stricter live-promotion 게이트 — `app/governance/paper_gate_performance.py::evaluate_paper_gate_performance`(read-only, #72 Paper Gate 보다 엄격). 최소 표본(둘 다): `evaluated_trades≥100` + `trading_days≥28`(미만 → `INSUFFICIENT_SAMPLE`). 성과: PF≥1.3 / win_rate≥0.50 / payoff≥1.0 / expectancy>0 / average_return>0. 리스크: MDD≤10% / 연속손실≤5 / daily_loss_breach=0 / kill_switch=0. 주문품질: order_failure_rate≤5% / rejected_rate≤3%(FAIL), partial_fill>30%·slippage>50bps(WARN). 무결성: portfolio_drift_critical=0 / event_integrity HIGH·CRITICAL=0 / secret_exposure=0 / broker_order_type_mismatch=0. verdict: `INSUFFICIENT_SAMPLE` / `BLOCKED`(FAIL≥1 → live promotion 차단) / `READY_FOR_LIVE_REVIEW`(전부 PASS → `can_review_live_canary=True`, #43 입력). per-criterion PASS/WARN/FAIL + reason_code. **기준 충족해도 `auto_live_promotion`/`is_live_authorization` 항상 False**(dataclass `__post_init__` 가드) — 결과는 "검토 가능성/차단"만, 자동 promotion 0건. 다음 게이트(Live Capital Review #41 + Manual Approval #42 + Canary #43) 별도 필요. broker/OrderExecutor/route_order/KIS live endpoint import 0건, DB write 0건, 안전 flag 변경 0건, Secret/계좌 carry 0건. 정책: [`docs/paper_gate_performance_criteria.md`](docs/paper_gate_performance_criteria.md), 테스트: `test_paper_gate_performance_criteria.py`(21) + `test_paper_gate_performance_policy.py`(9).

**#70 / 9-01 + #71 / 9-02 + #72 / 9-03 실매매 기본 OFF + KIS Paper/Live 분리 + Live Capital Review (정책/분리/Gate, 실전 활성화 아님)**: (70) 실매매 기본 OFF — `app/permission/live_trading_off_policy.py::evaluate_live_off_policy`(현재 안전 플래그를 입력 DTO로 받아 `live_path_gated=True`/`live_order_blocked=True`/`is_live_authorization=False` 불변 + reason_codes LIVE_ORDER_BLOCKED_BY_DEFAULT/LIVE_PATH_GATED/LIVE_REQUIRES_EXPLICIT_APPROVAL/LIVE_TRADING_DISABLED_BY_DEFAULT/LIVE_AI_EXECUTION_DISABLED_BY_DEFAULT/LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE). 어떤 flag 하나로도 실전 허용 0 — config default ENABLE_LIVE/AI/FUTURES=False·KIS_IS_PAPER=True 테스트로 고정. (71) KIS Paper/Live 완전 분리 — `app/kis/endpoints.py::resolve_kis_endpoint`(host=kis_client 단일 진실 재사용·일치 테스트, Paper `openapivts`/TR `V`/account PAPER vs Live `openapi`/TR `T`/account LIVE). `KIS_IS_PAPER=true`→PAPER만, `false`+explicit live gate 없음→**BLOCKED(host=None, fallback 금지)**, `false`+gate 통과→LIVE *선택*(주문은 여전히 place_order 가드+승인 Gate로 차단). dataclass가 PAPER↔LIVE host 교차를 ValueError로 거부(no fallback 불변). `KisBrokerAdapter.place_order(is_paper=False)` NotImplementedError 회귀 테스트로 재확인. (72) Live Capital Review — `app/permission/live_capital_review.py::build_live_capital_review`(#42 `evaluate_live_manual_approval_gate` *그대로 재사용*: operator approval+max_order_notional+daily_live_limit+symbol whitelist+live capital review+manual approval). 상태 MISSING/INCOMPLETE/**READY** — READY여도 `order_created=False`/`broker_order_sent=False`/`is_live_authorization=False`(검토 readiness≠주문 승인), Paper 자금은 live 한도에 미사용(분리). endpoint `GET /api/status/live-safety`(read-only, 3블록). 프론트엔드 `LiveSafetyStatusCard`(Settings 탭, 3블록+"실전매매 기본 OFF"/"Paper·Live 분리"/"Review는 주문 승인 아님"/"현재 실전 주문 차단" 문구, **실전 켜기/live order/approve live/매수/매도 버튼 0개·입력 form 0개**). broker/OrderExecutor/route_order/KIS 어댑터(엔드포인트 정책은 host 상수만 재사용) 주문·취소·route 호출 0건, 외부 HTTP/AI SDK 호출 0건, DB write 0건, 안전 flag default·`.env` 변경 0건, secret/계좌 출력 0건, `get_settings` import 0건(입력 DTO). **본 PR은 실전을 켜지 않으며 실전 전환 *승인*을 수행하지 않는다**(검토/표시/차단만). 38개 backend 테스트(off-policy 13+separation 14+review 11) + 7개 frontend 테스트 + 회귀(live_manual_approval_gate/exe_env_defaults/repository_hygiene/secret_exposure) 무회귀. 자세한 정책: [`docs/live_trading_off_policy.md`](docs/live_trading_off_policy.md).

**BUILD-01 최종 빌드 전 전체 프로그램 정합성 점검 (offline/fake, read-only)**: 지금까지 만든 기능이 *하나의 흐름* 으로 끊김 없이 연결되는지 모의 검증 — `app/system/program_integrity_gate.py::run_program_integrity_gate`. 16 섹션(Universe→KIS readiness→4전략 vote→Agent Council→RiskOfficer→exit_plan→quality_score→BUY/SELL/HOLD→KIS Paper decision→fake 주문 결과→order_quality→portfolio→outcome/review→feedback·quality→UI/API→Live safety)을 **기존 모듈 재사용**(universe_status/kis_paper.readiness/agent_council/decision_quality/order_quality/portfolio_snapshot/post_trade_outcome·review·feedback/live_trading_off_policy/kis.endpoints)으로 read-only 점검. 각 PASS/WARN/FAIL + `build_ready`(필수 섹션 FAIL 0 AND Live safety PASS, WARN 허용). **KIS 모의 주문 경로는 fake route_order(합성)** — 실제 broker/route_order/KIS API 호출 0건(BUILD-01 오프라인; 장중 실제 KIS 모의 API 는 BUILD-02). BUY 흐름은 council→exit_plan→quality→decision→KIS Paper decision(broker_order_type=KIS_PAPER, HOLD→없음)→fake 체결(PAPER-SIM)→order_quality→portfolio 까지 연결 확인, feedback auto_apply_allowed=False+다음 quality penalty 반영 확인, Live safety(실전 OFF+path gated+Paper/Live 분리). endpoint `GET /api/system/program-integrity`(실 app route 주입해 UI/API manifest 검증) + CLI `scripts/run_program_integrity_gate.py`(exit 0 build_ready·1 FAIL·2 오류) → `reports/build/` JSON+MD(gitignore). 프론트엔드 `ProgramIntegrityGateCard`(Settings 탭, 전체/섹션 PASS·WARN·FAIL+build_ready, 버튼은 점검 새로고침·리포트 복사만 — 주문/실전/승인 버튼 0개). **실전 승인 아님** — `ProgramIntegrityReport.is_live_authorization=False`/`broker_order_sent=False`/`order_created_live=False`/`contains_secret=False` 불변, "수익 보장"/"실전 전환 승인" 문구 0건. broker/OrderExecutor/단일 주문 라우터/paper_trader/KIS 어댑터/anthropic/openai/httpx/requests import 0건, DB write 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. 27개 backend 모듈 테스트 + 6개 CLI 테스트 + 8개 frontend 테스트 + 회귀(agent_council/secret_exposure/live_trading_off_policy) 무회귀. 자세한 절차: [`docs/prebuild_program_integrity_gate.md`](docs/prebuild_program_integrity_gate.md).

**BUILD-02A 장 열리기 전 사전 검증 (offline, read-only)**: 장이 열리기 전에도 확인 가능한 *전 영역* 을 한 리포트로 점검 — `app/system/premarket_readiness_gate.py::run_premarket_readiness_gate`. **fast mode**(기본, API+CLI, in-process): ENV_READINESS(LIVE/AI/FUTURES off + KIS_IS_PAPER true) / KIS_CREDENTIALS(present 여부만·원문 0건, 미설정→WARN) / PAPER_LIVE_SEPARATION(#71) / UNIVERSE_FALLBACK(#54) / PORTFOLIO_SOURCE(#55) / AGENT_CARDS(설명·성과·품질·피드백 backing #49~#52) / PROGRAM_INTEGRITY(BUILD-01 build_ready) / PREFLIGHT_SMOKE(#63, DB 주입 시) / DOCS_RUNBOOK(필수 문서 존재+문제 보고 양식+Claude Code 안내+금지 문구 *단언형만* 검출·부정/인용 허용) / REPORT_SCRIPTS(백테스트·Walk-forward·스트레스·정합성 스크립트 가용). **full mode**(CLI 전용): 추가로 ruff·pytest·security_scan·npm lint·test·build 명령 plan 을 *생성*(모듈) + *실행*(CLI), 실패 시 섹션 FAIL(감추지 않음), `--dry-run` 은 plan 만. 산출: `premarket_ready`(FAIL 0) / `build_ready_for_offline`(+PROGRAM_INTEGRITY PASS) / `ready_for_market_open_rehearsal`(+KIS 자격 present — 미설정 시 offline 빌드는 OK·리허설 불가). endpoint `GET /api/system/premarket-readiness`(fast, DB 주입해 preflight 실행) + CLI `scripts/run_premarket_readiness_gate.py`(--mode fast/full --dry-run --kis-credentials-present, exit 0 ready·1 FAIL·2 오류) → `reports/prebuild/` JSON+MD(gitignore). 프론트엔드 `PremarketReadinessGateCard`(Settings 탭, overall/premarket_ready/rehearsal+섹션+KIS WARN 안내+BUILD-02B 안내, 버튼은 점검 새로고침·결과 복사만 — 주문/실전/승인 버튼 0개). **BUILD-01 포함 + 위에 환경/자격/문서/테스트·빌드 가용성 추가**, 장중 실제 KIS 모의 주문/체결 polling 은 **BUILD-02B**. **실전 승인 아님** — `PremarketReadinessReport.is_live_authorization=False`/`broker_order_sent=False`/`order_created=False`/`contains_secret=False` 불변. broker/OrderExecutor/단일 주문 라우터/paper_trader/KIS 어댑터/KIS 실제 API/anthropic/openai/httpx/requests import·호출 0건, DB write 0건, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. 30개 backend 모듈 테스트 + 8개 CLI 테스트 + 9개 frontend 테스트 + 회귀(program_integrity/secret_exposure/live_trading_off_policy) 무회귀. 자세한 절차: [`docs/premarket_readiness_gate.md`](docs/premarket_readiness_gate.md).

**BUILD-02B-0 KIS 모의 AI 자동매매 전체 코드 감사 (offline/fake, read-only)**: KIS 모의 API 기반 AI 자동 매수/매도가 *판단 → KIS Paper 주문 결정 → 주문 결과 → 체결 품질 → 포트폴리오 반영 → outcome/review/feedback* 까지 코드 단위로 연결되는지 fake 로 감사 — `app/system/kis_paper_ai_autotrade_audit.py::run_kis_paper_ai_autotrade_audit`. 18 섹션: ENV/UNIVERSE/MARKET_DATA/STRATEGY_VOTES/AGENT_COUNCIL/RISK_OFFICER/EXIT_PLAN/QUALITY_GATE/PAPER_DECISION_BRIDGE(council BUY→KisPaperAutoDecision, HOLD→None)/PAPER_ORDER_EXECUTOR(result 계약 broker_order_type=KIS_PAPER·LIVE 생성 ValueError 차단)/BUY_SELL_LIMITS(권한 게이트 9종 + happy path ALLOWED, 장중 시각 주입)/SELL_TRIGGER(보유 청산만·naked SELL→HOLD)/ORDER_RESULT_QUALITY(fake route_order_fn 합성 KIS_PAPER 체결)/PORTFOLIO_APPLICATION(capital_state commit_buy/sell)/OUTCOME_REVIEW_FEEDBACK/UI_CONTRACTS/LIVE_SAFETY/FAKE_FLOWS(fake BUY/SELL/HOLD). **기존 검증 모듈 재사용** — 감사 모듈은 broker/OrderExecutor/route_order/KIS 어댑터를 import·호출하지 않고, 실제 executor(`execute_kis_paper_auto_order`)는 *테스트* 에서 fake route_order_fn + MockBrokerAdapter + dry_run=True 로만 실행(route_order 미호출, KIS API 0건). 핵심 판정: KIS 실제 API/실전 endpoint 호출 0, LIVE broker_order_type 0, is_live_authorization=False, risk_veto/exit invalid/quality low → 주문 decision 미생성, SELL 보유 필요, BUY exit_plan 필요. `paper_autotrade_ready`(FAIL 0) / `ready_for_market_open_rehearsal`(+KIS 자격 present). endpoint `GET /api/system/kis-paper-autotrade-audit` + CLI `scripts/run_kis_paper_autotrade_audit.py`(exit 0/1/2) → `reports/prebuild/`(gitignore). 프론트엔드 `KisPaperAiAutotradeAuditCard`(Settings 탭, 섹션 PASS·WARN·FAIL+ready+BUILD-02B 안내, 버튼은 점검 새로고침·결과 복사만 — 매수/매도/주문/실전/승인 버튼 0개). 장중 실제 KIS 모의 주문/체결 리허설은 **BUILD-02B**. `KisPaperAuditReport.is_live_authorization/broker_order_sent/order_created/contains_secret=False` 불변, 안전 flag·`.env` 변경 0건, secret/계좌 출력 0건. 35개 backend 모듈 테스트(실 executor offline 실행 포함) + 7개 CLI 테스트 + 7개 frontend 테스트 + 회귀(kis_paper_auto_trading_pipeline/kis_paper_sell_e2e/secret_exposure) 무회귀. 자세한 절차: [`docs/kis_paper_ai_autotrade_audit.md`](docs/kis_paper_ai_autotrade_audit.md).

**STRATEGY-VALIDATION-01 전략 가능성 종합 평가 (advisory, read-only)**: 백테스트(#46 strategy_council_backtest) + Walk-forward(#47) + Stress(#48) + Paper 성과 + order_quality/feedback 결과를 *종합* 해 전략을 5단계(STRONG_CANDIDATE / CAUTIOUS_CANDIDATE / RESEARCH_ONLY / NOT_READY / BLOCKED) 로 판정 — `app/system/strategy_potential.py::evaluate_strategy_potential`. 7개 sub-score(backtest / walk_forward / stress_resilience / paper_execution / agent_value / risk_control / data_sufficiency, 각 0~100 또는 None=평가불가) 가중 평균 → overall_strategy_potential_score. **본 모듈은 새 매매 로직 0건** — 기존 리포트의 `.to_dict()` 만 입력받아 종합한다. **data-sufficiency 게이팅**: `sample_fixture_only=True`(실데이터 아님) 또는 Paper 표본 부족이면 STRONG_CANDIDATE 판정 *불가*, STRONG 은 실데이터 + Paper Gate(100건/28거래일) + WF 안정 + Stress FAIL 0 + Agent 우위를 모두 충족할 때만. Paper 표본 4단계(PAPER_NO_TRADES_YET / PAPER_SAMPLE_TOO_SMALL / PAPER_EARLY_SIGNAL / PAPER_GATE_EVALUABLE). Agent 결합 효과 `agent_value_verdict`(AGENT_ADDS_VALUE / AGENT_RISK_REDUCTION_VALUE / AGENT_UNDERPERFORMS / AGENT_VALUE_INSUFFICIENT_SAMPLE) — "Agent Council 이 단일 전략보다 나은가". Stress FAIL ≥ 2 또는 secret/live_authorization 주장 → BLOCKED. **결과가 좋아 보여도 자동 적용 / 실전 전환 / 주문 0건** — `StrategyPotentialReport.do_not_auto_apply=True` / `auto_apply_allowed=False` / `is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` 불변(dataclass `__post_init__` ValueError 가드), method_fit.recommended_tuning_candidates 도 자동 적용 금지(운영자 검토 + 별도 PR + 재백테스트). 모듈은 broker / OrderExecutor / route_order / KIS API / `app.ai.client` / anthropic / openai / httpx / requests import 0건(정적 grep 가드), 안전 flag·`.env` 변경 0건. endpoint `GET /api/system/strategy-potential`(sample fixture in-process 종합, read-only, 주문 0건) + CLI `scripts/run_strategy_potential_report.py`(--run-sample / --backtest-json / --walk-forward-json / --stress-json / --paper-json / --has-real-data, exit 0 평가완료·1 BLOCKED·2 오류) → `reports/strategy_validation/`(gitignore). 프론트엔드 `StrategyPotentialReportCard`(AISignal 탭, verdict + 7 sub-score + 강점/약점/리스크/다음 단계 + "자동 적용 안 됨·실전 승인 아님·수익 보장 아님", 버튼은 새로고침·복사만 — 매수/매도/실전/자동적용/승인 버튼 0개, input/textarea 0개). 34개 backend 테스트(모듈 26 + CLI 8) + 12개 frontend 테스트. 자세한 정책: [`docs/strategy_potential_validation.md`](docs/strategy_potential_validation.md).

**REAL-DATA-STRATEGY-01 실제/준실제 데이터 기반 전략 검증 (advisory, read-only)**: 사용자 핵심 질문 "*실제 데이터* 기준 내 매매기법 + Agent 결합 전략이 가능성 있는가" 에 답하기 위해 sample fixture 가 *아닌* 실제/준실제 OHLCV 로 검증 — `app/system/real_data_strategy.py::evaluate_real_data_strategy`. 신규 `app/market_data/` 패키지(`ohlcv_quality.py` 무결성/표본 검증 + `real_ohlcv_loader.py` CSV/디렉토리/yfinance 로더, **기존 `app.backtest.real_data.loader` + `load_ohlcv_from_csv` 재사용**). **KIS historical 시세 API 는 미구현** (`kis_historical_supported()==False` — KIS Client 는 현재가/잔고/당일체결만 제공, 과거 일봉 endpoint 없음) → CSV(권장)/yfinance(옵션, 네트워크) 사용, **KIS 주문 API 호출 0건**. backtest(#46)/walk-forward(#47)/stress(#48) 실행 후 STRATEGY-VALIDATION-01 `evaluate_strategy_potential` 재사용해 5단계 verdict 산출. **데이터 출처 분류** (CSV_USER / CSV_REAL_FIXTURE / YFINANCE / SAMPLE_FIXTURE) → `real_data_used` / `sample_fixture_only`. OHLCV 품질: 잘못된 OHLC(low>open/close, high<open/close, high<low) / 0이하 가격 / 음수 거래량 → FAIL, 중복 timestamp / 표본 부족(<100 bar·<28일) → WARN. **verdict cap (실데이터 특화 보수 제한)**: 품질 FAIL → BLOCKED, sample fixture only → 최대 RESEARCH_ONLY, 실데이터지만 거래 수<100 → 최대 CAUTIOUS_CANDIDATE. 시장국면/시간대 favorable/dangerous + Agent 도움/방해(`agent_value_verdict`) + 사용자 매매기법 강점/약점/튜닝 후보(자동 적용 금지) 산출. **결과가 좋아도 자동 적용/실전 전환/주문 0건** — `RealDataStrategyReport.do_not_auto_apply=True` / `auto_apply_allowed=False` / `is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` / `kis_historical_available=False` 불변(dataclass `__post_init__` ValueError 가드). 모듈은 broker / OrderExecutor / route_order / KIS 주문 API / anthropic / openai / httpx / requests / `app.ai.client` import 0건(정적 grep 가드), 안전 flag·`.env` 변경 0건. endpoint `GET /api/system/real-data-strategy-validation/latest`(latest 리포트 파일 또는 quasi-real 데모 즉석 계산, read-only, 주문 0건) + CLI `scripts/run_real_data_strategy_validation.py`(--input-csv / --input-dir / --symbols / --allow-yfinance / --kis-historical(무시) / --min-trades / --min-days / --strict / --write-latest, exit 0 평가완료·1 BLOCKED·2 오류) → `reports/strategy_validation/`(gitignore). 프론트엔드 `RealDataStrategyValidationCard`(AISignal 탭, data_source/real_data_used/sample fixture 경고/표본/4 score/verdict/강한·위험 국면/Agent 도움·방해/다음 단계, 버튼은 새로고침·복사만 — 매수/매도/실전/자동적용/승인 버튼 0개, input/textarea 0개). quasi-real 데모 fixture `backend/tests/fixtures/real_data/demo_quasi_real.csv`(130 bar, 무결 OHLC) 추가. 기존 `005930.csv` 는 잘못된 OHLC 15건 포함 → 품질 검증이 BLOCKED 로 검출(테스트로 확인). 37개 backend 테스트(loader/quality 18 + 오케스트레이터/endpoint 12 + CLI 7) + 15개 frontend 테스트. 자세한 정책: [`docs/real_data_strategy_validation.md`](docs/real_data_strategy_validation.md).

**REAL-DATA-INPUT-01 실제 OHLCV 데이터셋 확보 + 다종목 Backtest/Walk-forward (advisory, read-only)**: REAL-DATA-STRATEGY-01 위에 1순위 *깨끗한 실제 OHLCV 확보* + 2순위 *다종목 실데이터 backtest/walk-forward 집계* 추가. (A) 수집: `app/market_data/ohlcv_collector.py::collect_ohlcv` + CLI `scripts/collect_real_ohlcv_data.py`(--source existing|yfinance, --symbols, --output-dir, --min-days, --recommended-days, --strict). 품질 OK→PASS 만 output_dir 에 기록, **품질 FAIL CSV 는 통과/기록 금지, 깨진 OHLC 자동 보정 0건, 수집 실패를 성공으로 표시 0건, sample/mock 대체 0건**. yfinance 미설치/네트워크 실패 → 명확한 FAIL(`yfinance_available` carry). exit 0(≥1 PASS)/1(전부 FAIL or strict)/2. (B) 다종목 집계: `app/system/real_ohlcv_dataset.py::evaluate_real_ohlcv_dataset` + CLI `scripts/run_real_ohlcv_backtest_walkforward.py`(--input-dir, --symbols, --min-trades, --min-days, --strict, --write-latest). input-dir 의 {symbol}.csv 를 *종목별 품질검증* → PASS 종목만 backtest+walk-forward(기존 #46/#47 + REAL-DATA-STRATEGY-01 `evaluate_real_data_strategy`/`evaluate_strategy_potential` 재사용) → 종목별 `per_symbol`(PF/expectancy/MDD/WF/agent/verdict) + 전체 aggregate(median PF/expectancy/MDD/WF, total_trades, `agent_value_summary` + 도움/방해/무진입 종목). **verdict cap**: PASS 0 → BLOCKED, 품질 FAIL 과반 → NOT_READY, sample_only → RESEARCH_ONLY, total_trades<100 → RESEARCH_ONLY, median WF<40 → RESEARCH_ONLY, Agent underperform ≥50% → RESEARCH_ONLY, Paper 0건 → 실전 검토 불가 문구. 데이터 저장: 실데이터 `data/market/real_ohlcv/`(**gitignore**), 테스트 소형 fixture `backend/tests/fixtures/real_data_clean/`(10종목 180 bar 무결 OHLC, *추적*). 기존 `005930.csv`(잘못된 OHLC 15건)는 계속 BLOCKED 로 검출(테스트 lock). **결과가 좋아도 자동 적용/실전 전환/주문 0건** — `RealOhlcvDatasetReport.do_not_auto_apply=True` / `auto_apply_allowed=False` / `is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` / `kis_historical_available=False` 불변(dataclass `__post_init__` 가드). 모듈은 broker / OrderExecutor / route_order / KIS 주문 API / anthropic / openai / httpx / requests / `app.ai.client` import 0건(정적 grep 가드), 안전 flag·`.env` 변경 0건. UI: `RealDataStrategyValidationCard`(AISignal) 가 dataset 리포트 시 PASS/BLOCKED 종목 + total_trades·median PF·median WF·Agent 효과 요약 추가 표시(`--write-latest` → `GET /api/system/real-data-strategy-validation/latest`), 매수/매도/실전/자동적용/승인 버튼 0개. 26개 backend 테스트(collector/gitignore 12 + dataset/CLI 14) + 5개 frontend dataset 테스트. 자세한 절차: [`docs/real_ohlcv_data_collection_and_validation.md`](docs/real_ohlcv_data_collection_and_validation.md).

**INTRADAY-DATA-01 분봉(intraday) 데이터 기반 단타 전략 검증 (advisory, read-only)**: ORB/VWAP/Momentum/Gap/Agent Council 은 *장중 단타* 전략이라 **일봉으로는 진입이 발생하지 않는다**(REAL-DATA-INPUT-01 §0: 실 일봉 10종목 total_trades=0). 따라서 **분봉** 으로 검증. (A) 품질/로더 `app/market_data/intraday_ohlcv.py::check_intraday_quality`/`load_intraday_csv` — 분봉 특화: timestamp datetime 파싱 + 장중(KST 09:00~15:30) 검증 + 일중 평균 bar 수(<5 면 **일봉으로 간주 FAIL**) + `bar_size_minutes`(연속 bar 간격 중앙값) + OHLC 무결성(`_is_invalid_ohlc` 재사용) + `sanitize_ohlcv_bars`(잘못된 row 드롭, 과다 FAIL). FAIL=OHLC 손상/파싱 과다/분봉 아님, WARN=중복/장중밖/표본부족. (B) 오케스트레이터 `app/system/intraday_strategy_validation.py::evaluate_intraday_strategy` + CLI `scripts/run_intraday_strategy_validation.py`(--input-dir, --symbols, --strict, --output/--json, --write-latest) — PASS 종목만 backtest(#46)+walk-forward(#47) → 종목별 `per_symbol`(trades/PF/expectancy/WF/agent/verdict) + 집계(median win_rate/PF/expectancy/MDD/WF, total_trades, `agent_value_summary` 6종 AGENT_ADDS_VALUE/RISK_REDUCTION/UNDERPERFORMS/TOO_CONSERVATIVE/INSUFFICIENT/MIXED + 무진입 종목). **단타 전용 caps**: PASS 0 → BLOCKED, 품질 FAIL 과반 → NOT_READY, total_trades 0/<30 → RESEARCH_ONLY, <100 → 최대 CAUTIOUS, median WF<40 → RESEARCH_ONLY, Agent 무진입 ≥50% → RESEARCH_ONLY, Paper 0건 → 실전 검토 불가(STRONG 도달 불가). KIS 분봉 시세 API 미구현(`kis_intraday_supported()==False`) → 분봉 CSV 입력 사용, **KIS 주문 API 호출 0건**. 데이터: 실데이터 `data/market/intraday/`(gitignore), 테스트 fixture `backend/tests/fixtures/intraday_clean/`(5종목 × 12거래일 × 78봉 5분봉, *추적*). 분봉 fixture 는 진입이 발생(council BUY 수백 건 → total_trades>0)해 일봉과 대조 검증 — clean 합성 분봉은 PF/win_rate 가 비현실적으로 높을 수 있어(overfit) caps 가 RESEARCH_ONLY 로 보수적으로 묶음. **결과가 좋아도 자동 적용/실전 전환/주문 0건** — `IntradayStrategyReport.do_not_auto_apply=True` / `auto_apply_allowed=False` / `is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` / `kis_intraday_available=False` 불변(dataclass 가드). 모듈은 broker / OrderExecutor / route_order / KIS 주문 API / anthropic / openai / httpx / requests / `app.ai.client` import 0건(정적 grep 가드), 안전 flag·`.env` 변경 0건. endpoint `GET /api/system/intraday-strategy-validation/latest`(**무거운 실행 금지 — 파일 없으면 empty fallback**, CLI `--write-latest` 전용) + UI `IntradayStrategyValidationCard`(AISignal, intraday_data_used/bar_size/PASS·BLOCKED/total_trades/win_rate·PF·WF/Agent 무진입·효과, 새로고침·복사만 — 매수/매도/실전/자동적용/승인 버튼 0개, input 0개). 34개 backend 테스트(loader/quality 11 + 오케스트레이터/endpoint 12 + CLI 7 + sanitize 4) + 12개 frontend 테스트. 자세한 절차: [`docs/intraday_ohlcv_strategy_validation.md`](docs/intraday_ohlcv_strategy_validation.md).

**INTRADAY-DATA-02 실제 분봉 CSV 확보 경로 + KIS read-only collector 준비 (read-only)**: INTRADAY-DATA-01 위에 ① 운영자가 증권사/HTS 분봉 CSV 를 표준 경로에 넣어 바로 검증, ② KIS 분봉 read-only collector 의 *안전 구조*(공식 endpoint 미확인 시 차단)를 추가. **표준 입력 경로 통일**: `data/market/intraday_ohlcv/`(gitignore), 파일명 `005930_5m.csv`(bar-size 추론) 또는 `005930.csv`(--bar-size). (A) `app/market_data/intraday_csv_normalizer.py::normalize_intraday_csv` — 한글 컬럼(일자/시가/고가/저가/종가/거래량/현재가/체결가 등) → 표준 매핑 + 쉼표/"원"/"%" 제거 + cp949/utf-8-sig 인코딩 처리, **값 생성 0(가짜 데이터 없음)·파싱 불가 row 드롭(보정 아님)·카운트**, 필수 컬럼 누락 보고. CLI `scripts/normalize_intraday_csv.py`(--input/--output/--symbol, exit 0/1/2). `load_intraday_csv` 가 normalizer 내장 → 한글 CSV 도 파이프라인이 바로 처리. (B) `app/market_data/kis_intraday_collector.py` — **안전 placeholder**: 기본 `NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION`(공식 분봉 quotation endpoint/TR ID 확인 전까지 실제 호출 0건), 상태 5종(NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION/DISABLED/BLOCKED_NOT_READ_ONLY/READY_READ_ONLY) — env `KIS_INTRADAY_ENABLED=false`/`KIS_INTRADAY_ENDPOINT`/`KIS_INTRADAY_TR_ID`/`KIS_INTRADAY_READ_ONLY=true`. `collect_intraday_via_kis` 는 **어떤 경우에도 실제 요청 0건**(`requested=False` 불변), READY_READ_ONLY 여도 fetch 미구현(별도 PR). **httpx/requests/urllib/place_order/OrderExecutor/route_order import 0건**(구조적으로 호출 불가, 정적 grep 가드), 상태에 endpoint/secret 원문 0건(present 여부 bool 만, `is_order_endpoint=False`/`contains_secret=False` 불변). 공식 미확인 endpoint 를 production 에 확정값처럼 하드코딩 0건. (C) CLI 보강: `run_intraday_strategy_validation.py` 기본 `--input-dir=data/market/intraday_ohlcv` + `--bar-size`/`--min-bars`/`--min-days`. endpoint `GET /api/system/intraday-data-source/status`(표준 경로 파일현황 + KIS collector 상태, read-only, KIS 실제 호출 0건, secret 원문 0건) + UI `IntradayStrategyValidationCard` 가 데이터 경로/입력 모드(CSV/KIS_PLACEHOLDER)/KIS collector 상태 + NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION 안내 추가 표시(매수/매도/실전/자동적용/승인 버튼 0개). 안전 flag·`.env` 변경 0건, broker/OrderExecutor/route_order/KIS 주문 API 호출 0건. 32개 신규 backend 테스트(normalizer 11 + KIS collector 11 + endpoint 2 + 기타) + 4개 frontend data-source 테스트. 자세한 절차: [`docs/intraday_data_source_collection.md`](docs/intraday_data_source_collection.md).

**REAL-INTRADAY-TEST-01 실제 분봉 데이터 기반 전략 가능성 최종 테스트 (advisory, read-only)**: 사용자 핵심 질문 "내 전략이 *실제 데이터* 기준 가능성 있는가" 에 끝까지 답 — 실제 분봉 수집 → 검증 → *사용자용 최종 판단*. (A) `scripts/collect_yfinance_intraday_ohlcv.py` — yfinance 분봉(005930→005930.KS 변환, 1m/5m/15m, period 5d/30d/60d) 수집, 실패는 reason_code(YFINANCE_NOT_INSTALLED/NETWORK_ERROR/EMPTY_RESPONSE/YFINANCE_INTRADAY_LIMIT/QUALITY_FAILED)로 기록, **합성 대체 0건·수집 실패를 성공으로 보고 0건**, 품질 FAIL 종목 미기록. (B) `app/system/real_intraday_final_result.py::build_final_result` — IntradayStrategyReport 에 *실제 데이터 여부/표본* 게이팅 적용해 개발자 verdict(STRONG~BLOCKED) + **사용자 판단 6단계**(PROMISING_FOR_PAPER_TEST / WORTH_MORE_RESEARCH / TOO_EARLY_TO_JUDGE / STRATEGY_NEEDS_TUNING / NOT_PROMISING_ON_CURRENT_DATA / BLOCKED_BY_DATA) + 한 줄 결론 산출. 게이팅: PASS 0/합성뿐 → BLOCKED_BY_DATA, 합성 fixture → 최대 TOO_EARLY(실제 가능성 판단 불가), total_trades 0 → STRATEGY_NEEDS_TUNING, <30 → TOO_EARLY, <100 → 최대 WORTH_MORE_RESEARCH, 종목<3 → 최대 TOO_EARLY, 충분거래+PF<1+expectancy≤0 → NOT_PROMISING, PROMISING 은 실데이터+거래100+·PF≥1.2·expectancy>0·WF≥40·Agent 도움 모두 충족 시(그래도 *실전 아님*, Paper 리허설 권고). CLI `scripts/run_real_intraday_final_test.py` → `reports/strategy_validation/real_intraday_final_result.{md,json}` + `intraday_final_latest.json`(gitignore). **실측(2026-05-25, yfinance 5m 60d 10종목)**: actual_data_used=True, total_trades 4,719, median PF ~1.10, median expectancy 양(+), Agent **AGENT_ADDS_VALUE**(도움 6/방해 4), 그러나 median walk_forward_score ~0(과최적화 의심) → developer `RESEARCH_ONLY` / user **`WORTH_MORE_RESEARCH`**("연구 가치 있으나 튜닝 필요"). **결과가 좋아도 자동 적용/실전 전환/주문 0건** — `RealIntradayFinalResult.do_not_auto_apply=True` / `is_live_authorization=False` / `is_order_signal=False` / `contains_secret=False` / `no_profit_guarantee=True` 불변(dataclass 가드). 모듈 broker/OrderExecutor/route_order/KIS 주문 API/anthropic/openai/httpx/requests/`app.ai.client` import 0건. endpoint `GET /api/system/real-intraday-final-result/latest`(파일 없으면 empty fallback, 무거운 backtest API 미실행) + UI `IntradayStrategyValidationCard` 상단에 *사용자 최종 판단 headline*(한 줄 결론 + 실제 데이터 여부 + Paper 권고 + "실전 승인 아님·수익 보장 아님") 표시, 매수/매도/실전/승인 버튼 0개. 27개 신규 backend 테스트(final-result 판정 18 + endpoint 2 + collector 5 + 기타) + 5개 frontend final-judgement 테스트. 자세한 절차: [`docs/real_intraday_strategy_test.md`](docs/real_intraday_strategy_test.md).

**체크리스트 11-00 / BUILD-FINAL-GATE — EXE 빌드 전 통합 검증 Gate (read-only)**: EXE 빌드 직전 전체(health/config/security/DB/Universe/Portfolio/Agent/Order/Stress/Backtest/Walk-forward/Intraday real-data/Live safety/UI·API/문서/CI)를 한 번에 점검해 **빌드해도 되는 상태인지** 판정 — `app/system/final_prebuild_gate.py::run_final_prebuild_gate` (순수 합성 함수, 호출자가 섹션 결과를 입력 DTO 로 전달). 판정 3종: `BUILD_READY` / `BUILD_READY_WITH_WARNINGS` / `BUILD_BLOCKED`(+ exe_build_allowed / ready_for_market_rehearsal / ready_for_paper_rehearsal). **BLOCKED 조건**: backend pytest/frontend build 실패, security findings/secret/.env 추적, LIVE·AI·FUTURES=true, KIS_IS_PAPER=false, KIS live 주문 경로, broker/OrderExecutor/route_order 직접 호출, BUILD-01/02A/02B FAIL, UI/API manifest FAIL, DB 실패, Universe 0+fallback 없음, Portfolio mixed, EXE 빌드 필수파일 누락, 주문/실전/승인 버튼, require-kis-credentials 모드 자격 missing. **WARN(빌드 차단 아님)**: KIS 자격 미설정, Paper 표본 부족, 실제 분봉 전략 WORTH_MORE_RESEARCH(전략 운영 확장 WARN), 닫기권고 open PR, fast 모드 미실행 섹션, Cargo.lock 미추적 등. CLI `scripts/run_final_prebuild_gate.py`(fast: 안전 플래그+security_scan+ruff+pytest collection+리포트 파일+정적 repo 점검 / --full: frontend build 추가 / --require-kis-credentials, exit 0·1·2) → `reports/final_prebuild/`(gitignore). endpoint `GET /api/system/final-prebuild-gate`(**fast, subprocess/무거운 테스트/KIS 호출 없음** — 안전 플래그+리포트 파일+정적 입력 합성, backend/frontend/security 섹션은 fast 에서 WARN, cwd 비의존 repo-root 절대경로) + UI `FinalPrebuildGateCard`(Settings 탭, 전체 상태/빌드 가능 여부/PASS·WARN·FAIL/섹션/BLOCKER/WARN/다음 단계, 새로고침·복사만 — 매수/매도/실전/자동적용/승인 버튼 0개). **`FinalPrebuildReport.is_live_authorization=False` / `broker_order_sent=False` / `order_created=False` / `contains_secret=False` / `no_profit_guarantee=True` 불변(dataclass 가드)**. 모듈 broker/OrderExecutor/route_order/anthropic/openai/httpx/requests import 0건. **'빌드 가능'과 '전략 유망'은 다르다** — 실제 분봉 전략 WORTH_MORE_RESEARCH 여도 빌드는 가능. **실측(2026-05-25, fast)**: BUILD_READY_WITH_WARNINGS(PASS 13/WARN 8/FAIL 0) — ruff·security 0·collection·안전 플래그 정상 → 빌드 가능, WARN 은 KIS 자격·Paper 표본·분봉 WMR·fast 미실행 섹션. 34개 신규 backend 테스트(gate 매트릭스 28 + endpoint 2 + CLI 4) + 12개 frontend 테스트. 자세한 절차: [`docs/final_prebuild_integrated_gate.md`](docs/final_prebuild_integrated_gate.md).

**KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 대규모 전략검증 (read-only)**: KIS 연결 확인이 아니라 *신뢰성 있는 실제 KIS 분봉* 으로 현재 결합 전략(ORB/Momentum/Gap/VWAP + Agent Council + RiskOfficer + exit_plan + quality_score gate)의 가능성을 종합 판정. 사용 API: **주식일별분봉조회 [국내주식-213]** `GET /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` TR `FHKST03010230` (read-only 시세, *주문 API 아님*). 신규: `app/market_data/kis_intraday_universe.py`(top50+보강 100종목, 우선주/중복/invalid 제외, 순수 데이터) + `KisClient.inquire_time_dailychartprice`(read-only 분봉, 토큰 관리 재사용) + `app/market_data/kis_intraday_fetch.py`(응답 파싱/1m→5m resample, httpx/requests import 0건) + `scripts/collect_kis_intraday_ohlcv.py`(단계적 1→5→20→100 수집, 토큰 gitignored 캐시로 `EGW00133` 1분당 1회 회피, `EGW00201` 초당 초과 backoff, CSV→`data/market/intraday_ohlcv/kis/{symbol}_5m.csv` gitignore) + `app/system/kis_intraday_100_final_result.py`(품질 PASS-only backtest+walk-forward+stress+Agent vs 단일전략 → 개발자 verdict + 사용자 6단계 판단, 기존 `intraday_strategy_validation`/`real_intraday_final_result`/`agent_stress_test` 재사용) + `scripts/run_kis_intraday_100_validation.py`(→ `reports/strategy_validation/kis_intraday_100_final_result.{md,json}` gitignore). KIS 대규모 cap: 품질 PASS<30 → `DATA_NOT_RELIABLE`; PROMISING 은 실데이터+PASS≥70+trades≥500+median PF≥1.2+expectancy>0+WF≥40+Agent positive+stress FAIL 0 모두 충족 시만(미달 시 최대 `WORTH_MORE_RESEARCH`), PROMISING 이어도 실전 아님(Paper 100건+28거래일+운영자 승인 필요). endpoint `GET /api/system/kis-intraday-100-validation/latest`(latest 리포트, 없으면 empty fallback, 무거운 수집/backtest 미실행) + UI `KisIntraday100ValidationCard`(AISignal 탭, verdict/수집/품질/성과/Agent/상위종목/다음단계 — 새로고침·복사만, 매수/매도/실전/자동적용/승인 버튼 0개·input/textarea 0개). **KIS 주문 API 호출 0건**(`inquire-time-dailychartprice` 만), broker/OrderExecutor/route_order/place_order 호출 0건, 안전 flag(LIVE/AI/FUTURES/KIS_IS_PAPER) 변경 0건, appkey/secret/token/계좌 원문 출력 0건, 자동 적용/실전 전환/threshold 자동 반영 0건, 수익 보장 문구 0건. `KisIntraday100Result.is_live_authorization`/`broker_order_sent`/`order_created`/`is_order_signal`/`kis_order_api_called`/`contains_secret`=False · `do_not_auto_apply`/`no_profit_guarantee`=True 불변(dataclass 가드). 27개 backend 테스트 + 13개 frontend 테스트. 자세한 절차: [`docs/kis_intraday_100_validation.md`](docs/kis_intraday_100_validation.md).

**KIS-INTRADAY-1Y-SCALED-VALIDATION-01 — 1년 데이터 10/25/50 확장 검증 (read-only)**: 새 forward 데이터를 기다리는 대신 *과거 1년* 5분봉으로 고정 룰(`FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1`)을 변경 없이 10→25→50 종목 확장 검증 — 종목 폭 의존성·분기/반기 안정성·RISK_VETO 유효성. KIS 분봉이 ~1년 전(2025-05-26)까지 제공 확인 → 기존 `kis_6m`(2025-11-25~) 보존 + older 6개월(`data/market/intraday_5m_1y_old`, 2025-05-27~2025-11-24) 별도 수집 후 병합(중복 제거)≈240거래일. `app/system/intraday_1y_scaled_validation.py`(1년 병합 + rule hash lock + 10/25/50 단계 locked-rule 백테스트 + 월/분기/반기/worst-month/symbol-split/Agent/cost/defense + scale verdict) + 수집은 기존 read-only collector(`collect_kis_intraday_ohlcv.py`) 재사용(progress/failed json) + CLI `run_intraday_1y_scaled_validation.py` + endpoint `GET /api/system/intraday-1y-scaled-validation/latest` + UI `Intraday1YScaledValidationCard`(AISignal 탭, 데이터없음/수집중/분석중/완료/실패). verdict: SCALE_BLOCKED(hash 불일치)/SCALE_FAIL(데이터부족·<0·PF<1.05·MDD>20)/WEAK/WATCH(≥3·PF≥1.10·MDD≤15)/PAPER_CANDIDATE(≥5·PF≥1.15·MDD≤15·거래≥150·posR≥0.5·slip OK·RISK_VETO 우위)/RESEARCH_CONFIRMED(≥8·PF≥1.20·MDD≤12). **전체 verdict는 단계 최소(보수)** — 50종목 통과해도 10/25 FAIL 시 WATCH cap. `live_trading_recommendation`·`real_order_allowed`=False·`dry_run_required`=True 항상. EXE 판단: WATCH→관찰용 재빌드(자동매매 OFF) / PAPER_CANDIDATE↑(50종목 1년 통과)→dry-run(자동주문 OFF) EXE 검토(실전 금지, 실제 모의주문 별도 승인 게이트) / 10만 좋고 25·50 약화→breadth 의존→WATCH 이하. **Paper/Backtest only · EXE 빌드 0건 · 실전 금지** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건, rule hash 로 검증 중 파라미터 변경 0건, look-ahead 0건, 기존 6개월 CSV 보존, 안전 flag 변경 0건, 수익 보장 문구 0건. `Scaled1YReport.is_live_authorization`/`live_trading_recommendation`/`real_order_allowed`/`broker_order_sent`/`order_created`/`exe_build_executed`=False·`dry_run_required`/`do_not_auto_apply`/`no_profit_guarantee`=True·`auto_apply_allowed`=False 불변(dataclass 가드). 10개 backend + 8개 frontend 테스트. (1년 수집 ~3h 후 실측 latest.json 기록.) 자세한 절차: [`docs/intraday_1y_scaled_validation.md`](docs/intraday_1y_scaled_validation.md).

**KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01 — robust 분봉 데이터셋 수집/품질/메타데이터 (read-only, 백테스트 0건)**: 종목 수만 늘리는 대신 *정확한 장기 검증*을 위해 3개 축(시간분할 Train/Validation/Test·OOS / 장세 구분 8종 / 종목 유형 4그룹)을 반영한 robust 데이터셋을 **별도 경로**에 수집 — **본 작업은 수집/품질검증/분할 메타데이터 구축 전용, 백테스트를 실행하지 않는다.** 신규 순수 모듈 `app/market_data/robust_dataset.py`(종목군 `build_robust_symbol_groups` LARGE_CAP10/MID_CAP10/HIGH_VOL_THEME10/ETF_PROXY5=35 + 선정이유·group 라벨 / 시간분할 `build_time_split` 50·25·25 TWO_YEAR/ONE_YEAR/MIN/INSUFFICIENT, **test_used_for_selection=False 불변**(Test/OOS 는 selector·파라미터 선택 금지) / 장세 `label_market_regimes`(ETF proxy 069500 우선·없으면 대형+중형 equal-weight, 8종 UPTREND/DOWNTREND/SIDEWAYS/HIGH_VOLATILITY/GAP_UP_DAY/GAP_DOWN_DAY/CRASH_LIKE_DAY/LOW_LIQUIDITY_DAY + regime_primary + confidence, lookback 20일 ret20·ma slope·vol20·gap·vol_ratio formula 명시, **no_look_ahead=True 불변**·미래 데이터로 과거 라벨링 0) / 품질 `evaluate_dataset_quality`(종목별·그룹별 + split_feasible + regime_feasible) / `build_dataset_manifest`(ready_for_robust_backtest = 품질 PASS·WARN + split 가능 + ≥30종목 + ≥200거래일 + regime 가능)). 수집기 `scripts/collect_robust_intraday_dataset.py`(기존 `collect_kis_intraday_ohlcv` read-only KIS 분봉 엔진[국내주식-213 FHKST03010230] *그대로 재사용* — resume/원자저장/중복제거/EGW00201 backoff/토큰캐시 승계, 5m → `data/market/robust_intraday_5m`, 1m 정밀 subset 대표10종목 → `data/market/robust_intraday_1m_subset`, **1분봉 미제공/실패는 FAIL 아닌 UNAVAILABLE**, 기존 6개월/1년 데이터 보존·별도 경로) + 검증/manifest `scripts/validate_robust_intraday_dataset.py`(read-only 적재→품질·시간분할·regime·manifest json/md, **백테스트 0건**) + endpoint `GET /api/system/robust-dataset/status`(latest snapshot 또는 종목군 manifest empty fallback, 무거운 적재/KIS 호출 0건) + UI `RobustDatasetStatusCard`(AISignal 탭, 수집·품질 status/종목군/기간·거래일/시간분할·regime·1분봉/ready/경고/다음작업 — 새로고침·복사만, **주문/실전/자동매매 시작/EXE 빌드/적용 버튼 0개·input/textarea 0개**). 리포트는 `reports/strategy_validation/robust_*`(gitignore). **소규모 실측(read-only, representative10·5m·3일)**: 10/10 종목 980 bars 수집 성공 → validate honest 부분 manifest(quality WARN·split FAIL(2일)·10/35종목·ready=False) — 파이프라인 실데이터 동작 확인. 전량(robust50·2y→KIS ~1년) 수집은 운영자 장시간 read-only 작업. **수집/검증 전용 · 백테스트/주문/실전 전환/EXE 빌드 0건** — broker/OrderExecutor/route_order/KIS 주문 API/anthropic/openai/httpx/requests/`app.ai.client` import·호출 0건, place_order/route_order/cargo·tauri build 0건(정적 grep), 안전 flag 변경 0건, 기존 데이터 삭제 0건, 수익 보장 문구 0건. `SymbolGroupManifest`/`TimeSplitManifest`/`RegimeManifest`/`DatasetQualityReport`/`DatasetManifest` 모두 `is_live_authorization`/`real_order_allowed`/`live_trading_recommendation`/`is_order_signal`/`kis_order_api_called`/`broker_order_sent`/`exe_build_executed`/`contains_secret`=False·`do_not_auto_apply`/`no_profit_guarantee`=True 불변(dataclass 가드). 30개 backend + 8개 frontend 테스트. 자세한 절차: [`docs/robust_dataset_collection.md`](docs/robust_dataset_collection.md).

**KIS-INTRADAY-60D-WEEKLY-NEW-DATA — 고정 룰 추가 기간 새 데이터 재검증 (read-only)**: 60d/weekly 고정 룰(`FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1`)이 *기존 6개월 밖 새 데이터* 에서도 유지되는지 — 룰을 **변경 없이**(rule hash `EXPECTED_LOCKED_RULE_HASH`, 불일치→BLOCKED) 적용, look-ahead 금지. `scripts/collect_forward_extra_ohlcv.py`(기존 read-only collector 재사용, 추가 기간을 별도 경로 `data/market/intraday_5m_forward_extra/` 에 resume 수집 + progress/failed/quality, 기존 6개월 보존, KIS 주문 API 0건) + `app/system/locked_60d_weekly_new_data_validation.py`(rule hash lock + 새 거래일 계산 + 충분 시 new-data-only/extended WF/slippage/agent/breadth/decay + verdict) + CLI `run_locked_60d_weekly_new_data_validation.py` + endpoint `GET /api/system/locked-60d-weekly-new-data/latest` + UI `Locked60dWeeklyNewDataCard`(AISignal 탭, 데이터없음/수집중/분석중/완료/실패). verdict: NEW_DATA_BLOCKED(hash 불일치)/**NEW_DATA_INSUFFICIENT(새 거래일<20)**/FAIL/WEAK/WATCH/PAPER_CANDIDATE/RESEARCH_CONFIRMED, `live_trading_recommendation`·`real_order_allowed`=False·`dry_run_required`=True 항상. **실측(2026-06)**: **추가 forward 데이터 미존재** — KIS 시세 최신일 2026-05-22(미래 일자 조회도 동일), 기존 6개월이 이미 포함, 추가 수집 전부 overlap → **새 거래일 0** → **NEW_DATA_INSUFFICIENT**(rule_hash_match=True 룰 불변 확인). **EXE 재빌드 보류** — 60D-WEEKLY holdout 의 RESEARCH_PROMISING 은 기존 6개월 내부 결과이며 새 forward 확인 미달; 장이 더 진행돼 새 거래일 ≥20 쌓이면 동일 고정 룰(hash 일치)로 자동 재검증, 그때 PAPER_CANDIDATE 이상이면 dry-run(자동주문 OFF) EXE 검토. **Paper/Backtest only · EXE 빌드 0건 · 실전 금지** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건, rule hash 로 검증 중 파라미터 변경 0건, look-ahead 0건, 기존 6개월 CSV 보존, 안전 flag 변경 0건, 수익 보장 문구 0건. `NewDataReport.is_live_authorization`/`live_trading_recommendation`/`real_order_allowed`/`broker_order_sent`/`order_created`/`exe_build_executed`=False·`dry_run_required`/`do_not_auto_apply`/`no_profit_guarantee`=True·`auto_apply_allowed`=False 불변(dataclass 가드). 10개 backend + 8개 frontend 테스트. 자세한 절차: [`docs/locked_60d_weekly_new_data_validation.md`](docs/locked_60d_weekly_new_data_validation.md).

**KIS-INTRADAY-60D-WEEKLY-FIXED-REVALIDATION-01 — 60d/weekly 고정 룰 holdout 재검증 (read-only)**: FORWARD-UNIVERSE 의 grid 60d/weekly(+8.3%)가 사후최적화인지 확인 — 그 변형을 고정 룰 `FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1`(selector FORWARD_STABLE, lookback 60거래일, weekly, size10 primary·15/20 보조, RISK_VETO, composite, GAP/ORB/VWAP, daily_loss_stop −1.5%, costs 1.5/18/5bps, exit stop/target+EOD)로 **검증 전 잠그고**(변경 금지) holdout 검증. `app/system/locked_60d_weekly_validation.py` + `forward_universe_validation.run_universe_backtest` 에 `count_window`(holdout 기간만 계상, lookback 은 항상 point-in-time)·per-period PF·slippage passthrough 추가. 검증: original 6M replay + last-20D/last-40D(주 판정)/worst-month holdout + even/odd symbol split + slippage stress(5/7/10bps) + static·40d-monthly 비교 + RISK_VETO vs OFF + defense none vs daily + 보조 size. verdict 5단계 LOCKED_RULE_FAIL/WEAK/WATCH/PAPER_CANDIDATE/RESEARCH_PROMISING, `live_trading_recommendation` 항상 False. CLI `scripts/run_locked_60d_weekly_validation.py`(→ `reports/strategy_validation/locked_60d_weekly_*.{json,md}` + `*_latest.json`, gitignore) + endpoint `GET /api/system/locked-60d-weekly/latest` + UI `Locked60dWeeklyValidationCard`(AISignal 탭, 4상태, 새로고침·복사만). **실측(50종목·7개월, point-in-time)**: **LOCKED_RULE_RESEARCH_PROMISING** — original replay +8.34%/PF1.47(grid 재현 ✓), **last-40D holdout +8.77%/PF1.81/MDD1.38%/63거래**, last-20D +6.3%/PF2.27, **worst-month(3월) +1.0% 방어(baseline −23.2%)**, slippage 10bps +6.0%(robust), static +1.9%<40d/monthly +3.6%<60d/weekly +8.3%, RISK_VETO +8.3% vs OFF −3.0%, decay −0.43pp. **caveat**: 표본 작음(holdout 63거래<100 LOW_CONFIDENCE, 6개월 데이터), **symbol-split breadth 의존**(disjoint 절반 +1.5%/+2.3%로 급감 — +8.3%는 50종목 best10 선택 의존). 결론: 처음으로 *모의 리허설 후보* 도달 → EXE **재빌드 후 dry-run KIS 모의 리허설 가능(실전 금지, 추가기간 권장)**, 단 추가 기간 재검증 선행. **Paper/Backtest only · EXE 빌드 0건 · 실전 금지** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건, 검증 중 룰 변경 0건, look-ahead 0건, 안전 flag 변경 0건, 수익 보장 문구 0건. `Locked60dReport.is_live_authorization`/`live_trading_recommendation`/`broker_order_sent`/`order_created`/`exe_build_executed`=False·`do_not_auto_apply`/`no_profit_guarantee`/`rule_locked_before_validation`/`no_look_ahead`=True·`auto_apply_allowed`=False 불변(dataclass 가드). 13개 backend + 8개 frontend 테스트. 자세한 절차: [`docs/locked_60d_weekly_validation.md`](docs/locked_60d_weekly_validation.md).

**KIS-INTRADAY-FORWARD-UNIVERSE-REBUILD-01 — point-in-time universe 선별 검증 (read-only)**: FORWARD-VALIDATION 의 FORWARD_WEAK 원인(universe 를 6개월 전체 in-sample 선택, symbol-split decay +10pp)을 제거 — 각 rebalance 시점에서 *이전 lookback 구간만* 으로 종목 점수 계산 후 다음 test 구간에 *그때 선정 종목만* 적용(look-ahead 금지). `app/system/forward_universe_selector.py`(lookback-only 합성 score 고정 가중치 `0.30 cost_adj_expectancy + 0.25 PF + 0.20 win_rate + 0.15 liquidity + 0.10 time_bucket_edge` − penalties, rebalance weekly/biweekly/monthly, 12 selector 변형) + `app/system/forward_universe_validation.py`(rolling rebalance chain·기간마다 universe 재선정 + selector variants + lookback·rebalance grid + Agent·손실방어 결합 + verdict, look-ahead selector STATIC_IN_SAMPLE_TOP10 은 참고용·후보 제외) + CLI `scripts/run_forward_universe_validation.py`(→ `reports/strategy_validation/forward_universe_*.{json,md}` + `*_latest.json`, gitignore) + endpoint `GET /api/system/forward-universe/latest` + UI `ForwardUniverseCard`(AISignal 탭, 4상태, 새로고침·복사만). verdict 4단계: `UNIVERSE_FAIL`(forward<0/거래<30) / `UNIVERSE_WEAK` / `UNIVERSE_WATCH`(forward≥2·MDD≤20·거래≥60→관찰용 EXE 가능, 자동매매·모의주문 비활성) / `UNIVERSE_PAPER_CANDIDATE`(forward≥5·MDD≤15·거래≥100·posR≥0.55→dry-run KIS 모의 리허설, 실전 금지). `live_trading_recommendation` 항상 False. **실측(50종목·7개월, point-in-time)**: **UNIVERSE_WATCH**(WEAK 대비 개선) — STATIC_ALL(선별없음) −1.3%(FAIL) vs **best FORWARD_STABLE_UNIVERSE +3.6%/MDD1.4%/101거래/posR0.75(WATCH)** → point-in-time 선별이 ~+4.9pp 추가(forward 에서도 유효); 참고 look-ahead in-sample top10 +10.3%(상한, 후보 제외); grid 60d+weekly +8.3%·40d+weekly +5.5%(lookback 길수록·rebalance 잦을수록 개선, 별도 고정 후 재검증 필요); Agent RISK_VETO +3.6%(최고)·OFF −1.1%(악화 일관). 결론: **종목 선별을 forward 로 재설계하니 static 대비 양(+) 회복(WATCH), 단 PAPER_CANDIDATE 미달 — 더 긴 lookback 후보를 고정해 재검증 후에만 관찰용 EXE.** **Paper/Backtest only · EXE 빌드 0건 · look-ahead universe 선택 후보 제외** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건(정적 grep), 안전 flag 변경 0건, 수익 보장 문구 0건. `ForwardUniverseReport.is_live_authorization`/`live_trading_recommendation`/`broker_order_sent`/`order_created`/`exe_build_executed`=False·`do_not_auto_apply`/`no_profit_guarantee`=True·`auto_apply_allowed`=False 불변(dataclass 가드). 15개 backend + 9개 frontend 테스트. 자세한 절차: [`docs/forward_universe_rebuild.md`](docs/forward_universe_rebuild.md).

**KIS-INTRADAY-FORWARD-VALIDATION-01 — 고정 룰 forward/OOS 검증 (read-only)**: DECOMPOSITION-01 개선 조합(+9%, PAPER_REHEARSAL_CANDIDATE)이 *동일 6개월 내부* 선택이라 in-sample 편향 가능 → 룰을 **검증 전 고정**(`rule_locked_before_validation=True`)하고 universe/ranking 을 *train 구간/종목에서만* 만들어 *test* 에 적용해 forward 생존 검증. `app/system/wf_6m_forward_validation.py`(고정 룰 A/B/C/D + 4 split(Monthly forward / Anchored forward / Rolling symbol split / Worst-month holdout) + Agent 5모드 forward 재검증(RISK_VETO+SIZER 결합 포함) + decay/overfit + verdict) + `wf_6m_sim_v2` 에 `by_symbol`(train universe 산출)·`apply_position_sizing`(veto+sizer 결합) 추가 + CLI `scripts/run_wf_6m_forward_validation.py`(→ `reports/strategy_validation/forward_validation_*.{json,md}` + `*_latest.json`, gitignore) + endpoint `GET /api/system/forward-validation/latest` + UI `ForwardValidationCard`(AISignal 탭, 4상태, 새로고침·복사만). forward verdict 4단계: `FORWARD_FAIL`(return<0/PF<1.05→EXE 보류) / `FORWARD_WEAK`(양(+)이나 약함→EXE 빌드 가능하나 자동매매·모의주문 비활성·관찰용 UI만) / `FORWARD_WATCH`(return≥2·PF≥1.10·MDD≤20·거래≥60→모의 리허설 준비, 자동주문 dry-run 우선) / `PAPER_REHEARSAL_CONFIRMED`(return≥5·PF≥1.15·MDD≤15·거래≥100·posR≥0.5·worst-month 방어·anchored 양(+)→KIS 모의매매 리허설 가능, 실전 금지). `live_trading_recommendation` 항상 False. **실측(50종목·7개월, train-only universe)**: **최종 FORWARD_WEAK** — in-sample +9% 가 forward 에서 +2.3%/PF1.08/MDD4.9%(WEAK)로 약화, D(Agent OFF) −21.6%(FAIL); **rolling symbol-split decay +10pp** → universe/ranking 이 train *종목* 과적합(in-sample 편향이 핵심); worst-month(3월) holdout 방어 성공(A −10% vs baseline −23%)이나 여전히 음(−); Agent forward 는 RISK_VETO 단독(+2.3%)이 최안정, OFF/REVIEW −2.9%, veto+sizer 결합 −2.6%(과도). 결론: **Paper 리허설 확대 아직 불가, universe 선택을 forward 로 재설계 필요.** **Paper/Backtest only · EXE 빌드 0건** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 0건(정적 grep), 안전 flag 변경 0건, 자동 적용/실전 전환 0건, 수익 보장 문구 0건, 기존 final result 보존. `ForwardReport.is_live_authorization`/`live_trading_recommendation`/`broker_order_sent`/`order_created`/`exe_build_executed`=False·`do_not_auto_apply`/`no_profit_guarantee`=True·`auto_apply_allowed`=False 불변(dataclass 가드). 13개 backend + 8개 frontend 테스트. 자세한 절차: [`docs/wf_6m_forward_validation.md`](docs/wf_6m_forward_validation.md).

**KIS-INTRADAY-50-6M-STRATEGY-AGENT-DECOMPOSITION-01 — 매매기법 vs Agent 분해 (read-only)**: baseline(−19%)에서 매매기법만의 성과 / Agent OFF 효과 / Agent 7역할 도움·방해 / 손실원인(신호·종목·5슬롯·비용·청산·Agent) / Paper 리허설 후보를 규명. `wf_6m_sim_v2` 확장(allowed_strategies 전략-only 필터, max_hold·trailing·time_stop 청산, 7 Agent 역할(OFF/ENTRY_SELECTOR/RISK_VETO_ONLY/POSITION_SIZER_ONLY/EXIT_ADVISOR_ONLY/REGIME_FILTER_ONLY/REVIEW_ONLY), grade veto, size_scale, daily·equity 손실 stop) + `app/system/wf_6m_strategy_agent_decomposition.py`(전 섹션 종합 + verdict + EXE 재빌드 권고) + CLI `scripts/run_wf_6m_strategy_agent_decomposition.py`(→ `reports/strategy_validation/strategy_agent_decomposition_*.{json,md}` + immutable `decomposition_baseline_snapshot.{json,md}` + `*_latest.json`, gitignore) + endpoint `GET /api/system/strategy-agent-decomposition/latest` + UI `StrategyAgentDecompositionCard`(AISignal 탭, 데이터없음/분석중/완료/실패 4상태, baseline vs Agent OFF·Agent 역할 ranking·매매기법 only 최고·verdict·EXE 재빌드 권고·Paper 후보, 새로고침·복사만). EXE 재빌드 권고(verdict 매핑): STILL_NOT_RECOMMENDED 이하 보류 / WATCHLIST_ONLY 빌드 가능하나 자동매매 비활성·관찰용만 / PAPER_REHEARSAL_CANDIDATE 이상 재빌드 후 모의 리허설 가능(실전 금지). **실측(50종목·120거래일)**: 매매기법 only 전부 손실(GAP −11%/ORB −25%/VWAP −33%/MOMENTUM −44%, 모든 조합 −24~−34%) → 전략만으로는 비용 후 생존 불가; Agent OFF −34% vs council −18% → **Agent 끄면 더 나쁨**, RISK_VETO_ONLY +31pp·POSITION_SIZER_ONLY +32pp(MDD 5.7%)로 Agent 위험기능이 최대 개선 레버(EXIT_ADVISOR trailing −80% 금지); 비용0 +19%(매도세 18bps 치명); council BUY 39,437건 중 676건(1.7%)만 체결; Paper 후보 `DEF_daily_loss_1.5`(+9.2%/PF1.21/MDD7.25%/259거래/OOS+)·`DEF_equity_dd_10`(+9.0%) → **PAPER_REHEARSAL_CANDIDATE**(universe in-sample 캐비엇, forward 검증 별도 필요). **Paper/Backtest only · EXE 빌드 0건** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건, tauri/cargo build 명령 0건(정적 grep), 안전 flag 변경 0건, 자동 적용/실전 전환 0건, 수익 보장 문구 0건, 기존 final result 보존. `DecompositionReport`/`SimV2Result.is_live_authorization`/`broker_order_sent`/`order_created`/`exe_build_executed`=False·`do_not_auto_apply`/`no_profit_guarantee`=True·`auto_apply_allowed`=False 불변(dataclass 가드). 12개 backend + 7개 frontend 테스트(엔진 확장 회귀 포함 50 backend). 자세한 절차: [`docs/wf_6m_strategy_agent_decomposition.md`](docs/wf_6m_strategy_agent_decomposition.md).

**KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — 손실 원인분해 + 재설계 실험 (read-only)**: WF-6M-50 baseline(−19%, NOT_RECOMMENDED)의 "왜 개별 전략 forward-return PF 는 양(+)인데 포트폴리오는 음(-)인가"를 원인분해하고 원인별 해결 실험을 구현. 신규: `app/system/wf_6m_signal_extract.py`(per-bar council+4전략 vote+EOD fwd-return 1회 계산 후 *캐시* gitignored — 모든 실험 재사용) + `app/system/wf_6m_sim_v2.py`(실험용 per-timestamp 시뮬: universe 필터/신호 선택 랭킹 8종/비용 민감도/진입 시간 컷오프·강제청산/Agent 역할 6종(진입권 회수)/장세 필터/worst-month 방어; **장세 필터는 *전일* 시장수익만 사용 — look-ahead 방지**; signals 주입 가능→테스트) + `app/system/wf_6m_root_cause_analysis.py`(A~H: 신호품질/비용민감도/시간대/보유시간/종목군/전략별/Agent손상/walk-forward·worst-month + 손실원인 TOP) + `app/system/wf_6m_rebuild_experiments.py`(실험 매트릭스 + verdict). verdict 5단계: `BLOCKED`/`STILL_NOT_RECOMMENDED`(PF<1.05·MDD>20·return<0)/`WATCHLIST_ONLY`/`PAPER_REHEARSAL_CANDIDATE`(PF≥1.15·MDD≤15·return≥5·OOS+·연속손실≤12·거래≥100)/`RESEARCH_PROMISING`(PF≥1.25·MDD≤12·return≥8·OOS+·worst-month 방어·Agent hurt 감소); 거래<100 LOW_CONFIDENCE, MDD>20 자동 NOT_RECOMMENDED. CLI `scripts/run_wf_6m_root_cause_analysis.py` + `scripts/run_wf_6m_rebuild_experiments.py` (→ `reports/strategy_validation/wf_6m_root_cause_*.{json,md}` + `wf_6m_rebuild_*.{json,md}` + `*_latest.json`, gitignore). endpoint `GET /api/system/wf-6m-root-cause/latest` + `GET /api/system/wf-6m-rebuild/latest` + UI `Wf6mRootCauseCard`/`Wf6mRebuildExperimentsCard`(AISignal 탭, 데이터없음/분석중/완료/실패 4상태, 새로고침·복사만 — 주문/실전/적용/자동매매 시작 버튼 0개·input/textarea 0개, "연구/백테스트 결과이며 실전매매 권고가 아닙니다" 경고). **실측(50종목·120거래일)**: baseline −18%, 비용0 +19%(비용이 엣지 잠식), EXCLUDE 제거 +26%p(단 in-sample), AGENT_OFF −13%(Agent 제거가 더 나쁨→veto 역할 유지 타당), 장세필터(전일) −0.7%(같은날 종가 쓰면 +35%지만 look-ahead 착시), 최고 현실 조합 UNI_go_tune_top10 +13.8%/PF1.25/MDD11% → **최종 WATCHLIST_ONLY**(OOS 미확인). **Paper/Backtest only** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import·호출 0건(정적 grep), 안전 flag 변경 0건, 자동 적용/실전 전환 0건, 수익 보장 문구 0건, 기존 final result 데이터 보존. `RootCauseReport`/`RebuildReport`/`SimV2Result.is_live_authorization`/`broker_order_sent`/`order_created`=False·`do_not_auto_apply`/`no_profit_guarantee`=True 불변(dataclass 가드). 20개 backend + 13개 frontend 테스트. 자세한 절차: [`docs/wf_6m_root_cause_rebuild.md`](docs/wf_6m_root_cause_rebuild.md).

**WF-6M-50SYMBOLS-01 — 6개월·50종목·1000만원 전략 종합 검증 + 업그레이드 방향 (read-only)**: "내 전략(ORB/VWAP/Momentum/Gap + Agent Council)이 1000만원 기준 의미 있는가 / 실전 가능성 / 업그레이드 방향" 을 6개월(≥120거래일)·50종목·실제 KIS 5분봉으로 검증. 신규 `app/backtest/portfolio_capital_sim.py::run_portfolio_capital_sim` — **공유 자본 포트폴리오 자금곡선 시뮬레이터**(초기 10,000,000 KRW, 동시 보유 ≤5, 종목당 1~2백만원, 현금 부족/중복 진입 차단, 위탁수수료+증권거래세(매도)+슬리피지 반영, **오버나이트 없음·장마감 강제청산**; Agent Council BUY 진입 + exit_plan(stop/target)+EOD 청산; `signals` 주입 시 council 재계산 생략→테스트 가능; 하루/주/월 수익·MDD·자금곡선·승률·손익비·평균보유·최악연속손실·최악하루·종목별 net_pnl 출력). `app/system/wf_6m_50symbols_report.py::build_wf_6m_report` — 포트폴리오 시뮬 + 종목별 검증(`intraday_strategy_validation`) + 집계 전략 분해(`strategy_council_backtest`) 종합 → 종목 등급화(GO/WATCH/TUNE/EXCLUDE) + 전략 생존/사망 + 장세·시간대 버킷 + 실전 가능성(`RESEARCH_ONLY`/`WORTH_MORE_RESEARCH`/`PAPER_REHEARSAL_WORTHY`/`NOT_RECOMMENDED`) + 강점·약점 TOP3 + Agent 최적 역할 + 업그레이드 방향 + 확장성 평가(키움/미국/선물/코인/멀티브로커). CLI `scripts/run_wf_6m_50symbols_report.py`(→ `reports/strategy_validation/wf_6m_50symbols_final_result.{md,json}` gitignore) + endpoint `GET /api/system/wf-6m-50symbols/latest`(latest, 없으면 empty fallback, 무거운 시뮬 미실행) + UI `Wf6m50SymbolsCard`(AISignal 탭, 새로고침·복사만, 매수/매도/실전/자동적용/승인 버튼 0개·input/textarea 0개). PAPER_REHEARSAL_WORTHY 는 6개월 history + 비용 후 양(+) + PF≥1.3 + WF≥40 + Agent 도움 모두 충족 시만(그래도 실전 아님). **Paper/Backtest only · 실주문/실체결 0건** — broker/OrderExecutor/route_order/KIS 주문 API/httpx/requests import 0건(정적 grep 가드), 안전 flag 변경 0건, 자동 적용/실전 전환/threshold 자동 반영 0건, 수익 보장 문구 0건, 결과가 나빠도 그대로 보고. `PortfolioSimResult`/`Wf6mReport.is_live_authorization`/`broker_order_sent`/`order_created`=False·`do_not_auto_apply`/`no_profit_guarantee`=True 불변(dataclass 가드). 18개 backend 테스트(합성 bar+주입 신호, 수집 데이터 비의존) + 12개 frontend 테스트. 자세한 절차: [`docs/wf_6m_50symbols_validation.md`](docs/wf_6m_50symbols_validation.md).

**INSTALL-UX-FIX-01 — 설치본 화면 혼란 요소 정리 (read-only)**: 설치된 EXE 에서 *소스 환경 전용* 점검이 FAIL/BUILD_BLOCKED 로 잘못 보이지 않도록 런타임 인지 추가. **실제 안전 점검(안전 플래그·live 주문 차단·security·broker 호출)은 컨텍스트 무관하게 그대로 FAIL 유지.** 신규 `app/core/runtime_context.py::detect_app_runtime`(SOURCE_DEV / PACKAGED_RUNTIME / CI_BUILD — env AUTOTRADE_RUNTIME > PyInstaller frozen/build_stamp > CI env > 소스). (1) `final_prebuild_gate.GateInputs.app_runtime`: packaged/CI 에서 EXE_BUILD_INPUTS·DOCS_RUNBOOK 누락 → SKIP(참고용), SOURCE_DEV 에서만 FAIL/WARN. (2) `premarket_readiness_gate.PremarketInputs.app_runtime`: `_sec_docs`/`_sec_report_scripts` 가 packaged/CI 에서 docs/scripts 미번들 → SKIP. 두 endpoint 가 `detect_app_runtime()` 주입. (3) `FinalPrebuildGateCard` 가 source-전용 SKIP 에 "설치본 참고용, 빌드 실패 아님" 안내. (4) `IntradayStrategyValidationCard`: `BLOCKED_BY_DATA` → INFO "실제 분봉 데이터 없음 — 전략 검증 미실행(설치 오류 아님)" + 기존 `MarketClosedNotice` 마운트(장 닫힘 09:05~14:50 안내). (5) `AutoPaperLoopCard`: Paper 현금 null → "아직 기록 없음(PAPER_SIMULATED)", cycle 0 → "아직 Bot 실행 기록 없음(자산 0원 아님)", KIS Paper Auto OFF/dry-run/Fill Polling OFF 의미 설명("안전 기본값"). 안전 flag·`.env`·broker/OrderExecutor/route_order·KIS 주문 API 변경 0건, secret 노출 0건, 매수/매도/실전/승인 버튼 0개. packaged 여도 LIVE flag 등 실제 위반은 BUILD_BLOCKED 유지(테스트 lock). 14개 신규 backend 테스트 + 4개 frontend 테스트.

**#45 / 5-05 Live 전환 감사 로그 (append-only)**: 운영자가 언제·어떤 조건으로 실전 전환을 검토/거절/철회했는지 추적하는 append-only 감사 로그 — `app/governance/live_transition_audit.py` (`LiveTransitionAuditLog.record`, frozen `LiveTransitionAuditEntry`). operator/reason 필수 + created_at 자동 + action(STANDARD_ACTIONS: LIVE_REVIEW_REQUESTED/REJECTED/READY_RECORDED, LIVE_CANARY_REVIEW_REQUESTED/REJECTED, LIVE_APPROVAL_REVOKED, LIVE_AUDIT_NOTE_ADDED — "APPROVED" 미사용) + snapshot(risk_profile/symbol_whitelist/max_order_notional/daily_live_limit/capital_review/paper_gate_verdict/canary_gate_verdict/manual_approval) + previous_audit_id 연결. **append-only**: update/delete 메서드·API 0건(POST 추가 + GET 조회만, PUT/PATCH/DELETE 없음, frozen entry), 정정은 새 이벤트로. **secret/account 저장 0건**: agent_memory sanitize(fail-closed) + 보강 패턴(KIS 8-2 계좌/Bearer/token) 스캔 → 적중 시 `LIVE_AUDIT_SECRET_BLOCKED` 기록 거부. **감사 로그는 주문 신호/실전 승인 아님** — `is_order_signal`/`is_live_authorization`/`broker_order_sent`/`order_created`/`auto_apply_allowed` 항상 False(dataclass `__post_init__` 가드), 기록해도 실전 주문 0건. API: `POST/GET /api/governance/live-transition-audit`(routes_governance, DB write 0건·append-only 메모리). broker/OrderExecutor/route_order/KIS live endpoint import 0건, 안전 flag 변경 0건. 정책: [`docs/live_transition_audit_log.md`](docs/live_transition_audit_log.md), 테스트: `test_live_transition_audit_log.py`(21) + `test_live_transition_audit_policy.py`(10).

**CONNECT-KIS-REALTIME-PRICE-TO-PAPER-AUTO-LOOP-V2 — KIS 실시간 시세 ↔ Paper Auto Loop (mock 주문 차단)**: AutoPaperLoop 가 mock 합성가가 아니라 *실시간 KIS 시세* 로 여러 종목을 감시하고 조건 충족 종목에 한해 리스크 한도 내에서 KIS *모의*주문을 자동 전송하도록 연결. 운용 모드를 4종으로 분리(`app/auto_paper/paper_auto_mode.py::resolve_paper_auto_mode`): `VIRTUAL_ONLY`(mock/advisory, broker 호출 0) / `KIS_REALTIME_DRYRUN`(KIS 실시세 판단만, broker_order_sent=false) / `KIS_REALTIME_PAPER_AUTO`(조건 충족 시 KIS 모의주문 자동 전송, 여러 종목) / `KIS_REALTIME_SMOKE_TEST`(PAPER_AUTO 의 하위 제한 모드 — 단일 종목/1주/1건). 신규 read-only 시세 어댑터 `app/market_data/kis_realtime.py`(`fetch_realtime_quote`/`build_kis_market_input`) 는 KIS read-only 시세(inquire-price[FHKST01010100] + 분봉[FHKST03010230])만 사용 — **주문 API import/호출 0건**(정적 grep: `.place_order(`/`route_order(`/`from app.execution`/`OrderExecutor` 0건), **mock silent fallback 금지**(실패 시 `KIS_MARKET_DATA_UNAVAILABLE`/`KIS_PRICE_STALE`/`KIS_PRICE_INVALID` → HOLD/skip). `price_source="kis"` 영구. **핵심 안전 가드**: `auto_permission` 에 `price_source`/`price_is_stale` 입력 + `KIS_REALTIME_PRICE_REQUIRED`/`KIS_PRICE_STALE` reason 추가 — 실제 전송(not dry_run)은 `price_source=="kis"` 에서만 허용(mock 시세로 KIS 모의주문 전송 차단). `driver_bridge.kis_paper_realtime_scan_tick` 가 universe 를 순회하며 종목별 KIS 실시세→Agent Council 판단→BUY/SELL 후보를 `per_symbol_notional`/`max_concurrent_positions`/`daily_buy_limit`/`max_new_positions_per_tick`/중복 가드 적용 후 `execute_kis_paper_auto_order`(기존 sanctioned route_order→RiskManager→PermissionGate→OrderExecutor→`KisBrokerAdapter.place_order(is_paper=True)`) 로 위임 — broker 직접 호출 0건. mock 단일 경로 결정은 `price_source="mock"` 태깅으로 전송 차단. 신규 settings: `kis_paper_max_concurrent_positions`(5)/`kis_paper_per_symbol_notional_krw`(1,000,000)/`kis_paper_daily_buy_limit_krw`(3,000,000)/`kis_paper_max_new_positions_per_tick`(1)/`kis_paper_scan_max_symbols`(10)/`kis_paper_smoke_mode`(false)/`kis_paper_smoke_symbol`/`kis_paper_smoke_qty`. `GET /api/auto-paper/run-readiness` 가 `paper_auto_mode`/`paper_auto_limits` carry, UI `AutoPaperLoopCard` 가 4모드 배지 + smoke/정상 구분 + price_source + mock 주문금지 안내(매수/매도/실거래 버튼 0). 불변: `ENABLE_LIVE_TRADING=false`/`KIS_IS_PAPER=true`/`broker_order_type=KIS_PAPER`/`is_live_authorization=False` 유지, 실전 주문 path/실전 TR 0. 47개 신규 테스트(paper_auto_mode 7 + kis_market_data 12 + realtime_paper_auto 8 + frontend 4 + 회귀 201 무회귀).

---

## 블록 2 — Governance Gate(#72~#96) · Desktop/EXE/Installer · System Audit/Hygiene

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

**#76 Futures Promotion Policy**: 선물 기능은 자동매매 전체에서 *가장 마지막* 단계 —
7단계 승격 정책(`FUTURES_DISABLED` → `SIMULATION` → `SHADOW` → `PAPER` →
`MANUAL_APPROVAL` → `AI_ASSIST` → `AI_EXECUTION_BLOCKED`)을
[`docs/futures_promotion_policy.md`](docs/futures_promotion_policy.md)에 정의.
주식 MVP / Paper / Shadow / Live Manual / AI Assist가 안정화되기 *전*에는 선물
실거래 진행 금지. 선물은 레버리지 + 강제청산 + 만기 + 24시간 거래로 위험 한 등급
높음. **`FUTURES_AI_EXECUTION`은 본 프로젝트가 *영구 BLOCKED*** —
`AIExecutionActivationGateResult.futures_allowed=False` 불변 (#75)으로 코드 단
강제. `FuturesRolloverPlan`은 advisory 객체일 뿐 broker 호출 트리거 0건 (#49),
만기일 근처 AI 자동매매는 *어떤 단계에서도 금지*. 본 PR 시점에 `ENABLE_FUTURES_LIVE_TRADING`
default false 유지, `FuturesRiskManager.evaluate_order` LIVE 분기 항상 REJECTED,
실제 선물 broker adapter 코드 0개.

**#77 Alpha Decay Monitor**: 전략별 알파 감쇠 read-only 분석 —
`app/governance/alpha_decay.py::evaluate_alpha_decay`. baseline (검증 단계
통과 시점) vs recent (최근 운용) 의 6개 핵심 지표(`expectancy` / `profit_factor` /
`win_rate` / `max_drawdown` / `max_consecutive_losses` / `data_quality_score`)와
market regime 변경을 가중치 누적해 0~100 score 산정 + 4단계 status
(`HEALTHY` / `WATCH` / `DECAY_WARNING` / `DISABLE_CANDIDATE`) + `INSUFFICIENT_DATA`
별도. 6종 `AlphaDecayKind` 로 *단기 부진* (SHORT_TERM_DRAWDOWN, REGIME_MISMATCH)
과 *구조적 성능저하* (STRUCTURAL_DECAY ≥3 지표 동시 악화) 를 구분.
API: `POST /api/governance/alpha-decay/evaluate`. UI: `AlphaDecayCard`.
**전략 자동 비활성 / 삭제 / promotion 변경 절대 금지** —
`AlphaDecayResult.auto_disable=False` / `auto_apply_allowed=False` /
`is_order_signal=False` 불변 (dataclass `__post_init__` ValueError 가드).
DISABLE_CANDIDATE 라벨은 *비활성 후보 표시*일 뿐, 실제 전략 변경은 운영자
수동 승인 + 별도 PR 필요 (Strategy Researcher #55 분석 + Promotion Gate #27
재진입). 본 모듈은 broker / OrderExecutor / route_order / paper_trader /
`app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` /
`requests` / `app.core.config.get_settings` import 0건, DB write 0건,
`.save_params(` / `.apply_params(` / `strategy.enabled = False` /
`PromotionGate(` / `evaluate_promotion(` 호출 0건, `settings.enable_*_trading =`
mutate 0건 (정적 grep 가드), UI에 "전략 비활성화" / "전략 삭제" / "파라미터
적용" / "promotion 변경" / "Place Order" 라벨 버튼 0개 (frontend 테스트로 lock).
자세한 정책: [`docs/alpha_decay_monitor.md`](docs/alpha_decay_monitor.md).

**#78 Correlation Guard**: sector / theme 익스포저 사전 검사 —
`app/risk/correlation_guard.py::CorrelationGuardRule`. **신규 BUY 집중도만
제한** — SELL/EXIT 은 *리스크 축소* 목적이므로 본 가드가 차단하지 않는다
(`SKIP_NON_BUY` invariant, 테스트로 lock). 4 verdict (`PASS` / `WARN` /
`REJECT` / `SKIP_NON_BUY`). 임계: `max_symbols_per_sector` /
`max_sector_exposure` (KRW + equity %) / `max_symbols_per_theme` /
`max_theme_exposure` (KRW + equity %) — 0/빈값은 비활성. `warn_ratio` 기본
0.8 — REJECT 임계의 80% 이상이면 WARN. 같은 심볼 재매수는 종목 수 카운트
증가 X (노출은 누적). `compute_return_correlation` / `returns_from_closes`
helper로 후속 PR에서 MarketBar 기반 수익률 상관계수 확장 가능 — 표본 부족 시
None 반환 → 검사 skip (데이터 부족을 보수적으로 차단하지 않음).
API: `POST /api/risk/correlation-guard/preview` (read-only). UI:
`CorrelationGuardCard`. **RiskManager의 *하위 pre-trade guard*로만 동작** —
broker / OrderExecutor / route_order 우회 0건, RiskManager / PermissionGate
흐름 대체 X. 본 모듈은 broker / OrderExecutor / route_order / paper_trader /
`app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` /
`requests` / `app.core.config.get_settings` import 0건, DB write 0건,
settings mutate 0건 (정적 grep 가드). `CorrelationGuardResult.is_order_signal=False` /
`auto_apply_allowed=False` 불변 (dataclass `__post_init__` ValueError 가드),
UI에 "주문 실행" / "정책 적용" / "ENABLE_*" 라벨 버튼 0개 (frontend 테스트로
lock). 자세한 정책: [`docs/correlation_guard_policy.md`](docs/correlation_guard_policy.md).

**#81 Strategy Registry beginner metadata**: 코드의 6개 strategy_id
(`sma_crossover` / `rsi_reversion` / `vwap_strategy` / `orb_vwap` /
`volume_breakout` / `pullback_rebreak`) 위에 *얇은 메타 레이어* —
`app/strategies/registry_metadata.py`. displayName (한글) / beginnerName /
description / risk_level (low/medium/high) / recommended_mode
(paper_recommended / live_after_validation / live_caution) / supported_modes
/ backtest_available / paper_trading_available /
**live_trading_available=False 영구** (KIS live 미구현). API:
`GET /api/strategies/beginner-registry` (기존 `/registry` 와 *별도*, 호환
유지). UI: `StrategyRegistryCard` — displayName + `(internal_id)` *항상
함께 노출* (운영자가 로그/audit 매핑 가능). **기존 매매 로직 0줄 변경**,
**가짜 전략명 추가 영구 금지** (`골든브릿지` / `트라이앵글 전설` / `다이아
전략` / `퀀텀 점프` / `황금알` / `100% 승률` / `guaranteed` /
`magic strategy` 등 정적 grep 가드). `validate_metadata()` 가
STRATEGY_REGISTRY 와 1:1 불일치를 즉시 검출. 본 모듈은 broker / OrderExecutor /
route_order / paper_trader / 외부 HTTP / AI SDK import 0건, DB write 0건,
`STRATEGY_REGISTRY[...] =` mutation 0건, `settings.enable_*` mutate 0건.
UI 카드에 "전략 활성화 / 비활성화 / Apply Parameters / 주문 실행 / ENABLE_*"
라벨 버튼 0개 — 운영은 기존 BotControl / LiveEngine 흐름에서. 자세한 정책:
[`docs/strategy_registry.md`](docs/strategy_registry.md).

**#80 Pre-market Checklist**: 장 시작 전 자동 점검 —
`app/governance/pre_market_check.py::evaluate_pre_market_check`. 11 카테고리
(api / db / broker / data / watchlist / strategy / risk / kill_switch /
agent / notification / governance) 점검 + 모드별 required check. PASS /
WARN / FAIL / SKIP / UNKNOWN 상태. `start_allowed=False` 는 required FAIL 1건
이상 — **manual_ack=True 라도 우회 불가** (정책 + 테스트 lock). 3 verdict
(READY_TO_START / WARN_BUT_START_ALLOWED / DO_NOT_START). `strict=True` 모드는
UNKNOWN(required) 도 FAIL 취급. API: `GET/POST /api/governance/pre-market-check`,
CLI: `scripts/pre_market_check.py` (exit 0=allowed / 1=blocked / 2=error). UI:
`PreMarketCheckCard` — 모바일 헤드라인 (오늘 자동운용 가능 / 주의 필요 / 시작
금지) + "다시 점검" + "확인했습니다" 두 버튼만, **자동매매 시작 / mode 변경 /
flag 토글 / Place Order 라벨 버튼 0개** (frontend 테스트로 lock). 본 모듈은
broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
`app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` /
`app.core.config.get_settings` import 0건, DB write 0건,
`settings.enable_*_trading =` mutate 0건 (정적 grep 가드).
`PreMarketCheckResult.is_order_signal=False` / `live_flag_changed=False` /
`mode_changed=False` 불변 (dataclass `__post_init__` ValueError 가드). 본
게이트는 자동매매 시작 / mode 변경 / flag 토글을 *수행하지 않는다* — 결과만
반환하고 실제 시작은 BotControl 흐름에서 본 결과를 참조해 결정. 자세한 정책:
[`docs/pre_market_check_policy.md`](docs/pre_market_check_policy.md).

**#79 Loss Tagging**: 손실 거래 *추정* 원인 자동 태깅 —
`app/analytics/loss_tagging.py::estimate_loss_reasons` + `LossReasonLog` 테이블
(alembic 0022). **태그는 *추정값*이며 확정 원인이 아니다** —
`LossEstimateResult.is_estimated=True` / `is_order_signal=False` /
`is_investment_advice=False` 불변 (dataclass `__post_init__` ValueError 가드).
25 tag 7 카테고리(`strategy` / `market` / `execution` / `risk` / `data` /
`agent` / `unknown`) — primary 우선순위 risk > data > market > execution >
strategy > agent > unknown. API: `POST /api/analytics/loss-tags/estimate` /
`GET /summary` / `GET /recent` / `PATCH /{id}/review` — **DELETE 엔드포인트
0건** (정적 grep 가드), append + review only. 운영자 review 는 `review_*`
컬럼만 update — *원본 추정 데이터 변경 0건*. helper 3종(`summarize_for_daily_report`
/ `summarize_for_strategy_researcher` / `summarize_loss_reasons`) 가 DailyReport
/ StrategyResearcher / RiskAuditor / AgentMemory 에서 read-only carry 가능.
UI(`LossReasonCard`)는 "추정 원인 · 확정 원인 아님" 영구 배지 + "확정 원인이
아닙니다" disclaimer 영구 노출. **태그를 *주문 차단 / 실행 트리거*로 사용 금지** —
UI에 "강제 적용" / "자동 비활성" / "전략 비활성화" / "삭제" / "확정 원인" /
"주문 차단 적용" / "ENABLE_*" / "Place Order" 라벨 버튼 0개. 본 모듈은 broker /
OrderExecutor / route_order / paper_trader / `app.ai.assist` / `app.ai.client` /
`anthropic` / `openai` / `httpx` / `requests` import 0건, evaluator 는 DB write
0건, storage 는 `db.delete(` / `DELETE FROM` 0건. 자세한 정책:
[`docs/loss_tagging_policy.md`](docs/loss_tagging_policy.md).

**Desktop Release Workflow (GitHub Actions 자동 빌드)**: 로컬 PC 에 Rust /
WiX 가 설치되어 있지 않아도 EXE/MSI 를 자동 빌드할 수 있도록
`.github/workflows/desktop-release.yml` 정식 활성화. 10 step (Checkout →
Node/Python/Rust setup → Tauri CLI v2 → 빌드 전 safety guard (`.env.example`
default + workflow self-check regex) → repository hygiene + security_scan →
frontend test+build → backend sidecar PyInstaller → `cargo tauri build` (WiX/
NSIS 자동 다운로드) → 빌드 후 safety guard (`.env` / `*.pem` / `*.key` /
`*.p12` / `*.pfx` / `*.crt` / `*.cer` / `*.keystore` / `*.jks` bundle 내
포함 0건 검증) → workflow artifact + GitHub Release draft 업로드). 절대 원칙
(workflow + test 로 강제): **수동 trigger only** (`workflow_dispatch` 만, push
/ schedule / pull_request 자동 트리거 0건 — `test_desktop_release_workflow_uses_workflow_dispatch_only`),
**Windows runner 한정** (`runs-on: windows-latest` — `test_desktop_release_workflow_runs_on_windows`),
**LIVE/AI/FUTURES enable flag true 설정 0건** (`test_desktop_release_workflow_does_not_enable_live_flags`
+ workflow self-check regex), **KIS_IS_PAPER false 설정 0건** (동일), **secret /
API key 직접 임베드 0건** (`test_desktop_release_workflow_has_no_secret_strings`
— sk- / sk-ant- / ghp_ / xox / PST / 한국 계좌번호 패턴 검사), **artifact
path 에 .env / .pem / .key 등 포함 0건** (`test_desktop_release_workflow_artifact_path_excludes_secrets`),
**signing private key 는 GitHub Secrets 만** (코드 직접 임베드 0건). 5개
신규 hygiene test 추가 (34 → 39 PASS). 운영 로직 변경 0건, broker /
OrderExecutor / route_order 호출 0건, settings 안전 flag default mutate 0건.
실행: GitHub Actions 탭 → desktop-release → Run workflow (release_tag /
draft / create_release 입력). 자세한 정책: [`docs/desktop_exe_status.md`](docs/desktop_exe_status.md)
§8-C, [`docs/exe_oneclick_installation.md`](docs/exe_oneclick_installation.md) §3-1.

**#96 Loss Root Cause Tagging (결정/실행 단계 손실 원인 advisory)**: 손실 거래에
*"왜 잃었는가"* 의 근본원인을 16개 태그 × 5 카테고리로 *추정* 분류하는 advisory
모듈 — `app/analytics/loss_root_cause.py`. **#79 `loss_tagging.py`** (post-trade
25 tag × 7 cat) 와는 **별개 분석 레이어** — 본 모듈은 *결정 시점 / 실행 단계*
약점에 초점. 16 root cause tag: decision(`LATE_ENTRY` / `LATE_EXIT` /
`STALE_SIGNAL` / `AGENT_OVERRULED`) + risk(`HIGH_CORRELATION` /
`RISK_GATE_REJECTED`) + market(`HIGH_VOLATILITY` / `BAD_REGIME` / `NEWS_RISK`)
+ execution(`LOW_LIQUIDITY` / `SLIPPAGE` / `SPREAD_TOO_WIDE`) +
strategy(`STOP_LOSS_HIT` / `TIME_STOP_HIT` / `KIMP_CONVERGENCE_FAIL`) +
`UNKNOWN`. `KIMP_CONVERGENCE_FAIL` 은 *crypto-specific* — 본 프로젝트 1차 배포
미적용, 향후 crypto 확장 시 활성화. primary tag 선정 우선순위: 카테고리
priority (risk > decision > market > execution > strategy > unknown) × severity
(HIGH > MEDIUM > LOW). #94 `STALE_SIGNAL` 과 #95 `HIGH_CORRELATION` 직접 연동
(`signal_age_minutes_at_entry` / `portfolio_max_correlation` 입력). 집계 함수
`summarize_root_causes()` 로 N개 손실 거래 → frequency / by_strategy / top_tags /
high_severity_tags 통계. API: `POST /api/analytics/loss-root-cause/{evaluate,summarize}`
read-only. UI: `LossRootCauseCard.jsx` (기존 `LossReasonCard.jsx` 와 별개 파일)
— primary tag 배지 + 5 invariant 영구 배지 ("추정 태그 · 확정 아님" / "주문
신호 아님" / "자동 적용 안 함" / "투자 조언 아님" / "분석 전용 · 주문 기능 아님")
+ 단일 거래 detail (multi-cause tag + rationale + improvement_advice) + 집계
요약 (top tags / high severity / by_strategy 분포 토글). **본 결과는 *추정값*
이며 *확정 원인이 아님*** — `LossRootCauseResult.is_estimated=True` 영구 +
`is_order_signal=False` / `auto_apply_allowed=False` / `is_investment_advice=False`
불변 (dataclass `__post_init__` ValueError). broker / OrderExecutor / route_order /
paper_trader / `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` /
`httpx` / `requests` / `app.core.config.get_settings` import 0건 (정적 grep
가드), DB write 0건, settings 안전 flag mutate 0건, frontend `input` /
`textarea` 0개, "지금 매수" / "지금 매도" / "Place Order" / "실거래 활성화" /
"ENABLE_LIVE_TRADING 토글" / "BUY/SELL/HOLD signal" 라벨 button 0개. 본 PR 은
**실거래 실행 기능을 추가하지 않으며**, 태그는 advisory — RiskManager /
OrderGuard 자동 차단 트리거로 사용하지 않음. AI Agent prompt context 에 carry
받아 *선택적 학습* 자료로만 사용. 49개 신규 backend 테스트 + 14개 신규 frontend
테스트 PASS. 자세한 정책: [`docs/loss_tagging.md`](docs/loss_tagging.md).

**#95 Portfolio Correlation Guard (포트폴리오 수익률 상관관계 advisory)**:
현재 보유 포지션 + 신규 진입 후보 종목 간의 *Pearson 상관계수 매트릭스*를
계산해 동일 시장 리스크 과노출을 advisory 검사 —
`app/risk/portfolio_correlation_guard.py`. **#78 `correlation_guard.py`**
(*sector/theme 노출 cap*) 와는 **완전히 다른 개념** — 본 모듈은 종목 간
*historical return correlation* 매트릭스 분석. #78 의 helper 함수
`compute_return_correlation` / `returns_from_closes` 를 *재사용* 한다.
verdict 5단계 (`HEALTHY` / `WATCH` / `WARN` / `BLOCK` / `INSUFFICIENT_DATA`)
+ pair severity 4단계 (`LOW` / `MEDIUM` / `HIGH` / `EXTREME`). default
thresholds: `warn_threshold=0.50` / `caution_threshold=0.70` /
`block_threshold=0.85` / `min_bars=20`. 본 가드는 **|corr| 절댓값 기준** —
음의 상관관계 (-0.85 등) 도 *반대 방향의 강한 결합* 이므로 동일하게 advisory
발생. 후보 vs 기존 max |corr| 추적 (`candidate_max_correlation`) 으로 신규
종목 진입 시 즉시 위험도 평가. strict 모드: WARN 도 `new_entry_allowed=False`
로 격하. 데이터 부족 시 INSUFFICIENT_DATA (차단 안 함 — *advisory 미적용*).
**적용 범위**: asset-class agnostic — 본 프로젝트 1차 배포는 국내주식 단타,
crypto 는 후속 PR (모듈 재사용 가능). API: `POST /api/risk/portfolio-correlation/evaluate`
read-only — return_series 또는 close_series 둘 중 하나 입력. UI:
`PortfolioCorrelationGuardCard.jsx` (기존 `CorrelationGuardCard.jsx` 와 별개
파일) — verdict 헤드라인 + BLOCK 차단 배너 ("상관관계 과다로 신규 진입 주의")
+ 4 invariant 영구 배지 + 쌍 정렬 표 (severity 순) + 후보 max |corr| 표시.
절대 invariant (테스트로 lock): `is_order_signal=False` /
`auto_apply_allowed=False` / `is_live_authorization=False` 불변 (dataclass
`__post_init__` ValueError), broker / OrderExecutor / route_order /
paper_trader / `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` /
`httpx` / `requests` / `app.core.config.get_settings` import 0건 (정적 grep
가드), DB write 0건, settings 안전 flag mutate 0건, frontend `input` /
`textarea` 0개, "지금 매수" / "지금 매도" / "Place Order" / "실거래 활성화" /
"ENABLE_LIVE_TRADING 토글" / "BUY/SELL/HOLD signal" 라벨 button 0개. 본 PR
은 **실거래 실행 기능을 추가하지 않으며**, BLOCK verdict 도 *권고* 수준 —
실제 차단은 별도 RiskRule (후속 PR + 운영자 명시 옵트인) 에서 처리.
RiskManager / OrderGuard 우회 0건. 35개 신규 backend 테스트 + 19개 신규
frontend 테스트 PASS. 자세한 정책: [`docs/correlation_guard.md`](docs/correlation_guard.md).

**#94 Signal Alpha Decay (신호 단위 신선도 advisory)**: *개별 신호* 의 시간
경과 후 기대수익 감쇠를 분석하는 advisory 모듈 — `app/analytics/signal_alpha_decay.py`.
**#77 governance/alpha_decay** (*전략 단위* 일/주 baseline vs recent) 와는
**완전히 다른 개념** — 본 모듈은 진입 신호가 t=0 에서 1분 / 3분 / 5분 / 10분 /
30분 / 60분 후 어떻게 감쇠하는지 bucket 단위로 측정. verdict 5단계
(`FRESH` / `DECAYING` / `STALE` / `EXPIRED` / `UNKNOWN`) + `decay_score`
(0~100, t=0 대비 평균 % clamp). default thresholds: `max_actionable_age_minutes=30`
/ `decay_warn_pct=70.0` / `decay_fail_pct=30.0` / `min_sample_count=10` /
verdict 시간 임계 (FRESH ≤ 1m / DECAYING ≤ 30m / STALE ≤ 60m / EXPIRED > 60m).
realtime helpers (`compute_signal_age_minutes` / `freshness_verdict_for_age` /
`is_signal_actionable(strict=False|True)`) — `strict=False` 는 EXPIRED 만 차단,
`strict=True` 는 STALE 도 차단. **EXPIRED 신호는 신규 진입 근거로 사용 금지** —
AI Agent prompt context 에 verdict carry 권장 (후속 PR), UI 카드는
"이 신호는 오래되어 진입 근거로 사용 금지" 차단 배너 노출. API: `POST /api/analytics/alpha-decay/evaluate`
+ `GET /api/analytics/alpha-decay/freshness?age_minutes=N` read-only. UI:
`SignalAlphaDecayCard.jsx` (기존 `AlphaDecayCard.jsx` 와 별개 파일) — verdict
헤드라인 + 4 invariant 영구 배지 ("주문 신호 아님" / "자동 적용 안 함" /
"실거래 허가 아님" / "advisory 분석") + EXPIRED 차단 배너 + bucket 표 토글 +
실시간 freshness 표시 (currentAgeMinutes prop). 절대 invariant (테스트로
lock): `is_order_signal=False` / `auto_apply_allowed=False` /
`is_live_authorization=False` (dataclass `__post_init__` ValueError), broker /
OrderExecutor / route_order / paper_trader / `app.ai.assist` / `app.ai.client`
/ `anthropic` / `openai` / `httpx` / `requests` / `app.core.config.get_settings`
import 0건 (정적 grep 가드), DB write 0건, settings 안전 flag mutate 0건,
frontend `input` / `textarea` 0개, "지금 매수" / "지금 매도" / "Place Order" /
"실거래 활성화" / "ENABLE_LIVE_TRADING 토글" / "BUY/SELL/HOLD signal" 라벨
button 0개. 본 PR 은 실거래 실행 기능을 추가하지 *않으며*, RiskManager /
OrderGuard 우회 없음 — 본 카드는 *판단 보조*만 제공. 43개 신규 backend
테스트 + 18개 신규 frontend 테스트 PASS. 자세한 정책: [`docs/alpha_decay.md`](docs/alpha_decay.md).

**#93 Security Scan 보강 (secret / 인증서 / 번들 누출 차단)**: secret 탐지 +
인증서/키 파일 + EXE/MSI/sidecar bundle artifact + `.env` 실제 파일 추적을
*read-only* 검출하는 통합 보안 스캐너. 신규 `scripts/security_scan.py` (744
tracked file 스캔 시 sub-second) — 20+ 패턴 (`openai_api_key` /
`anthropic_api_key` / `github_pat` / `slack_token` / `telegram_bot_token` /
`aws_access_key` / `gcp_api_key` / `kis_personal_secret_token` / `jwt_token` /
`bearer_long_token` / `korean_bank_account` / `credit_card` 등) + `.env` 의
실제 secret 값 / `ENABLE_LIVE_TRADING=true` / `ENABLE_AI_EXECUTION=true` /
`ENABLE_FUTURES_LIVE_TRADING=true` / `KIS_IS_PAPER=false` 값 측 매칭. 4단계
severity (`HIGH` / `MEDIUM` / `LOW` / `INFO`) + 마스킹된 snippet 출력 (head 4
+ "..." + tail 4 — 진짜 secret 도 출력 노출 0건). False positive 처리:
*디렉토리 단위 allowlist* (`backend/tests/**` / `docs/**` / `assets/**` /
`frontend/src/**/*.test.*`) + *라인 단위 ignore 마커* (`# security-scan: ignore`
또는 `// security-scan: ignore`) + `.env.example` placeholder 패턴 (빈 값 /
`여기에` / `your-` / `<...>`). `frontend/src/config/brokers.js` 의 UI
placeholder (8자리 + 2자리 계좌번호 예시) 2건은 line 단위 ignore 마커 추가 — 원본 파일 + bundled `assets/index-*.js` 모두 clean. 신규
`backend/tests/_fake_secrets.py` — 테스트 fake placeholder 단일 진실 (모든
값이 `FAKE-` / `PLACEHOLDER` / `0000` 마커 포함, `assert_all_placeholders_contain_fake_marker()`
self-check). `backend/tests/test_repository_hygiene.py` 보강 — 9개 신규 case
(`test_gitignore_blocks_certificate_and_key_files` / `_blocks_installer_and_bundle_artifacts`
/ `_blocks_pyinstaller_sidecar_outputs` / `_blocks_tauri_sidecar_binaries_keeps_readme`
/ `test_no_certificate_or_keystore_files_tracked` / `_no_installer_or_bundle_artifacts_tracked`
/ `_no_dotenv_file_tracked_only_examples` / `test_security_scan_script_exists_and_runs_clean`
/ `test_fake_secrets_module_has_clear_markers` / `test_no_real_kis_token_pattern_tracked`),
매 CI 실행마다 본 스캐너를 subprocess 로 호출해 finding 0건 검증 — 신규 secret
commit 시도 시 회귀 차단. 본 PR 시점 `git ls-files` 744 파일 스캔 결과 finding
0건 (initial false positive 4건은 brokers.js 의 UI placeholder 라인 마커 +
`assets/**` skip_glob 으로 제거). 본 모듈은 broker / OrderExecutor / route_order
/ DB / 외부 HTTP / AI SDK import 0건, read-only — 어떤 파일도 *수정하지
않는다*. 안전 flag (`KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false` /
`ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`) default 변경
0건, 출금 기능 추가 0건. 자세한 정책 / 패턴 카탈로그 / 확장 가이드:
[`docs/security_scan.md`](docs/security_scan.md).

**#92 Release Readiness Report (advisory meta-aggregator)**: 운영자가 "지금 새
릴리스 태그를 찍어도 되는가 / 다음 promotion 단계로 검토 가능한가" 를 판단할 수
있는 *단일 advisory 리포트* — `app/governance/release_readiness.py`. 기존
governance gates(#72/#73/#74/#75) + #80/#91 Pre-market + #77 Alpha Decay + #88
System Hygiene + #90 Desktop EXE 빌드 상태 + recent activity metrics 를
*read-only 로 carry* 받아 종합 verdict 산출. verdict 4단계(`READY_TO_TAG` /
`READY_WITH_CAVEATS` / `DO_NOT_TAG` / `INSUFFICIENT_DATA`). 10 카테고리
(`safety_flags` / `governance_gates` / `pre_market` / `strategy_health` /
`desktop_build` / `system_hygiene` / `documentation` / `data_freshness` /
`recent_activity` / `operator`) × 평균 2개 항목 = 약 21개 check item.
`release_kind` 3단계(`BETA` / `RC` / `STABLE`)별 required 매트릭스가 단계별로
강화 — `BETA` 는 안전 flag + pre-market + hygiene 최소, `RC` 추가 Paper Gate /
Strategy Health / 운영자 opt-in / desktop sidecar / test pass rate, `STABLE` 추가
Live Manual Gate / desktop installer / data freshness / emergency stop 한도.
**READY_TO_TAG 라벨은 *실거래 활성화 / 자동 promotion 이 아니다*** —
`ReleaseReadinessResult.is_live_authorization=False` /
`auto_apply_allowed=False` / `is_order_signal=False` / `live_flag_changed=False` /
`mode_changed=False` 불변(dataclass `__post_init__` ValueError 가드). 본 모듈은
다른 governance gate evaluator 를 *직접 호출하지 않는다* — 호출자가 각 gate
결과를 *라벨 / boolean 으로 요약*해서 전달 (정적 grep 가드로 lock). 본 모듈은
broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
`app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` /
`app.core.config.get_settings` import 0건 (정적 grep 가드), DB write 0건,
settings 안전 flag mutate 0건 — 운영자가 *현재 .env 상태* 를 input DTO 로 명시
입력해 실제값↔입력값 혼선 시 즉시 인지. `Secret` 원문 0건 — input DTO 에 API
key / 계좌번호 / Anthropic Key / Telegram Bot Token 필드 없음, `operator_note`
plaintext 만 max 500 chars 허용. API: `POST /api/governance/release-readiness/{evaluate,markdown}`
read-only. UI: `ReleaseReadinessCard` — verdict 헤드라인 + 4 invariant 영구 배지
("실거래 허가 아님" / "자동 .env 수정 안 함" / "release 자동 태깅 안 함" /
"주문 신호 아님") + 실패 / 경고 / 필요 조치 리스트 + 세부 항목 / markdown
미리보기 토글, secret 입력 form (input / textarea) 0개, "릴리스 자동 태깅" /
"git tag 자동 생성" / "GitHub Release publish" / "자동 promotion" / "실거래
활성화" / "Place Order" / "ENABLE_*" 라벨 button 0개 (frontend 테스트로 lock).
47개 신규 backend 테스트 + 19개 frontend 테스트 PASS. 자세한 정책:
[`docs/release_readiness_policy.md`](docs/release_readiness_policy.md).

**#91 Pre-market Checklist 확장 (Desktop EXE / KIS Paper one-click 흐름)**: 기존
#80 `evaluate_pre_market_check` 위에 *데스크톱 / KIS Paper 특화 점검 항목*을
추가한 확장 — 신규 모듈 생성 0건, 동일 `app/governance/pre_market_check.py` 에
필드 / 카테고리 / check item *얹기*. 신규 `CheckCategory` 2종(`DESKTOP` / `KIS_PAPER`,
기존 11 카테고리 유지). 신규 input 필드 7종(`desktop_mode` / `desktop_sidecar_connected`
/ `desktop_status_endpoint_ok` / `kis_paper_ready` / `kis_paper_can_run_mock` /
`kis_paper_can_run_kis` / `kis_paper_blocked_reasons`). 신규 result 필드 1종
(`kis_paper_test_allowed: bool` — `start_allowed=True` AND KIS Paper / Mock 중
하나 이상 가능할 때만 True, `desktop_mode=False` 면 항상 False). 신규 check
items 9종 — *초보자 안전 flag proactive checks 4종*(`kis_is_paper_safety` /
`enable_live_trading_safety` / `enable_ai_execution_safety` / `enable_futures_safety`,
SIM/PAPER/LIVE_SHADOW 한정으로 `.env` 의 안전 flag *비활성* 상태를 검증 — 기존 #80
의 LIVE invariant 와 *반대 방향*) + *desktop 2종*(`desktop_sidecar` /
`desktop_status_endpoint`, DESKTOP 카테고리, `desktop_mode=True` 한정) + *KIS Paper
3종*(`kis_paper_readiness` / `kis_paper_capability`, KIS_PAPER 카테고리, blocked
사유는 라벨만 carry — secret 원문 0건). Frontend `PreMarketCheckCard` 확장 —
desktop / kis_paper 카테고리 항목 포함 시 *KIS Paper test 활성화 게이트 배너*
(`pre-market-kis-paper-test-gate` testid) 표시, `DO_NOT_START` 시 *초보자 안내 블록*
(`pre-market-beginner-help` testid)에 `.env` 4개 flag 점검 가이드. `KisPaperOneClickTestCard`
옵션 prop `preMarketCheckResult` 추가 — 있을 때만 `start_allowed=false` 면 quick /
slow / mock 3개 시작 버튼 모두 disabled + 차단 배너 (backwards compat 유지). 절대
invariant: 본 카드 `input` / `textarea` 0개 (secret 입력 form 미허용), "실거래
시작" / "지금 매수" / "Place Order" / "ENABLE_*" 라벨 button 0개, `.env` 자동 수정
0건 (수정 안내만), `kis_paper_blocked_reasons` 는 라벨만 carry (KIS App Key / Secret
/ 계좌번호 / Anthropic Key 원문 0건). 본 모듈은 broker / OrderExecutor / route_order /
paper_trader / `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` /
`requests` / `app.core.config.get_settings` import 0건 유지 (정적 grep 가드), DB write
0건, `settings.enable_*_trading =` mutate 0건. 38개 신규 테스트 PASS(backend 22 +
frontend Card 10 + frontend KisPaper 6) + 기존 95개 회귀 0건(backend 41 + Card 10 +
KisPaper 19). 자세한 정책: [`docs/pre_market_check_policy.md`](docs/pre_market_check_policy.md)
§10-A, [`docs/pre_market_checklist.md`](docs/pre_market_checklist.md).

**#89 KIS Paper one-click test + EXE 상태 점검**: 한투 모의투자 API 를 사용한
*원클릭* AI 자동매매 모의 테스트 orchestration. 사용자는 "준비상태 확인" 후
*확인 모달* 을 거쳐 "한투 모의 빠른 점검 시작" / "한투 모의 느린 스트레스 시작"
/ "내부 Mock 고속 스트레스 시작" 버튼 중 하나만 누른다. 매수/매도 수동 버튼
0개 — AI 판단 결과는 *카운터* 로만 표시. 신규 모듈 `app/kis_paper/` 4종
(`__init__.py` / `readiness.py` / `engine.py` / `scoring.py`) + 신규 API
`/api/kis-paper/{readiness,start,stop,status,report}`. 14개 절대 invariant
정적 lock:
- 실계좌 주문 0건 (`KisBrokerAdapter.place_order(is_paper=False)` `NotImplementedError`)
- `KIS_IS_PAPER=false` / `ENABLE_LIVE_TRADING=true` / `ENABLE_AI_EXECUTION=true`
  변경 0건
- frontend 에 KIS key/secret/account 입력 form 0개 (테스트로 lock)
- readiness 응답에 secret 원문 0건 — `*_present: bool` 만 carry
- BLOCKED 모드에서 engine 진행 0건
- KIS 모드 예외 시 mock 으로 silent swap 0건
- `KisPaperReadiness.is_order_intent/is_order_signal=False` 불변
- `KisPaperScore.is_live_authorization=False` 불변 + 점수 문구에 "실거래
  가능"/"LIVE 시작"/"지금 매수"/"지금 매도"/"Place Order" 단어 0건
- engine 모듈이 broker / OrderExecutor / route_order 를 *runtime* import 0건
- UI 에 "지금 매수"/"실거래 시작"/"Place Order" 라벨 button 0개
- 확인 모달 통과 (`confirm=true`) 전에 backend `/start` 호출 0건
- API rate limit 적용 (≥3초 간격) + KIS rate limit hit 즉시 중단
- engine 의 default tick runner 는 *카운터 갱신만* — 실 broker 호출은
  운영자가 본인 PC 에서 wrapper 주입 시점에 활성화

UI: `KisPaperOneClickTestCard` (5개 버튼 + 안전 배지 + readiness / counters /
점수판 / 확인 모달, BUY/SELL/HOLD/Place Order/실거래 라벨 버튼 0개). 35
backend 신규 PASS + 15 frontend 신규 PASS. EXE 상태: 현재 src-tauri/target/
부재 + Rust 툴체인 미설치 → `cargo tauri build` 시도 불가 (`docs/desktop_exe_status.md`
에 기록), `scripts/start_kis_paper_test_windows.bat` + `.ps1` 가 backend
자동 실행 보조 (Secret 입력 0건, ENABLE_* 변경 0건). 자세한 정책:
[`docs/kis_paper_oneclick.md`](docs/kis_paper_oneclick.md),
[`docs/desktop_exe_status.md`](docs/desktop_exe_status.md).

**#88 System Hygiene (운영 로직 변경 0건)**: GitHub 원격 저장소 기준
보완사항 — `.gitignore` 정리 (`.venv-310/` / `backend/.venv-310/` 명시 추가
+ `src-tauri/target/` / `*.msi` 등 #86 desktop artifact), 6개 workflow YAML
검증 (모두 `python -c "yaml.safe_load"` 통과), `backend/requirements.txt` /
`backend/.env.example` / `.env.staging.example` 형식 점검 (각각 1줄 1패키지 /
1줄 1환경변수, 실 Secret 0건, LIVE flag 모두 false), README *실거래 허가
아님* 배너 + #84~#88 신규 문서 링크 (`strategy_signal_aggregator.md` /
`strategy_selection_agent.md` / `desktop_packaging.md` / `desktop_update_policy.md`
/ `beta_tester_install_guide.md` / `tailscale_smartphone_access.md` /
`first_run_setup_wizard.md` / `system_audit_2026_05.md` / `system_hygiene_report.md`
+ 4개 `status/*.md` + `dependency_policy.md`), `docs/final_completion_summary.md`
최상단에 *과거 스냅샷* 경고 + status/ 인덱스 추가 (기존 내용 보존). 새 정적
검사 `backend/tests/test_repository_hygiene.py` 24 PASS — `.gitignore` Secret
allowlist / `.venv-310/` ignore / `backups/*` ignore / requirements 1줄 구조
/ env example Secret 의심값 (`sk-`, `ghp_`, `xox`, Bearer, 계좌번호 형식)
0건 / workflow Secret echo 0건 / LIVE flag 활성화 패턴 0건 / README 핵심 문구
/ `sw.js` `/api` 캐시 금지 / 6개 docs 존재. **본 PR 변경 항목**: `.gitignore` /
8개 신규 docs (`docs/status/*.md` 4종 + `dependency_policy.md` +
`system_hygiene_report.md`) / README / `final_completion_summary.md` 헤더 /
`test_repository_hygiene.py`. **변경 0건**: `app/` 운영 로직 / broker /
OrderExecutor / `route_order` / Strategy / RiskManager / DB schema / Alembic
migrations / `.env*` 값 / 안전 flag default. 자세한 정책:
[`docs/system_hygiene_report.md`](docs/system_hygiene_report.md),
[`docs/dependency_policy.md`](docs/dependency_policy.md),
[`docs/status/current_state.md`](docs/status/current_state.md),
[`docs/status/known_risks.md`](docs/status/known_risks.md),
[`docs/status/next_steps.md`](docs/status/next_steps.md).

**#87 System Audit 2026-05 (코드/로직 변경 0건)**: 새 매매기법 추가 / 안전
flag 변경 / `.env` 수정 / broker / Strategy / RiskManager / OrderExecutor /
`route_order` 코드 *전혀 변경 없이*, 현재 자동매매 시스템의 전 영역을 *단일
진실* 문서로 카탈로그화한 audit PR. 6개 매매기법 (`sma_crossover` /
`rsi_reversion` / `vwap_strategy` / `orb_vwap` / `volume_breakout` /
`pullback_rebreak`) 외 *어떤 전략도 존재하지 않음을* 정적 grep + dataclass
가드로 lock. 가짜 / 경쟁사 전략명 (`골든브릿지` / `100% 승률` /
`guaranteed` / `magic strategy` 등) 도입 0건 (`test_strategy_registry_metadata.py`
+ 본 PR 의 `test_system_audit_invariants.py` 9개 통합 invariant 로 재검증).
`OperationMode` 7종 / `KillSwitchLevel` 4단계 / `live_trading_available=False`
6/6 / `backtest_available=True` 6/6 / `paper_trading_available=True` 6/6 /
`ENABLE_LIVE_TRADING=ENABLE_AI_EXECUTION=ENABLE_FUTURES_LIVE_TRADING=false`
(`.env.example` default) / `KIS_IS_PAPER=true` default / `DEFAULT_MODE=SIMULATION`
default 모두 테스트로 강제. **추가된 데이터 모델 0건** — 사용자 요청서의
`DecisionLog` 는 기존 `AgentDecisionLog` (chain_id + symbol + reasons + meta)
가 이미 모든 필드를 표현. 자세한 카탈로그:
[`docs/system_audit_2026_05.md`](docs/system_audit_2026_05.md) — 18개 섹션,
6개 전략의 파일 / 클래스 / entry / exit / risk_profile / 핵심 파라미터 /
모드 별 가용성 / UI 표시명 (10곳) / 위험관리 8개 Rule / Broker 4개 어댑터 /
Agent 15종 / DB 22 migrations / 알림 채널.

**#86 Desktop Installer / Beta 배포 (skeleton + 문서)**: 베타테스터가
PowerShell / uvicorn / npm 명령어를 직접 실행하지 않고 `AgentTrader-v1-Setup.exe`
를 더블클릭해 설치하고 바탕화면 아이콘으로 실행할 수 있게 만드는 *Windows
설치형 앱* 구조 — Tauri v2 채택. 본 PR 시점: `src-tauri/` skeleton(`tauri.conf.json`
/ `Cargo.toml` / `src/main.rs` / `capabilities/default.json` / `icons/README.md`
/ `.gitignore` / `build.rs`) + 6개 문서 + `UpdateCheckerCard` 프론트엔드(mock) +
`.github/workflows/desktop-release.yml`(draft, manual_dispatch only). Tauri
**updater 는 비활성** (`plugins.updater.active=false`, `pubkey=""`) — 실 활성화
는 후속 PR에서 `tauri signer generate` 결과 public key 만 commit, private key 는
`GitHub Secrets::TAURI_PRIVATE_KEY` 에만. **본 PR 에서 `ENABLE_LIVE_TRADING` /
`ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` / API Key / Secret / 계좌
번호 변경 0건, broker 호출 0건**. `UpdateCheckerCard` 는 mock 응답만 사용
(`provider` prop 으로 테스트 주입 가능) — 자동 *적용* 0건, 사용자가 "재시작
하여 적용" 명시 클릭해야만 시뮬레이션 진행. 자세한 정책:
[`docs/desktop_packaging.md`](docs/desktop_packaging.md) — 패키징 / Tauri 구조 /
backend 자동 실행 설계,
[`docs/desktop_update_policy.md`](docs/desktop_update_policy.md) — 자동 업데이트
/ 서명 키 관리,
[`docs/beta_tester_install_guide.md`](docs/beta_tester_install_guide.md) — 초보자
설치 가이드,
[`docs/tailscale_smartphone_access.md`](docs/tailscale_smartphone_access.md) —
스마트폰 원격 관제,
[`docs/first_run_setup_wizard.md`](docs/first_run_setup_wizard.md) — 첫 실행
wizard 설계.

