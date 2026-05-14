# Strategy Signal Aggregator

> 4개 단타 전략의 신호를 *종목 단위* 로 합쳐 통합 후보를 만든다. **최종
> 출력은 주문이 아니라 advisory 후보 데이터** — 모든 실 주문은 기존 sanctioned
> 경로(`route_order` → RiskManager → PermissionGate → OrderExecutor) 를 거쳐야
> 한다.

코드: [`backend/app/strategies/aggregator.py`](../backend/app/strategies/aggregator.py)
테스트: [`backend/tests/test_strategy_aggregator.py`](../backend/tests/test_strategy_aggregator.py)

## 1. 목표

기존 시스템은 4개 단타 전략을 *개별 실행* 한다 — 각 전략이 따로 신호를
내고 따로 결재 후보로 송출된다. 결과:

- 같은 종목에 대해 BUY 가 4번 송출되는 중복
- VolumeBreakout BUY vs VWAPStrategy EXIT 같은 *충돌* 이 운영자에게
  *별개* 후보로 보임
- 어느 전략이 *현재 장세에 적합* 한지 통합적으로 평가하기 어려움

본 Aggregator 는 이 4가지 전략의 신호를:

1. 종목 단위로 합치고
2. 충돌을 *해소* 하고
3. 장세에 따라 가중치를 적용해
4. ExecutionRecommender 가 사용할 수 있는 *단일 advisory 후보*를 만든다.

## 2. 대상 전략 4종 — 역할 분담

| strategy_id | 전략 | 본 Aggregator 에서 역할 |
|---|---|---|
| `volume_breakout`  | 거래대금 급증 + 고점 돌파 | **신규 BUY 후보** — 추세 진입 |
| `pullback_rebreak` | 눌림목 + 재돌파             | **신규 BUY 후보** — 보수적 진입 |
| `vwap_strategy`    | VWAP 평균 회귀 + 손절/회복 | **EXIT/SELL 우선** — 손실 방어 |
| `orb_vwap`         | ORB + VWAP 돌파            | **OPENING_CHAOS cooldown 필수** |

본 aggregator 는 위 4종 외 다른 전략(`sma_crossover` / `rsi_reversion`) 도
*받을 수는 있으나*, 본 PR 시점 default 가중치 매트릭스는 단타용 4종에 한해
정의됨. 다른 전략의 vote 는 default weight `1.0` 으로 합산.

## 3. 데이터 모델

### 3.1 `StrategyVote`

단일 전략이 한 종목에 대해 던지는 *vote*. **주문이 아니다.**

| 필드 | 의미 |
|---|---|
| `strategy_id` | 전략 식별자 (`volume_breakout` 등) |
| `symbol` | 종목 코드 |
| `action` | `SignalAction` — BUY/SELL/EXIT/WATCH/NO_SIGNAL |
| `confidence` | 0~100 — 전략 자체의 확신도 |
| `quality_score` | 0~100 — 신호 자체의 강도 |
| `reasons` / `risk_notes` | 운영자가 보는 텍스트 carry |
| `indicators` | 자유 dict — `orb_cooldown_active` 등 가드 키 carry |
| `sizing_hint` / `exit_plan` | 전략이 제안하는 사이즈 / 청산 계획 |
| `is_fresh` | stale vote 면 False — 합산 시 가중치 ½ |
| `voted_at` | 같은 (strategy, symbol) 중복 시 최신 vote 채택 |

### 3.2 `AggregatedSignal`

4개 vote 를 합친 최종 advisory. **주문이 아니다** —
`is_order_intent = False` 불변 (`__post_init__` ValueError 가드).

