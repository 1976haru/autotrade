# Step 4-01 — Agent 전략 최적화 입력 표준 스키마

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 스키마는 AI Agent (LLM) 가 *전략 추천 / 제외 사유를 설명*하기 위해
> 받는 read-only 입력이며, **주문 신호로 사용할 수 없습니다.**

## 1. 목적

3-02 ~ 3-08 의 산출물(백테스트 / 파라미터 최적화 / Walk-forward / Stress Test
/ Paper 후보 / 운영자 리포트)을 *AI Agent 가 읽을 수 있는 단일 표준 입력
구조* 로 변환합니다. 이를 통해:

- LLM 이 (전략, 종목, 파라미터) 별로 *왜 Paper 후보가 됐는지 / 왜 제외됐는지*
  자연어로 설명할 수 있게 됩니다.
- 단계별 metric drift 위험을 줄여 schema 한 곳만 유지보수하면 됩니다.
- 본 스키마를 통해 들어온 입력은 *advisory only* — 자동 paper trader 시작 /
  자동 실거래 활성화 / 자동 promotion 변경 *모두 금지*.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/agents/strategy_optimizer_agent.py` | 스키마 + 빌더 + Agent 본체 |
| `backend/tests/test_strategy_optimizer_agent.py` | 단위 / 통합 / invariant 테스트 |
| `docs/agent_strategy_input_schema.md` | 본 정책 문서 |

별도 `schemas/` 디렉토리는 만들지 않습니다 — 하나의 모듈에 schema +
builder + agent 를 함께 두어 의존성 + import 경로를 단순화.

## 3. 스키마 (`StrategyAgentInputItem` — per-strategy 14 필수 필드)

| # | 필드 | 타입 | 의미 |
|---|---|---|---|
| 1 | `strategy` | `str` | 전략 ID (`sma_crossover` 등 6개 중 하나) |
| 2 | `symbol` | `str` | 종목 코드 (`005930` 등) |
| 3 | `params` | `dict` | 파라미터 dict (예: `{"short": 5, "long": 20}`) |
| 4 | `backtest_metrics` | `dict` | 3-02 metrics (3-06 표준 14 키) |
| 5 | `optimization_metrics` | `dict` | 3-03 metrics |
| 6 | `walk_forward_verdict` | `str \| None` | 3-04 verdict (`HEALTHY`/`OVERFIT_RISK`/`INSUFFICIENT_DATA`) |
| 7 | `stress_test_verdict` | `str \| None` | 3-05 summary verdict (`PASS`/`WARN`/`FAIL`) |
| 8 | `paper_candidate_status` | `str` | `ReportStatus` 값 (6 상태) |
| 9 | `risk_flags` | `list[str]` | advisory 위험 신호 (예: `profit_factor_below_1`) |
| 10 | `exclusion_reasons` | `list[str]` | 제외 사유 (한국어, 어느 단계에서 탈락) |
| 11 | `recommendation_context` | `dict` | AI 가 자연어 설명에 *참고*할 안전 컨텍스트 |
| 12 | `is_order_signal` | `bool=False` | **불변** — `__post_init__` ValueError |
| 13 | `auto_apply_allowed` | `bool=False` | **불변** — `__post_init__` ValueError |
| 14 | `is_live_authorization` | `bool=False` | **불변** — `__post_init__` ValueError |

## 4. 스키마 (`StrategyAgentInput` — top-level wrapper)

| 필드 | 타입 | 의미 |
|---|---|---|
| `generated_at` | `str` | ISO8601 UTC |
| `schema_version` | `str` | `"1.0"` — 스키마 진화 시 의도적 옵트인 PR 필요 |
| `overall_status` | `str` | `ReportStatus` 값 (전체 판정) |
| `items` | `list[StrategyAgentInputItem]` | per-strategy 결과들 |
| `reasons_no_candidate` | `list[str]` | 후보 0건일 때 사유 carry |
| `advisory_disclaimer` | `str` | AI 가 *반드시 인지해야 할 advisory 안내* (비어있을 수 없음) |
| `metadata` | `dict` | pipeline / source 카운트 등 |
| `is_order_signal` | `bool=False` | **불변** |
| `auto_apply_allowed` | `bool=False` | **불변** |
| `is_live_authorization` | `bool=False` | **불변** |

## 5. AI Agent 가 사용하는 방법

```python
from app.agents.strategy_optimizer_agent import (
    StrategyOptimizerAgent, build_strategy_agent_input,
)
from app.agents.base import AgentContext
from app.analytics.strategy_optimization_report import (
    ReportInputs, build_operator_report,
)

# 옵션 A — 5 단계 산출물 경로로 빌드.
agent_input = build_strategy_agent_input(inputs=ReportInputs(
    backtest_summary_path="reports/backtest_real/real_data_backtest_summary.json",
    optimization_summary_path="reports/parameter_optimization/parameter_optimization_summary.json",
    walk_forward_summary_path="reports/walk_forward/walk_forward_summary.json",
    stress_test_summary_path="reports/stress_test/stress_test_summary.json",
))

# 옵션 B — 기존 OperatorReport(3-08) 객체로 빌드.
report = build_operator_report(ReportInputs(...))
agent_input = build_strategy_agent_input(operator_report=report)

