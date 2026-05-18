# Step 4-06 — AI 직접 주문 금지 (Cross-Cutting Invariant Guard)

> 본 문서는 CLAUDE.md 절대 원칙 1~5 의 *코드-단 강제* 정책입니다. **AI Agent 의
> 추천 결과는 어떤 경우에도 실제 주문으로 해석되지 않습니다.** 본 PR 은 *기능
> 추가 0건* — 기존 invariant 의 *cross-cutting 통합 lock* 만.

## 1. 강제 invariant 매트릭스

| 항목 | 강제 위치 | 본 PR 의 검증 |
|---|---|---|
| `AgentOutput.is_order_intent=False` | `AgentBase.AgentOutput.__post_init__` ValueError | `test_agent_output_is_order_intent_true_raises` |
| `AgentOutput.can_execute_order=False` | `AgentBase.AgentOutput.__post_init__` | `test_agent_output_can_execute_order_true_raises` |
| `AgentMetadata.can_execute_order=False` | default 값 (모든 등록 agent) | `test_agent_metadata_can_execute_order_false_default` |
| Per-agent dataclass `is_order_signal=False` | 각 dataclass `__post_init__` (11개 클래스) | `test_all_dataclasses_reject_is_order_signal_true` |
| Per-agent dataclass `auto_apply_allowed=False` | 위 | `test_all_dataclasses_reject_auto_apply_allowed_true` |
| Per-agent dataclass `is_live_authorization=False` | 위 | `test_all_dataclasses_reject_is_live_authorization_true` |
| `StrategyCombinationRecommendation.auto_start_paper_trader=False` | `__post_init__` | `test_strategy_combination_auto_start_paper_trader_false` |
| `OverfitWarningReport.auto_disable=False` | `__post_init__` | `test_overfit_warning_report_auto_disable_false` |
| `MarketRegimeReport.auto_start_paper_trader=False` | `__post_init__` | `test_market_regime_report_auto_start_paper_trader_false` |
| advisory agents 의 broker / OrderExecutor / route_order import 0건 | 정적 grep (docstring-stripped) | `test_no_broker_executor_imports` (per-module parametrized) |
| advisory agents 의 외부 HTTP / AI SDK import 0건 (anthropic/openai/httpx/requests/yfinance) | 정적 grep | `test_no_external_http_or_ai_sdk` |
| advisory agents 의 safety flag mutate 0건 | 정적 grep | `test_no_safety_flag_mutation` |
| 순수 advisory agents 의 DB write 0건 | 정적 grep (`db.add` / `.commit` / raw DML) | `test_no_db_write_in_pure_advisory_agents` |
| `AgentRole` enum 6 role 매트릭스 | enum 정의 + 테스트 | `test_agent_role_enum_values` |
| `AgentDecision` enum 에 BUY/SELL/PLACE_ORDER 0개 | enum 정의 + 테스트 | `test_agent_decision_enum_no_buy_sell` |
| AI 추천 → PaperDecision 유일 변환 경로 | `app/auto_paper/decisions.py` | `test_paper_decision_invariants` 등 4 케이스 |
| frontend Agent UI 안전 배지 (Paper/advisory/실거래 OFF 라벨) | 정적 grep | `test_card_has_paper_only_or_advisory_badge` |
| frontend 카드에 `매수`/`매도`/`Place Order`/`실거래 시작`/`ENABLE_LIVE_TRADING 토글` *button 라벨* 0개 | 정적 grep | `test_card_has_no_buy_sell_button_labels` |
| frontend 카드에 API key / Secret / 계좌번호 carry 0건 | 정적 grep | `test_card_no_forbidden_secret_keywords` |

## 2. Orchestration 제외 모듈 (4-06 검사 외)

다음 모듈은 *sanctioned* broker / DB 경로를 사용 — **본 advisory 가드의 검증
대상 *아님*** :

| 모듈 | 사유 |
|---|---|
| `auto_trader_loop.py` | 자동매매 *orchestrator* — `route_order` 의 sanctioned 흐름 (RiskManager → PermissionGate → OrderExecutor) 사용. CLAUDE.md 절대 원칙 2 의 *유일한* 정식 broker 호출 경로. 본 모듈에 대한 별도 가드는 `test_auto_trader_loop` 에 강제. |
| `operating_loop.py` | Agent orchestration loop — approval queue / advisory 흐름 조정. |
| `agent_memory.py` | DB storage agent — memory persistence 를 sanctioned DB write 로 수행. |

본 제외 list 가 늘어나는 것은 *advisory 가 아닌 모듈* 추가를 의미 — review 시점에
신중 검토 필요.