| 필드 | 의미 |
|---|---|
| `final_action` | `AggregatedAction` — BUY/SELL/EXIT/WATCH/REJECT/NO_SIGNAL |
| `confidence` / `quality_score` | 가중 평균 + supporting boost |
| `supporting_strategies` / `opposing_strategies` / `neutral_strategies` | 분류 |
| `reasons` / `risk_notes` | 각 vote 의 reason 통합 |
| `conflict_level` | NONE / LOW / MEDIUM / HIGH |
| `recommended_strategy` | 가중치 1위 strategy_id |
| `entry_plan` / `exit_plan` | 가장 보수적 stop_loss vote 의 exit_plan 채택 |
| `market_regime` | 합산 시점 regime carry |
| `candidate_qualified` | approval queue 로 송출 가능 여부 |

### 3.3 `SignalConflict`

같은 종목 안에서 BUY ↔ SELL/EXIT 충돌 1쌍 — 대표적 두 vote 만 기록.
`severity` = LOW / MEDIUM / HIGH.

### 3.4 `StrategyAggregationResult`

전체 결과 — `signals` / `conflicts` / `dropped` / `market_regime` /
`generated_at`. `is_order_intent = False` 불변.

## 4. 통합 규칙

### 4.1 같은 방향 2+ → confidence 상승

같은 종목에 *long*(BUY) vote 가 2개 이상이면 `confidence_boost_per_supporter`
만큼 추가 가산(default `+7` per supporter, supporter_count - 1 회 적용). 단,
최종 confidence 는 `min(100, ...)`.

### 4.2 VWAP loss / EXIT 우선

`vwap_strategy` 또는 `orb_vwap` 의 `EXIT` / `SELL` vote 가 있으면 같은 종목의
모든 BUY vote 를 *항상* 압도한다. 이유: 손실 방어 신호가 신규 진입보다 우선.

- `final_action` 은 EXIT / SELL
- `supporting_strategies` 는 short 방향 vote 만
- BUY vote 들은 `opposing_strategies` 로 carry

### 4.3 RISK_OFF — 모든 BUY 차단

`market_regime == "RISK_OFF"` 면:
- 모든 BUY vote 는 합산에서 제외
- 결과 `final_action = REJECT`, `candidate_qualified = False`
- EXIT/SELL 은 여전히 통과 — 손실 방어 정보

### 4.4 LOW_LIQUIDITY — WATCH 강등

`market_regime == "LOW_LIQUIDITY"` 면 BUY 후보는 모두 `final_action = WATCH`
로 강등, `candidate_qualified = False`.

### 4.5 충돌 처리

같은 종목에 long ↔ short 방향이 동시에 있으면 `conflict_level` 계산:
- 양쪽 모두 confidence ≥ 70 → **HIGH**
- 한 쪽만 ≥ 70 → **MEDIUM**
- 둘 다 < 70 → **LOW**

`conflict_level > policy.max_conflict_for_candidate` (default MEDIUM) 이면
`candidate_qualified = False`. HIGH 면 approval queue 에 *올리지 않는다*.

### 4.6 단일 전략 가드

supporter 가 1개뿐인 경우 `quality_score >= policy.min_quality_score_single_strategy`
(default 70) 이어야 후보 자격 유지. 미만이면 WATCH 로 강등.

### 4.7 중복 종목 → 1건

같은 `(strategy_id, symbol)` 의 vote 가 여러 개면 가장 최신(또는 입력 순서
마지막) 만 채택. 같은 symbol 에 *다른* 전략들의 vote 가 여러 개면 → 1개
`AggregatedSignal` 로 합쳐짐.

## 5. 장세별 가중치 (default `regime_weights`)

| Regime | volume_breakout | pullback_rebreak | vwap_strategy | orb_vwap |
|---|---|---|---|---|
| `TREND_UP`       | **1.3** | **1.2** | 1.0 | 1.0 |
| `CHOPPY`         | 0.8     | 0.8     | **1.3** | 0.9 |
| `OPENING_CHAOS`  | 0.7     | 0.6     | 0.8 | 1.0 (cooldown 필수) |
| `RISK_OFF`       | (BUY 전면 차단) |

