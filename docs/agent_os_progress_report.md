# Agent OS Progress Report

본 보고서는 Auto Trader 프로젝트의 *Agent / Agent OS* 관련 작업 항목 진척도를 점검한 결과입니다. **점검만 수행하며 기능을 구현하지 않습니다.** 체크리스트 엑셀은 수정하지 않으며, 본 문서가 단일 진척 보고용입니다.

생성: `git log --oneline -1` 시점 — `0acd107 Merge branch 'feature/059-frontend-api-integration'`.

## 0. 상태 분류

| 분류 | 의미 |
|---|---|
| ✓ **완료** | 코드 / 테스트 / 문서 모두 main에 머지된 상태. 운영자 / Agent가 즉시 사용 가능. |
| 🟡 **기능완료/커밋대기** | 구현 / 테스트는 끝났으나 commit·push·merge가 안 되어 main에 반영되지 않음. |
| 🟠 **부분완료** | 일부 자산만 존재 (예: 코드만 있고 문서 / 테스트 부족; 또는 v1만 있고 v2 backlog). |
| ⚪ **미착수** | 항목 자체가 코드 / 문서에 발견되지 않음. backlog. |

본 보고서 작성 시점에 `git status`는 `## main...origin/main` + untracked `backend/.venv-310/`만 — main과 동기화 상태이므로 *기능완료/커밋대기* 항목은 없음.

---

## 1. 핵심 Agent 항목 (체크리스트 #51 ~ #59 + Agent Memory)

| 항목 | 현재 상태 | 구현 파일 | 테스트 파일 | 문서 파일 | 부족한 점 | 다음 작업 번호 | 우선순위 |
|---|---|---|---|---|---|---|---|
| **#51 Agent Architecture (6 roles)** | ✓ 완료 | `app/agents/base.py`, `app/agents/roles.py` | `tests/test_agents_architecture.py` (41) | `docs/agent_architecture.md`, `docs/agent_design.md` | mock 6개 역할 deterministic 구현. 실 LLM 통합 없음 (옵트인) | 후속: 실 LLM 통합 옵트인 PR | 중 |
| **#52 Market Observer** | ✓ 완료 | `app/agents/market_observer.py` | `tests/test_market_observer_agent.py` (31) | `docs/market_observer_agent.md` | regime 추론 — 단순 임계값 기반 | 후속: factor / pattern 기반 regime classifier 보강 | 중 |
| **#53 News / Trend Agent** | ✓ 완료 | `app/agents/news_trend_agent.py` | `tests/test_news_trend_agent.py` (25) | `docs/news_trend_agent.md` | provider 모두 disabled — 실 News / Trends API 통합은 별도 옵트인 PR | 후속: Google Trends alpha 통합 | 저 |
| **#54 Risk Auditor** | ✓ 완료 | `app/agents/risk_auditor.py` | `tests/test_risk_auditor.py` (41) | `docs/risk_auditor_agent.md` | 12종 위험 이벤트 감지. emergency_stop 자동 토글 X (의도) | 후속: 자동 알림 (텔레그램/이메일) | 중 |
| **#55 Strategy Researcher** | ✓ 완료 | `app/agents/strategy_researcher.py` | `tests/test_strategy_researcher.py` (52) | `docs/strategy_researcher_agent.md` | 자동 반영 X (의도). 실 LLM 자연어 rationale 미통합 | 후속: anthropic SDK 옵트인 통합 | 중 |
| **#56 Execution Recommender** | ✓ 완료 | `app/agents/execution_recommender.py`, `app/api/routes_execution_recommender.py` | `tests/test_execution_recommender.py` (43) | `docs/execution_recommender_agent.md` | LIVE_AI_ASSIST 모드 외 submit 차단 (의도) | 후속: 실 LLM candidate 생성 | 중 |
| **#57 Daily Report Agent** | ✓ 완료 | `app/agents/daily_report_agent.py`, `scripts/generate_daily_report.py` | `tests/test_daily_report_agent.py` (47) | `docs/daily_report_agent.md`, `docs/daily_report_policy.md` | 자동 스케줄러 / 알림 미통합 | 후속: cron / APScheduler + 텔레그램 송신 | 중 |
| **#58 Agent Memory** | ✓ 완료 | `app/agents/agent_memory.py`, `app/api/routes_agent_memory.py`, `alembic/0020` | `tests/test_agent_memory.py` (45) | `docs/agent_memory.md` | vector / semantic search 미구현 | 후속: embedding + vector DB 옵트인 | 중 |
| **#59 Frontend Integration** | ✓ 완료 | `frontend/src/services/backend/client.js`, `components/common/DataSourceBanner.jsx`, `components/BackendOfflineBanner.jsx`, `utils/errorMessage.js` | `DataSourceBanner.test.jsx` (23) + 1286 frontend tests | `docs/frontend_integration.md` | WebSocket / SSE 미통합 (polling만), code splitting 임계 초과 | 후속: WS push, code split, demo fixture 풍부화 | 저 |