## 3. 검증된 advisory dataclass 11개 (4-01 ~ 4-05)

본 PR 의 `TestPerAgentDataclassInvariants` 가 *각각 3 invariant 필드 × 11 클래스
= 33 케이스 + default 검증 11 케이스 = 44 케이스* 로 lock:

- 4-01: `StrategyAgentInputItem`, `StrategyAgentInput`
- 4-02 v1: `StrategyDecision`, `StrategyCombinationRecommendation`
- 4-02 v2: `PaperStrategyEntry`, `PaperStrategyCombination`
- 4-03: `OverfitWarning`, `OverfitWarningReport`
- 4-04: `MarketRegimeReport`
- 4-05: `StrategyExplanation`, `PaperStartExplanation`

각 클래스의 `__post_init__` 가 `is_order_signal=True` / `auto_apply_allowed=True`
/ `is_live_authorization=True` 시도를 *즉시* `ValueError` 로 차단.

## 4. AI 추천 → 실제 주문 *유일한* 경로

```
┌─────────────────────────────────────────────────────────────────────────┐
│ AI Agent advisory output                                                │
│ - AgentOutput(is_order_intent=False, can_execute_order=False)           │
│ - PaperStartExplanation / StrategyCombinationRecommendation 등          │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ caller (운영자 또는 paper_tick_handler)
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ app.auto_paper.decisions.process_ai_recommendation()                    │
│ - AIRecommendationInput → PaperDecision 변환 (결정론적 휴리스틱)        │
│ - PaperDecision.is_order_signal=False (양 끝 lock)                      │
│ - record_paper_event() → in-memory ledger (실 broker 호출 0건)          │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ *모의 체결* — paper_order_id 가상 ID
                           ▼
                  PaperLoopEvent (ledger 기록)
                  실 broker 호출 0건 — Paper-only 시뮬레이션
```

**실제 주문은 다른 흐름** (`auto_trader_loop` 의 `route_order` →
`RiskManager.evaluate_order` → `PermissionGate.approve` → `OrderExecutor.execute`
→ `broker.place_order`) 으로만 발생하며, **AI Agent advisory output 은 그 어떤
단계에도 직접 진입할 수 없다**.

## 5. ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION default

- 본 PR 시점 `.env.example` / 모든 workflow / 설정 default = **false**
- `test_settings_default_safety_flags` 가 settings 객체에 4개 flag 필드 존재 확인
- 실제 *값* default 검증은 별도 `test_repository_hygiene.py` 의
  `test_env_examples_safe_defaults` 가 lock

## 6. CLAUDE.md 절대 원칙 1~5 상속

- ✅ 원칙 1: AI 가 broker 주문 API 를 *직접* 호출하지 않는다 — `_FORBIDDEN_BROKER_PATTERNS`
- ✅ 원칙 2: 모든 주문은 `RiskManager → PermissionGate → OrderExecutor` — AI Agent
  output 이 이 흐름에 *직접* 진입 불가능
- ✅ 원칙 3: 기본 운용모드 SIMULATION/PAPER, LIVE_AI_EXECUTION 기본 비활성 — flag
  default 검증
- ✅ 원칙 4: API Key/Secret/계좌번호 frontend 저장/커밋 금지 — frontend 카드 +
  agent dataclass 모두 secret 필드 0건
- ✅ 원칙 5: 실 API 호출은 backend 만 — frontend Agent UI 에 broker fetch / 외부
  HTTP 0건

## 7. 신규 agent 모듈 추가 시 체크리스트

1. dataclass 에 `is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` 필드 + `__post_init__` ValueError 가드
2. `to_dict()` 에서 invariant 강제 (True 변환 차단)
3. broker / OrderExecutor / route_order import 0건 (advisory 모듈이면)
4. 외부 HTTP / AI SDK import 0건 (advisory 모듈이면)
5. `test_agents_no_direct_order_guard.py::_dataclass_cases` 의 fixture 에 새 클래스 추가
6. frontend 카드 추가 시 안전 배지 + 금지 라벨 button 0개 + secret keyword 0건

## 8. 본 PR 변경 요약

- **`backend/tests/test_agents_no_direct_order_guard.py`** (신규): 75 tests
  across 8 classes — cross-cutting invariant guard
- **`docs/agent_no_direct_order_guard.md`** (본 문서): 정책 / 매트릭스 / 흐름도

**기능 코드 변경 0건** — 기존 invariant 의 *통합 lock 만*. 운영 로직 / broker /
OrderExecutor / route_order / safety flag default / `.env` *모두 미터치*.
