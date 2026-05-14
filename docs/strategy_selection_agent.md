# Strategy Selection Agent

> 지능형 advisory Agent — 시장 상태와 4개 단타 전략의 신호를 종합해 *최적
> 전략 조합*을 선택. **최종 결과는 주문이 아니라 approval candidate 전 단계의
> advisory 리포트**다. 사람이 승인하기 *전*에는 실제 주문이 발생하지 않는다.

코드: [`backend/app/agents/strategy_selection_agent.py`](../backend/app/agents/strategy_selection_agent.py)
테스트:
[`backend/tests/test_strategy_selection_agent.py`](../backend/tests/test_strategy_selection_agent.py),
[`backend/tests/test_strategy_signal_aggregator.py`](../backend/tests/test_strategy_signal_aggregator.py)

## 1. 목적

기존 시스템은 4개 단타 전략을 *개별 실행*한다 — 각 전략이 따로 신호를 내고
따로 결재 후보로 송출되어, 사람이 종목별로 *직접* 조합을 판단해야 했다. 본
Agent 는 이 조합 판단을 자동화한다:

1. 4개 전략의 `StrategyVote` 묶음을 받고
2. MarketRegime 을 고려해 `StrategySignalAggregator` 로 종목별 통합 후보 산출
3. 최종 채택 전략과 *제외된 전략들 + 사유* 를 carry
4. 운영자 / UI 에 "어느 전략을 골랐고 왜 다른 건 빠졌는지" 명시
5. `ExecutionRecommender` (#56) 흐름에 advisory payload 로 전달 가능

## 2. 대상 전략 4종 — 역할 분담

| strategy_id | 본 Agent 에서 역할 |
|---|---|
| `volume_breakout`  | **신규 BUY 후보** — 추세 추격 |
| `pullback_rebreak` | **신규 BUY 후보** — 보수적 진입 |
| `vwap_strategy`    | **EXIT/SELL 우선** — 손실 방어 |
| `orb_vwap`         | **OPENING_CHAOS cooldown 필수** — 장초반 과열 회피 |

`sma_crossover` / `rsi_reversion` 같은 다른 전략의 vote 도 받을 수 *있지만*,
본 PR 시점 default `regime_weights` 매트릭스는 단타용 4종만 명시. 다른 전략은
default weight `1.0` 으로 합산.

## 3. 데이터 모델

### 3.1 입력

`StrategySelectionInput`:
- `votes`: `tuple[StrategyVote, ...]` — 4개 전략의 vote 묶음
- `market_regime`: TREND_UP / CHOPPY / RISK_OFF / LOW_LIQUIDITY / OPENING_CHAOS
- `policy`: `AggregatorPolicy` (optional)
- `focus_symbol`: 단일 종목 분석 시 명시 (없으면 후보 자격 첫 종목 자동 선택)

### 3.2 출력 — `StrategySelectionReport`

| 필드 | 의미 |
|---|---|
| `symbol` | 분석 대상 종목 |
| `market_regime` | 합산 시점 regime |
| `selected_strategy` | 최종 채택 (없으면 None) |
| `final_action` | BUY / SELL / EXIT / WATCH / REJECT / NO_SIGNAL |
| `confidence` / `quality_score` | 가중 평균 + supporter boost |
| `conflict_level` | NONE / LOW / MEDIUM / HIGH |
| `candidate_qualified` | approval queue 송출 가능 여부 |
| `candidates` | `tuple[StrategyCandidate]` — 모든 vote 의 score / supporting 분류 |
| `blocked` | `tuple[BlockedStrategyEntry]` — 제외 전략 + reason |
| `reasons` / `risk_notes` | 사람이 읽는 carry |
| `aggregated_signal` | aggregator 결과 그대로 carry (advisory) |
| `is_order_intent` | **항상 False** (`__post_init__` ValueError 가드) |
| `is_order_signal` | **항상 False** (UI 가 표시) |
| `can_execute_order` | **항상 False** (직접 주문 불가) |

### 3.3 `BlockedStrategyEntry.reason` (BlockedReason enum)

| 값 | 의미 |
|---|---|
| `RISK_OFF_REGIME` | RISK_OFF 에서 BUY 차단 |
| `LOW_LIQUIDITY_REGIME` | 거래대금 부족 — BUY → WATCH 강등 |
| `ORB_COOLDOWN_ACTIVE` | OPENING_CHAOS 에서 ORB cooldown 미통과 |
| `QUALITY_BELOW_THRESHOLD` | 단일 전략 quality_score < 70 |
| `CONFIDENCE_BELOW_THRESHOLD` | 단일 전략 confidence < 50 |
| `CONFLICT_TOO_HIGH` | conflict_level HIGH — approval queue 차단 |
| `OPPOSING_VWAP_PRIORITY` | VWAP EXIT/SELL 우선 — BUY 압도 |
| `NO_SIGNAL` | 신호 없음 |
| `WATCH_ONLY` | WATCH only — 후보 자격 없음 |

BUY/SELL/HOLD 같은 *주문 결정 라벨* 0건 (운영자 가독성을 위해 카테고리 분리).

## 4. 장세별 전략 선택 규칙

### 4.1 RISK_OFF — 모든 BUY 차단

`market_regime == "RISK_OFF"` 면:
- 모든 BUY vote 는 합산에서 제외 → `blocked` 에 `RISK_OFF_REGIME`
- `final_action = REJECT`, `selected_strategy = None`
- EXIT/SELL 은 여전히 통과 — 손실 방어 정보

### 4.2 LOW_LIQUIDITY — WATCH 강등

`market_regime == "LOW_LIQUIDITY"` 면 BUY 후보가 모두 `final_action = WATCH`
로 강등, `candidate_qualified = False`.

### 4.3 OPENING_CHAOS + ORB cooldown

`market_regime == "OPENING_CHAOS"` 에서 `orb_vwap` vote 가
`indicators["orb_cooldown_active"] = True` 이면 BUY → WATCH 강등 + `blocked`
에 `ORB_COOLDOWN_ACTIVE` 사유 carry.

### 4.4 장세 가중치 (default `regime_weights`)

| Regime | volume_breakout | pullback_rebreak | vwap_strategy | orb_vwap |
|---|---|---|---|---|
| `TREND_UP`      | **1.3** | **1.2** | 1.0 | 1.0 |
| `CHOPPY`        | 0.8     | 0.8     | **1.3** | 0.9 |
| `OPENING_CHAOS` | 0.7     | 0.6     | 0.8 | 1.0 (cooldown 필수) |
| `RISK_OFF`      | (BUY 전면 차단) |

`selected_strategy` 는 vote 들의 `confidence * weight` 가 가장 큰 전략 — 같은
방향 supporter 중 1위.

## 5. VWAP / Risk 우선 원칙

`vwap_strategy` 또는 `orb_vwap` 의 `EXIT` / `SELL` vote 가 있으면 **같은 종목**
의 모든 BUY vote 를 *항상* 압도:

- `final_action` 은 EXIT 또는 SELL
- `selected_strategy` 는 VWAP 계열 전략
- `supporting_strategies` 는 short 방향 vote 만
- BUY vote 들은 `blocked` 에 `OPPOSING_VWAP_PRIORITY` 사유로 carry

이유: 손실 방어 신호가 신규 진입 신호보다 *항상* 우선해야 한다.

## 6. 전략 충돌 처리

같은 종목 안에서 long ↔ short 방향이 동시에 있으면 `conflict_level`:
- 양쪽 confidence ≥ 70 → **HIGH**
- 한 쪽만 ≥ 70 → **MEDIUM**
- 둘 다 < 70 → **LOW**

`conflict_level > policy.max_conflict_for_candidate` (default MEDIUM) 이면
`candidate_qualified = False`. HIGH 면 **approval queue 등록 차단** — 운영자
검토 후 별도 의사결정 필요.

## 7. 단일 전략 가드

supporter 가 1개뿐인 경우:
- `quality_score < 70` (default) → WATCH 강등, `QUALITY_BELOW_THRESHOLD`
- `confidence < 50` (default) → 후보 자격 박탈, `CONFIDENCE_BELOW_THRESHOLD`

## 8. AgentBase 호환 (#51)

`StrategySelectionAgent` 은 `AgentBase` ABC 구현 (`role = STRATEGY_RESEARCHER`):
- `metadata` — 자기소개 (inputs / outputs / forbidden)
- `run(context: AgentContext) -> AgentOutput`
  - `context.extra["strategy_selection_input"]` 이 `StrategySelectionInput` 이면
    그대로 사용
  - 없으면 빈 입력 → NO_OP / NO_SIGNAL
  - 결과의 풍부 데이터는 `AgentOutput.metadata = report.to_dict()` 로 carry

`AgentDecision` 매핑:
- BUY 채택 (후보 자격 + 추천 전략 존재) → `RECOMMEND`
- EXIT / SELL (손실 방어 우선) → `WARN` (신규 진입 추천 아님)
- REJECT (RISK_OFF 차단) → `REJECT`
- WATCH → `OBSERVE`
- 그 외 → `NO_OP`

## 9. ExecutionRecommender 연계

`to_execution_proposal_from_selection(report, ...)` 가 `StrategySelectionReport`
→ `ExecutionProposal` 변환을 *advisory 단계로만* 수행:

- `aggregated_signal is None` → None
- `candidate_qualified=False` → None
- `final_action ∉ {BUY, SELL, EXIT}` → None
- BUY → `ProposalSide.BUY` / EXIT/SELL → `ProposalSide.SELL`
- 반환된 `ExecutionProposal.is_order_intent = False` /
  `can_execute_order = False` 그대로 carry

이후 운영자가 *명시적으로* `submit_proposal()` (=`submit_candidate(#44)`)
호출 시에야 기존 sanctioned 흐름 (`route_order` → RiskManager →
PermissionGate → OrderExecutor) 진입한다 — 본 Agent 는 그 단계를 *직접 호출
하지 않는다*.

## 10. 절대 원칙 매핑 (CLAUDE.md)

| 원칙 | 본 Agent 에서의 강제 방식 |
|---|---|
| 1. AI 가 broker 주문 직접 호출 금지 | broker import 0건 (정적 grep 가드) |
| 2. 주문은 Risk → Permission → Executor 순서 | `route_order` / `place_order` 호출 0건 |
| 3. 기본 운용모드 SIMULATION / PAPER | 본 Agent 는 mode 변경 0건 |
| 4. API Key / Secret frontend 저장 금지 | 본 Agent 는 api_key / secret 입력 0건 |
| 5. 실제 broker / AI API 호출은 backend | 본 모듈 외부 HTTP / AI SDK import 0건 |
| 6. 선물은 별도 어댑터 | 본 PR 시점 주식 단타 전략 4종만 |

## 11. 정적 grep 가드 (테스트로 lock)

`tests/test_strategy_selection_agent.py` 의 5개 invariant:

- `from app.brokers.{kis,mock_broker,futures_base}` / `OrderRequest` /
  `BrokerAdapter` import 0건
- `from app.execution.{executor,order_executor,order_router}` import 0건
- `route_order(` / `broker.place_order(` / `broker.cancel_order(` 호출 0건
- `from app.permission` / `from app.ai.assist` import 0건, approval queue
  submit helper 호출 0건
- `anthropic` / `openai` / `httpx` / `requests` import 0건
- `settings.enable_*_trading =` mutate 0건
- `StrategySelectionReport.{is_order_intent, is_order_signal, can_execute_order}=True`
  생성 시 ValueError

## 12. API 엔드포인트

`POST /api/agents/strategy-selection` — read-only:

- **broker 호출 0건, audit row 0건, DB 변경 0건, 외부 네트워크 호출 0건**
- 응답의 `is_order_signal` / `is_order_intent` / `can_execute_order` 은 모두
  *항상 False*

요청:
```json
{
  "votes": [
    {"strategy_id": "volume_breakout", "symbol": "005930",
     "action": "BUY", "confidence": 80, "quality_score": 85}
  ],
  "market_regime": "TREND_UP",
  "focus_symbol": "005930"
}
```

응답: `StrategySelectionReport.to_dict()` 그대로.

## 13. Frontend UI — `StrategySelectionCard`

Dashboard / Agent 탭에 배치되는 read-only 카드. 표시:

- 현재 선택 전략 (`selected_strategy` + beginner displayName)
- 후보 전략 점수 표 (`candidates`)
- 제외된 전략 + 사유 (`blocked` — `reason` enum 라벨)
- 충돌 여부 (`conflict_level`)
- MarketRegime
- 최종 판단 chip (`final_action`)
- **"주문 아님 · 승인 후보 전 단계"** 배지 영구 노출
- 주문 / "승인 큐로 보내기" 같은 *enabling* 버튼 0개 (테스트로 lock)

## 14. 후속 backlog

- 시세 stale / data quality (#21) 와 더 강하게 연동
- Agent Memory (#agent_memory) 검색 결과를 risk_notes carry
- 합산 결과를 audit row 로 영구화 (현재 stateless)
- `AggregatorPolicy` 운영자 UI 편집 (코드 default 만)
- 선물 전략용 `FuturesStrategySelectionAgent` (#49 대비)

## 15. 참고 문서

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙
- [`docs/strategy_signal_aggregator.md`](strategy_signal_aggregator.md) — #84
- [`docs/strategy_contract.md`](strategy_contract.md) — Strategy ABC 계약
- [`docs/execution_recommender_agent.md`](execution_recommender_agent.md) — #56
- [`docs/ai_assisted_trading_policy.md`](ai_assisted_trading_policy.md) — #44
- [`docs/agent_architecture.md`](agent_architecture.md) — #51 6역할 Agent
