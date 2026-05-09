# Agent Architecture (#51)

본 문서는 6개 표준 Agent 역할의 공식 contract를 정의한다. CLAUDE.md 절대 원칙 1, 2 + [`agent_design.md`](agent_design.md) 위에서, *Agent의 권한 범위*를 코드 단에서 분리하는 정책 layer.

## 1. 목적

Agent가 한 번에 *판단·주문*하지 않도록 역할을 분리한다. 단일 Agent가 분석 / 추천 / 주문을 모두 만들면:

- 사고 발생 시 책임 추적 불가 ("어느 단계에서 잘못됐나")
- AI bug / hallucination이 곧장 broker 주문으로 이어질 위험
- 운영자가 *어떤 단계*에서 제어 가능한지 모호

본 contract는 6개 역할로 책임을 분리하고, *어떤 역할도 broker 주문을 만들지 못하게* 한다 — ExecutionRecommender도 approval queue *후보 payload*까지만 생성한다.

## 2. 핵심 원칙 (절대 invariant)

| 원칙 | 구현 |
|---|---|
| Agent는 분석 / 추천 / 리포트만 한다 | 모든 출력은 [`AgentOutput`](../backend/app/agents/base.py) |
| Agent는 **broker를 모른다** | `app.agents.*` 모듈에서 `app.brokers.*` import 0건 (정적 grep 가드) |
| Agent는 **OrderExecutor를 호출하지 않는다** | `app.execution.executor` import 0건 |
| Agent는 **route_order를 호출하지 않는다** | `app.execution.order_router` import 0건 |
| Execution Recommender도 **직접 주문 금지** | approval queue 후보 *payload*만 생성, `can_execute_order=False` |
| `is_order_intent = False` 불변 | `AgentOutput.__post_init__` ValueError 가드 |
| `can_execute_order = False` 불변 | `AgentOutput.__post_init__` ValueError 가드 |

본 invariant는 [`tests/test_agents_architecture.py`](../backend/tests/test_agents_architecture.py)로 강제.

## 3. 역할별 Agent 표

### 3.1 ObserverAgent
- **목적**: 시장 / 데이터 / 운영 상태를 *관찰*만
- **입력**: `market_state`, `watchlist`, `recent_signals`
- **출력**: `AgentOutput(decision=OBSERVE)`
- **금지행동**: 주문 결정 / 추천 / 승인 후보 생성, broker 호출
- **현재 구현**: `backend/app/agents/roles.py::ObserverAgent`
- **주문 권한**: ❌

### 3.2 AnalystAgent
- **목적**: Observer가 만든 raw 데이터에서 *분석 의견* 도출
- **입력**: `market_state`, `watchlist`, `recent_signals`
- **출력**: `AgentOutput(decision=ANALYZE)` + confidence
- **금지행동**: 주문 추천 / 승인 후보 생성, broker 호출
- **현재 구현**: `backend/app/agents/roles.py::AnalystAgent`
- **주문 권한**: ❌