# 옵션 C — Agent 호환 호출 (AgentBase / AgentContext / AgentOutput).
agent = StrategyOptimizerAgent()
ctx = AgentContext(extra={"strategy_agent_input": agent_input})
out = agent.run(ctx)
# out.metadata["strategy_agent_input"] 에 to_dict() 결과 carry.
# LLM caller 는 본 dict 를 *AI 입력 컨텍스트*로 사용한다 (직접 LLM 호출은
# 본 모듈 외부에서 수행).
```

## 6. AI Agent 가 *해서는 안 되는 행동* (CLAUDE.md 절대 원칙 상속)

- ❌ 본 입력으로 *직접 BUY / SELL / HOLD 신호 생성* — 입력에 그런 필드 자체가 없음.
- ❌ 본 입력으로 `broker.place_order()` / `route_order()` / `OrderExecutor.execute()` 호출.
- ❌ 본 입력으로 `strategy.enabled = False` / `policy.max_* =` 같은 mutation.
- ❌ 본 입력으로 `ENABLE_LIVE_TRADING=true` / `ENABLE_AI_EXECUTION=true` 토글.
- ❌ 본 입력에서 API key / Secret / 계좌번호 추출 — 스키마에 그런 필드 없음.

## 7. AI Agent 가 *할 수 있는 행동* (advisory only)

- ✅ Paper 후보 / 제외 후보에 대한 *자연어 설명* 생성.
- ✅ 단계별 탈락 이유 (`exclusion_reasons`) 를 운영자에게 *설명*.
- ✅ `risk_flags` 를 의사결정 컨텍스트로 *참고* (자동 차단 트리거 아님).
- ✅ "다음 검토 시 무엇을 봐야 하는가" 같은 *운영자 안내* 생성.
- ✅ `recommendation_context.headline_metrics` 를 사용한 trade-off 설명.

## 8. 절대 invariant (테스트로 lock)

| 항목 | 강제 방식 |
|---|---|
| `StrategyAgentInputItem.is_order_signal=False` | `__post_init__` ValueError |
| `StrategyAgentInputItem.auto_apply_allowed=False` | `__post_init__` ValueError |
| `StrategyAgentInputItem.is_live_authorization=False` | `__post_init__` ValueError |
| `StrategyAgentInput.is_order_signal=False` | `__post_init__` ValueError |
| `StrategyAgentInput.auto_apply_allowed=False` | `__post_init__` ValueError |
| `StrategyAgentInput.is_live_authorization=False` | `__post_init__` ValueError |
| `advisory_disclaimer` 비어있을 수 없음 | `__post_init__` ValueError |
| 14 필수 필드 모두 schema 에 존재 | `test_item_schema_has_required_14_fields` |
| schema 자체에 API key / Secret / 계좌번호 필드 0건 | `test_schema_has_no_secret_fields` |
| broker / OrderExecutor / route_order import 0건 | 정적 grep |
| 외부 HTTP / AI SDK import 0건 (anthropic/openai/httpx/requests/yfinance) | 정적 grep |
| `app.core.config.get_settings` import 0건 | 정적 grep |
| `settings.enable_*_trading =` mutate 0건 | 정적 grep |
| Agent 출력 JSON 에 `"decision": "BUY"` / `"SELL"` / `"HOLD"` 0건 | 통합 테스트 |
| Markdown / disclaimer 에 "Place Order" / "지금 매수" / "지금 매도" / "실거래 시작" 라벨 0건 | 통합 테스트 |

## 9. Agent 호환성 (#51 Agent Architecture)

`StrategyOptimizerAgent` 는 `AgentBase` 호환:
- `metadata.role = AgentRole.STRATEGY_RESEARCHER` (#55 Strategy Researcher Agent
  와 동일 역할 카테고리 — *전략 개선 제안* 전용).
- `metadata.can_execute_order = False` (영구).
- `run(context) -> AgentOutput(decision=REPORT, summary, reasons, risk_flags,
  metadata={strategy_agent_input, advisory_only=True, ...})`.
- `AgentOutput.is_order_intent = False` / `can_execute_order = False` 영구
  (dataclass `__post_init__` ValueError 가드).

## 10. CLAUDE.md 절대 원칙 준수

- ✅ `RiskManager → PermissionGate → OrderExecutor` 흐름 *변경 0건*.
- ✅ 본 모듈은 *read-only* — broker / DB / 외부 API 호출 0건.
- ✅ `is_order_signal=False` 영구 — 본 schema 결과로 직접 주문 생성 *불가능*.
- ✅ 운영 모드 / 안전 flag default 변경 0건.
- ✅ LLM 호출은 *본 모듈 외부* 에서 — 본 모듈은 LLM 입력 *정규화*만.
- ✅ 실거래 활성화는 별도 게이트 (#73 Live Manual Gate 등) + 사용자 명시 옵트인.

## 11. 테스트

```bash
python -m pytest backend/tests/test_strategy_optimizer_agent.py -q
python -m pytest backend/tests/test_repository_hygiene.py -q
python scripts/security_scan.py
```

## 12. Schema 진화 정책

- `schema_version` 은 *명시 옵트인 PR* 로만 bump 가능.
- 새 필드 추가 시:
  1. 기본값 제공 (backwards compat).
  2. 본 문서 + `test_item_schema_has_required_14_fields` 동시 갱신.
  3. invariant 필드 (`is_order_signal` / `auto_apply_allowed` /
     `is_live_authorization`) 는 *추가 / 변경 / 삭제 절대 금지*.
- 보안 / 안전 필드 (API key / Secret / 계좌번호) *영구 금지* —
  `test_schema_has_no_secret_fields` 가 lock.