`OPENING_CHAOS` + `orb_vwap` 의 vote 중 `indicators["orb_cooldown_active"] = True`
이면 BUY → WATCH 강등 (caller 가 cooldown 표시).

본 표에 없는 regime/strategy 조합은 default weight `1.0`.

## 6. ExecutionRecommender 연계

`to_execution_proposal(signal, *, expires_in_seconds, default_quantity)`
helper 가 `AggregatedSignal` → `ExecutionProposal` 변환을 *advisory 단계로만*
수행:

- `candidate_qualified = False` → `None` 반환
- `final_action` ∉ {BUY, SELL, EXIT} → `None` 반환
- BUY → `ProposalSide.BUY` / EXIT/SELL → `ProposalSide.SELL`
- `quantity` 는 caller 의 `default_quantity` (본 helper 는 사이즈 결정자
  *아님*) — 실제 수량은 RiskManager / PositionSizingAgent 가 별도 산출.
- 반환된 `ExecutionProposal.is_order_intent = False` /
  `can_execute_order = False` 불변은 그대로 carry.

이후 운영자가 `submit_proposal()` 을 호출하면 기존 `submit_candidate` (#44)
→ `route_order` → RiskManager → PermissionGate 흐름으로 진입한다 — 본 모듈은
그 흐름을 *직접 호출하지 않는다*.

## 7. 절대 원칙 매핑 (CLAUDE.md)

| 원칙 | 본 모듈에서의 강제 방식 |
|---|---|
| 1. AI 가 broker 주문 직접 호출 금지 | 본 모듈 broker import 0건 (정적 grep 가드) |
| 2. 주문은 RiskManager → PermissionGate → Executor 순서 | 본 모듈 `route_order`/`place_order` 호출 0건 |
| 3. 기본 운용모드 SIMULATION / PAPER | regime 입력 + caller mode 에 의존, 본 모듈은 mode 변경 0건 |
| 4. API Key / Secret frontend 저장 금지 | 본 모듈 api_key / secret 입력 0건 |
| 5. 실제 broker / AI API 호출은 backend 만 | 본 모듈은 외부 HTTP / AI SDK import 0건 |
| 6. 선물은 별도 어댑터 | 본 PR 시점 주식 단타 전략 4종만 |

## 8. 정적 grep 가드 (테스트로 lock)

`tests/test_strategy_aggregator.py` 의 9개 invariant:

- `from app.brokers.kis|mock_broker|futures_base` import 0건
- `from app.brokers.base import OrderRequest|BrokerAdapter` 0건
- `from app.execution.{executor,order_executor,order_router}` import 0건
- `route_order(` / `broker.place_order(` / `broker.cancel_order(` 호출 0건
- `anthropic` / `openai` / `httpx` / `requests` import 0건
- `from app.permission` / `from app.ai.assist` / `submit_candidate(` 호출 0건
- `AggregatedSignal.is_order_intent = True` 생성 시 ValueError
- `StrategyAggregationResult.is_order_intent = True` 생성 시 ValueError
- `StrategyVote.confidence` / `quality_score` 0~100 범위 검증

## 9. 후속 backlog

- 시세 stale / data quality 와 더 강하게 연동 (#21 data quality 결합)
- Agent Memory (#agent_memory) 검색 결과로 risk_notes carry
- 합산 결과를 audit row 로 영구화 (현재는 stateless)
- `AggregatorPolicy` 운영자 UI 편집 (현재 코드 default 만)
- 선물 전략용 `FuturesStrategyAggregator` (별도 모듈 — #49 대비)

## 10. 참고 문서

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙
- [`docs/strategy_contract.md`](strategy_contract.md) — Strategy ABC 계약
- [`docs/strategies.md`](strategies.md) — 6개 전략 카탈로그
- [`docs/execution_recommender_agent.md`](execution_recommender_agent.md) — #56
- [`docs/ai_assisted_trading_policy.md`](ai_assisted_trading_policy.md) — #44
  LIVE_AI_ASSIST 흐름