### 3.3 RiskAuditorAgent
- **목적**: 일일 손실 / 중복 주문 / stale data / risk events 점검
- **입력**: `risk_state`, `audit_summary`
- **출력**: `AgentOutput(decision=WARN | REJECT | OBSERVE)` + `risk_flags`
- **금지행동**: broker 호출 (실제 거부는 `RiskManager`가 결정), 주문 생성, **emergency_stop 직접 토글**
- **현재 구현**:
  - `backend/app/agents/roles.py::RiskAuditorAgent` (#51 contract — risk_flags / decision)
  - `backend/app/agents/risk_auditor.py::RiskAuditorAgent` (#54 — DB 기반 advisory, `RiskAuditorReport` + audit_level + risk_score + PAUSE/EMERGENCY_STOP_RECOMMENDED. [`risk_auditor_agent.md`](risk_auditor_agent.md))
- **주문 권한**: ❌
- **긴급정지 토글 권한**: ❌ (advisory only — `emergency_stop_recommended`만 carry, 실제 토글은 운영자가 Kill Switch UI에서 수동 수행)

분류 규칙:
| 조건 | decision | flag |
|---|---|---|
| `emergency_stop=True` | REJECT | `emergency_stop_active` |
| `daily_loss_pct ≥ 80%` | REJECT | `daily_loss_critical` |
| `daily_loss_pct ≥ 50%` | WARN | `daily_loss_elevated` |
| `stale_price_rejections > 0` | WARN | `stale_data_recent` |
| `duplicate_rejections > 0` | WARN | `duplicate_orders_recent` |
| 위 조건 중 critical 없음 + 그 외 flag 있음 | WARN | (해당 flags) |
| 모두 통과 | OBSERVE | (없음) |

### 3.4 StrategyResearcherAgent
- **목적**: 전략 / 백테스트 메타데이터 분석 + 개선안 제안
- **입력**: `extra.backtest_summary` (win_rate, profit_factor 등)
- **출력**: `AgentOutput(decision=REPORT | RECOMMEND)`
- **금지행동**: broker 호출, 전략 자동 활성화/비활성화 (운영자 수동 결정)
- **현재 구현**: `backend/app/agents/roles.py::StrategyResearcherAgent`
- **주문 권한**: ❌

분류 규칙:
| 조건 | decision |
|---|---|
| `win_rate < 0.45` 또는 `profit_factor < 1.0` | RECOMMEND |
| 그 외 | REPORT |

### 3.5 ReportWriterAgent
- **목적**: audit summary + risk state + 다른 agent outputs를 합쳐 리포트 작성
- **입력**: `audit_summary`, `risk_state`, `extra (other agents output)`
- **출력**: `AgentOutput(decision=REPORT)`
- **금지행동**: broker 호출, 주문 추천
- **현재 구현**: `backend/app/agents/roles.py::ReportWriterAgent`
- **주문 권한**: ❌

### 3.6 ExecutionRecommenderAgent (가장 권한이 높지만 *여전히* 주문 X)
- **목적**: 매수 / 매도 후보 *제안*만 — approval queue 후보 payload 생성까지
- **입력**: `market_state`, `recent_signals`, `watchlist`
- **출력**: `AgentOutput(decision=APPROVAL_CANDIDATE | NO_OP)` + `approval_candidate` payload
- **금지행동**:
  - **broker / OrderExecutor / route_order 호출 0건** (정적 grep 가드)
  - **approval queue *등록* 금지** (caller 책임 — 별도 흐름)
  - AI key / Secret 사용 금지 — deterministic mock만 (실 LLM 통합은 후속 옵트인)
- **현재 구현**: `backend/app/agents/roles.py::ExecutionRecommenderAgent`
- **주문 권한**: ❌ (`can_execute_order=False` 불변)

`approval_candidate` payload 구조 (caller가 [`app.ai.assist.submit_candidate`](../backend/app/ai/assist.py)#44 같은 별도 흐름에 전달):

```python
{
    "source":             "AGENT_EXECUTION_RECOMMENDER",
    "symbol":             "005930",
    "side":               "BUY",
    "quantity":           1,
    "order_type":         "MARKET",
    "confidence":         85,
    "supporting_reasons": ["..."],
    "opposing_reasons":   [],
    "risk_note":          None,
    "is_order_intent":    False,    # *주문 객체가 아님* 명시
}
```

## 4. AgentOutput 표준

| 필드 | 의미 |
|---|---|
| `role` | 출력 생성한 Agent 역할 (`AgentRole` enum) |
| `decision` | 카테고리 (`AgentDecision` enum: OBSERVE / ANALYZE / WARN / REJECT / REPORT / RECOMMEND / APPROVAL_CANDIDATE / NO_OP) |
| `summary` | 사람이 읽을 한 줄 |
| `reasons` | 사유 리스트 |
| `confidence` | 0~100 (선택) |
| `risk_flags` | WARN/REJECT 사유 키 (예: `stale_data_recent`, `emergency_stop_active`) |
| `approval_candidate` | ExecutionRecommender만 채움 — caller 흐름용 dict |
| `metadata` | agent별 raw 결과 carry |
| `is_order_intent` | **항상 False** (가드) |
| `can_execute_order` | **항상 False** (가드) |
| `created_at` | UTC 시각 |

## 5. AgentContext 표준 입력

caller가 채워서 전달. 어떤 Agent도 본 context에서 broker 인스턴스 / API key / Secret을 받지 *않는다* — 본 dataclass의 필드 이름이 advisory 메타만 carry (정적 테스트로 lock).

```python
@dataclass(frozen=True)
class AgentContext:
    operator_intent:    str | None
    market_state:       dict | None
    watchlist:          list[str] | None
    recent_signals:     list[dict] | None
    audit_summary:      dict | None
    risk_state:         dict | None
    extra:              dict | None
```

## 6. Registry & 호출 흐름

```python
from app.agents.roles import build_default_registry
from app.agents.base import AgentRole, AgentContext

registry = build_default_registry()
agent = registry[AgentRole.OBSERVER]
output = agent.run(AgentContext(watchlist=["005930"]))
# output.decision == AgentDecision.OBSERVE
# output.is_order_intent == False
```

### 일반 호출 흐름 (예: operating_loop)

```
operating_loop
  ↓
ObserverAgent.run()        → OBSERVE
  ↓
AnalystAgent.run()         → ANALYZE
  ↓
RiskAuditorAgent.run()     → OBSERVE / WARN / REJECT
  ↓
ExecutionRecommenderAgent.run()  → APPROVAL_CANDIDATE 또는 NO_OP
  ↓
(caller가 별도 흐름에서 candidate를 approval queue에 넣음 — 본 모듈은 *제안*만)
  ↓
... [LIVE_AI_ASSIST(#44) flow] ...
  ↓
RiskManager.evaluate_order  →  PermissionGate.approve  →  OrderExecutor  →  Broker
```

## 7. API surface (read-only)

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/agents/architecture` | GET | 6개 표준 역할 + 절대 invariant 안내 |
| `/api/agents/catalog` | GET | 등록된 Agent 인스턴스의 metadata 카탈로그 |
| `/api/agents/mock-run` | POST | 단일 Agent를 mock 모드로 호출 — broker 호출 0건, audit row 0건 |

`/api/agents/mock-run`은 deterministic — 같은 context 입력에 같은 출력. 알 수 없는 role은 `decision=NO_OP` + `valid_roles` metadata 반환 (500 X).

## 8. 절대 invariant 요약 (테스트로 강제)

| invariant | 가드 |
|---|---|
| `AgentOutput.is_order_intent=True` 시 ValueError | `test_agent_output_rejects_is_order_intent_true` |
| `AgentOutput.can_execute_order=True` 시 ValueError | `test_agent_output_rejects_can_execute_order_true` |
| `app.agents.base`가 broker / executor / route_order import 0건 | `test_agents_base_module_does_not_import_broker_or_executor` |
| `app.agents.roles`도 broker import 0건 | `test_agents_roles_module_does_not_import_broker_or_executor` |
| `AgentContext`에 broker / api_key / secret 필드 0개 | `test_agent_context_does_not_carry_broker_or_keys` |
| 모든 mock 전략 `metadata.can_execute_order=False` | `test_registry_all_agents_have_can_execute_order_false` |
| ExecutionRecommender의 `approval_candidate.is_order_intent=False` | `test_execution_recommender_candidate_payload_marks_not_order_intent` |
| `/api/agents/mock-run`이 audit / approval row 생성 X | `test_api_mock_run_does_not_create_audit_or_orders` |

## 9. 변경 시 동기화

- 새 `AgentRole` enum 추가 → `_ARCHITECTURE_ROLE_DOC` (routes_agents.py) + `build_default_registry()` + 본 문서 §3 + 테스트
- 새 `AgentDecision` 추가 → 본 문서 §4 + 테스트
- ExecutionRecommender의 `approval_candidate` payload 구조 변경 → 본 문서 §3.6 + caller 흐름(예: #44) 동기화
- 실 LLM 통합 추가 → 별도 옵트인 PR + invariant 재검증 (mock 가정이 깨지지 않는지)

## 10. 관련 문서

- [`agent_design.md`](agent_design.md) — Agent 분리 정책 + AI 직접 주문 금지 (#216 등)
- [`market_observer_agent.md`](market_observer_agent.md) — Market Observer 정책 + snapshot JSON 구조 (#52)
- [`news_trend_agent.md`](news_trend_agent.md) — News/Trend Agent 정책 + theme_signals 요약 (#53)
- [`risk_auditor_agent.md`](risk_auditor_agent.md) — Risk Auditor 정책 + advisory invariant (#54)
- [`agent_decision_schema.md`](agent_decision_schema.md) — agent decision audit log 스키마 (#187+)
- [`ai_permission_gate.md`](ai_permission_gate.md) — AI 권한 단계 (#39)
- [`ai_assisted_trading_policy.md`](ai_assisted_trading_policy.md) — LIVE_AI_ASSIST 흐름 (#44)
- [`ai_execution_policy.md`](ai_execution_policy.md) — LIVE_AI_EXECUTION 안전 게이트 (#45)
- [`risk_manager_contract.md`](risk_manager_contract.md) — 주식 RiskManager 표준 진입점 (#34)
- [`order_executor_contract.md`](order_executor_contract.md) — broker 단일 호출 진입점 (#40)
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 1, 2