### 1.1 백엔드 Agent 추가 자산 (#187 / #205 등 누적)

| 항목 | 현재 상태 | 구현 파일 | 테스트 파일 | 문서 파일 |
|---|---|---|---|---|
| Operating Loop (장 흐름 단계 stub) | ✓ 완료 | `app/agents/operating_loop.py` | `tests/test_agents_operating_loop.py` (29) | `docs/agent_design.md` |
| Market Regime classifier | ✓ 완료 | `app/agents/market_regime.py` | `tests/test_agents_market_regime.py` (14) | `docs/market_observer_agent.md` (#52 보강) |
| Signal Quality | ✓ 완료 | `app/agents/signal_quality.py` | `tests/test_agents_signal_quality.py` (10) | `docs/agent_design.md` |
| Agent Stress (시나리오) | ✓ 완료 | (mock fixtures + 검증) | `tests/test_agents_stress.py` (10) | `docs/agent_stress_test_report.md` |
| AI Agent Stats / Decisions API | ✓ 완료 | `app/api/routes_ai.py` | `tests/test_ai_agent_stats.py` (33) | `docs/agent_decision_schema.md` |

---

## 2. 사용자 명시 점검 항목 (8건)

| # | 항목 | 현재 상태 | 구현 파일 | 테스트 파일 | 문서 파일 | 부족한 점 | 다음 작업 번호 | 우선순위 |
|---|---|---|---|---|---|---|---|---|
| 1 | **ChiefTradingAgent Orchestrator v2** | 🟠 부분완료 (v1만) | `app/ai/agents/council.py:259` (`ChiefTradingAgent`) | `tests/test_agent_council.py` (orchestrator section) | `docs/agent_decision_schema.md` (v1 기준) | v1은 deterministic + 단일 단계 deliberation. v2 (multi-stage / tool-use / self-critic loop) 미구현. 별도 PR로 spec → 구현 → 테스트 필요 | 신규 # `chief_orchestrator_v2` (별도 옵트인 PR) | **중** |
| 2 | **StrategySelectionAgent** | ✓ 완료 | `app/ai/agents/council.py:36` | `tests/test_agent_council.py::test_strategy_selection_agent_picks_for_regime` 외 | `docs/agent_decision_schema.md` | regime 별 전략 매핑 deterministic. 실 backtest 점수 기반 선택은 후속 (StrategyResearcher #55 결과를 입력으로 받는 통합 미구현) | 후속: #55 + #56 + StrategySelection 통합 | **중** |
| 3 | **OperatorBriefingAgent** | ✓ 완료 | `app/ai/agents/enhanced.py:183` | `tests/test_agents_enhanced.py` (22) | `docs/agent_decision_schema.md` | 운영자 브리핑 markdown 생성. 자동 송신 미통합 (이메일 / 텔레그램 / Slack 옵트인 별도 PR) | 후속: 알림 채널 통합 | 저 |
| 4 | **StopLossGuardianAgent** | ✓ 완료 | `app/ai/agents/enhanced.py:273` | `tests/test_agents_enhanced.py` | `docs/agent_decision_schema.md` | 손절 임계 advisory만. *자동 청산 X* (의도, RiskManager 우회 금지). 실 broker 강제청산은 운영자가 수동 결정 | 후속: 손절 시그널 dashboard surface 강화 | 중 |
| 5 | **Agent Output Sanitizer** | ⚪ 미착수 | (없음) | (없음) | (없음) | `agent_memory.py::sanitize_text`은 *메모리 저장 입력*만 sanitize. AgentOutput / AgentDecision의 *summary / reasons / metadata*에서 PII / API key가 *유출되지 않도록 통과 필터*가 없음. Agent가 외부 LLM 응답을 carry할 때 secret leakage 위험 | 신규 # `agent_output_sanitizer` (별도 PR) | **상** |
| 6 | **Agent Council E2E Test** | 🟠 부분완료 | `tests/test_agent_council.py` (27 tests — 개별 agent + ChiefTradingAgent orchestrator + persist_decision + AgentDecisionLog DB integration) | (위 동일) | `docs/agent_decision_schema.md` | E2E "10개 agent 동시 호출 → ChiefTradingAgent 통합 결정 → DB 기록 → 운영자 dashboard 확인"의 *전체 chain* 단일 테스트 부재. 단계별 테스트는 충분 | 후속: full-chain integration 시나리오 테스트 추가 | **중** |
| 7 | **Mobile Operator Mode** | ✓ 완료 | `frontend/src/components/tabs/OperatorPanel.jsx`, `App.jsx` (모바일 BottomNav) | `OperatorPanel.test.jsx` | `docs/smartphone_operator_mode.md` (87 lines) | 알림 (push notification) / haptic feedback 미통합. PWA 변환 미진행 (#59 backlog) | 후속: PWA + push notification | 저 |
| 8 | **Agent Decision Audit Mapping** | ✓ 완료 | `app/db/models.py::AgentDecisionLog`, `app/api/routes_ai.py` (`agent-stats` / `agent-decisions` / `agent-decisions/summary`) | `tests/test_ai_agent_stats.py` (33) | `docs/agent_decision_schema.md` (123 lines) | (없음 — 기본 스키마 + 조회 API + 통계 + 문서 모두 갖춤) | 후속: chain_id 시각화 timeline (frontend) | 저 |

### 2.1 결론 — 누락 / 보강 필요

- ⚪ **미착수 (1건)**: **Agent Output Sanitizer** — 우선순위 **상**. 실 LLM 통합 시 secret leakage 방지 필수.
- 🟠 **부분완료 (2건)**:
  - **ChiefTradingAgent Orchestrator v2** — v1 충분, v2 (multi-stage/tool-use)는 LLM 통합 옵트인 시점에 동시 진행.
  - **Agent Council E2E Test** — 개별 / orchestrator 테스트는 충분, full-chain 단일 시나리오 테스트가 backlog.
- ✓ **완료 (5건)**: StrategySelectionAgent / OperatorBriefingAgent / StopLossGuardianAgent / Mobile Operator Mode / Agent Decision Audit Mapping.

---

## 3. Frontend Agent 카드 인벤토리

| 카드 | 파일 | 테스트 | 연동 backend |
|---|---|---|---|
| AgentCouncilCard | `components/tabs/AgentCouncilCard.jsx` | `.test.jsx` | `app/ai/agents/council.py` 결정 → AgentDecisionLog |
| AgentDecisionHero | `components/tabs/AgentDecisionHero.jsx` | `.test.jsx` | `routes_ai::agent-decisions` |
| AgentDecisionSummaryCard | `components/tabs/AgentDecisionSummaryCard.jsx` | `.test.jsx` | `routes_ai::agent-decisions/summary` |
| AgentLatestTile | `components/tabs/AgentLatestTile.jsx` | `.test.jsx` | `routes_ai::agent-decisions` |
| AgentStatsCard | `components/tabs/AgentStatsCard.jsx` | `.test.jsx` | `routes_ai::agent-stats` |
| AgentMemoryCard | `components/tabs/AgentMemoryCard.jsx` | `.test.jsx` | `routes_agent_memory::*` (#58) |
| AiExecutionPolicyCard | `components/tabs/AiExecutionPolicyCard.jsx` | `.test.jsx` | `routes_ai_execution::*` (#45) |
| ExecutionRecommenderCard | `components/tabs/ExecutionRecommenderCard.jsx` | `.test.jsx` | `routes_execution_recommender::*` (#56) |
| RiskAuditorCard | `components/tabs/RiskAuditorCard.jsx` | `.test.jsx` | `routes_agents::risk-auditor/*` (#54) |
| StrategyResearcherCard | `components/tabs/StrategyResearcherCard.jsx` | `.test.jsx` | `routes_agents::strategy-researcher/*` (#55) |
| MarketObserverCard | `components/tabs/MarketObserverCard.jsx` | `.test.jsx` | `routes_agents::market-observer` (#52) |
| NewsTrendCard | `components/tabs/NewsTrendCard.jsx` | `.test.jsx` | `routes_agents::news-trend` (#53) |
| OperatorPanel (Mobile) | `components/tabs/OperatorPanel.jsx` | `.test.jsx` | risk policy / emergency stop |

---

## 4. 테스트 / 문서 누계

| 카테고리 | 개수 |
|---|---|
| 백엔드 agent 관련 테스트 | **484** (15개 파일) |
| 프론트엔드 agent 관련 테스트 | 카드별 평균 14-20 테스트, 총 ~250건 (정확한 sweep 필요) |
| `docs/` agent 관련 문서 | 14개 (architecture / design / decision_schema / 7개 agent 정책 / memory / decision schema / stress / smartphone / virtual / promotion 등) |
| 백엔드 전체 테스트 | **2342** (`DEFAULT_MODE=SIMULATION` 기준, KIS env-leak 2건 deselect) |
| 프론트엔드 전체 테스트 | **1286** (68 files) |

### 4.1 백엔드 Agent 테스트 상세

| 파일 | 테스트 수 | 커버리지 |
|---|---|---|
| `test_agent_council.py` | 27 | Council 10개 agent + ChiefTradingAgent orchestrator + persist_decision + DB |
| `test_agent_memory.py` | 45 | Sanitize / Save / Search / Archive / Ingest helpers / Static guards (#58) |
| `test_agents_architecture.py` | 41 | #51 AgentBase / AgentOutput invariants |
| `test_agents_enhanced.py` | 22 | OperatorBriefing / ScenarioStress / Readiness / StopLossGuardian / AgentCritic |
| `test_agents_market_regime.py` | 14 | Market regime classifier |
| `test_agents_operating_loop.py` | 29 | 장 흐름 stage stub |
| `test_agents_signal_quality.py` | 10 | Signal quality scoring |
| `test_agents_stress.py` | 10 | 시나리오 stress |
| `test_ai_agent_stats.py` | 33 | AgentDecisionLog 조회 / 통계 / chain_id |
| `test_daily_report_agent.py` | 47 | Stats / Findings / Markdown / CLI / API (#57) |
| `test_execution_recommender.py` | 43 | Proposal / Precheck / Submit / Static guards (#56) |
| `test_market_observer_agent.py` | 31 | Observer snapshot (#52) |
| `test_news_trend_agent.py` | 25 | Theme summarization (#53) |
| `test_risk_auditor.py` | 41 | 12개 위험 이벤트 / Static guards (#54) |
| `test_strategy_researcher.py` | 52 | Findings / Suggestions / Markdown (#55) |
| **합계** | **470** | |

---

## 5. 안전 invariant 점검

본 보고서 작성 시점 다음 invariant가 모든 agent 모듈에서 lock 상태:

| invariant | 가드 |
|---|---|
| 실 broker `place_order` / `cancel_order` 호출 0건 (모든 agent 모듈) | 정적 grep 가드 (각 agent 테스트에 포함) |
| OrderExecutor / route_order 직접 호출 0건 (#54-#58 모듈) | 정적 grep 가드 |
| `is_order_signal=False` 불변 (모든 advisory agent) | dataclass `__post_init__` ValueError |
| `auto_apply_allowed=False` 불변 (#55 #57 #58) | 동일 |
| API key / Secret / 계좌번호 / 개인정보 frontend 미저장 | client.js 검사 + #58 sanitize fail-closed |
| `Failed to fetch` 원문 사용자 노출 0건 | #59 friendlyErrorMessage |
| `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` default off | env 미변경 (모든 agent PR) |

---

## 6. 다음 작업 우선순위 — 권고 순서

1. **(상)** Agent Output Sanitizer — 실 LLM 통합 전 필수. PII / secret leak 방지.
2. **(중)** ChiefTradingAgent Orchestrator v2 — multi-stage / tool-use / self-critic. v1과 별도 모듈 + opt-in flag.
3. **(중)** Agent Council E2E full-chain 시나리오 테스트 — 10개 agent → chief → DB → frontend 단일 시나리오.
4. **(중)** StrategySelectionAgent ↔ #55 StrategyResearcher ↔ #56 ExecutionRecommender 통합 — 백테스트 점수 기반 전략 선택.
5. **(중)** Risk Auditor 자동 알림 — 텔레그램 / 이메일 옵트인.
6. **(중)** Daily Report 자동 스케줄러 — 장 마감 시각 trigger.
7. **(저)** Mobile Operator Mode PWA / push notification.
8. **(저)** Agent Memory vector / semantic search.
9. **(저)** Frontend WebSocket / SSE.
10. **(저)** Demo fixture 풍부화 — GitHub Pages.

---

## 7. 보고서 사용 가이드

- 본 보고서는 *진척 점검 결과*이며 *체크리스트 엑셀*과 *별도*로 유지된다 (사용자가 엑셀을 직접 갱신).
- 각 항목의 **현재 상태** 컬럼은 main branch 시점 기준이며, 이후 작업 진행 시 본 문서를 PR로 갱신.
- **부족한 점**과 **다음 작업 번호** 컬럼은 *backlog 식별*이지 *즉시 구현 명령*이 아니다 — 운영자가 우선순위 / 옵트인 시점을 결정.
- **우선순위**: **상** (실거래 활성화 전 필수) / **중** (시스템 완성도) / **저** (편의 / 확장).

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — #51 6 roles contract
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`agent_decision_schema.md`](agent_decision_schema.md) — AgentDecisionLog 스키마 (#187+)
- [`agent_stress_test_report.md`](agent_stress_test_report.md) — 시나리오 stress
- [`smartphone_operator_mode.md`](smartphone_operator_mode.md) — Mobile Operator
- [`market_observer_agent.md`](market_observer_agent.md) (#52)
- [`news_trend_agent.md`](news_trend_agent.md) (#53)
- [`risk_auditor_agent.md`](risk_auditor_agent.md) (#54)
- [`strategy_researcher_agent.md`](strategy_researcher_agent.md) (#55)
- [`execution_recommender_agent.md`](execution_recommender_agent.md) (#56)
- [`daily_report_agent.md`](daily_report_agent.md) (#57)
- [`agent_memory.md`](agent_memory.md) (#58)
- [`frontend_integration.md`](frontend_integration.md) (#59)
