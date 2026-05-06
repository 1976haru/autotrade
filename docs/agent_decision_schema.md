# Agent Decision Schema (185, MUST)

10-Agent Council의 결정 출력 구조 + DB 영구화 스키마. 운영자 사후 분석 / Audit 용.

## 모듈 구성

| 파일 | 역할 |
|---|---|
| [`app/ai/agents/base.py`](../backend/app/ai/agents/base.py) | `Agent` ABC, `AgentDecision` dataclass, `persist_decision`, `new_chain_id` |
| [`app/ai/agents/council.py`](../backend/app/ai/agents/council.py) | 10 agent 구현 + `ChiefTradingAgent.coordinate(ctx)` |
| [`app/db/models.py::AgentDecisionLog`](../backend/app/db/models.py) | DB 테이블 (alembic 0014) |

## AgentDecision dataclass

```python
@dataclass
class AgentDecision:
    agent_name:  str            # "ChiefTradingAgent" / "MarketRegimeAgent" / 등
    decision:    str            # BUY / SELL / HOLD / APPROVE / REJECT / WARN / INFO
    confidence:  int            # 0..100
    reasons:     list[str]      # human-readable 사유
    meta:        dict[str, Any] # agent 별 structured payload
    symbol:      str | None     # 대상 종목 (해당 없으면 None)
    chain_id:    str | None     # 같은 의사결정 사슬 묶는 키 (UUID)
```

## AgentDecisionLog (DB)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | int (pk) | |
| `created_at` | datetime (idx) | |
| `agent_name` | str(64) (idx) | "ChiefTradingAgent" 등 |
| `symbol` | str(16) | nullable |
| `mode` | str(32) (idx) | OperationMode value (VIRTUAL_AI_EXECUTION / SIMULATION 등) |
| `decision` | str(32) (idx) | BUY / SELL / HOLD / APPROVE / REJECT / WARN / INFO |
| `confidence` | int | 0..100, nullable |
| `reasons` | JSON list[str] | |
| `meta` | JSON dict | nullable |
| `chain_id` | str(64) (idx) | 같은 결정 사슬 묶는 UUID |

## 10 Agent 명세

### 1. ChiefTradingAgent (orchestrator)

종합 결정자. `coordinate(ctx: CouncilContext)` → `(chief_decision, [9 member decisions])`. 종합 규칙:
- `risk_agent.decision == REJECT` → REJECT
- `exit.decision == SELL` → SELL (청산 우선)
- `entry.decision == BUY` AND `news.decision != WARN` → BUY
- 그 외 → HOLD

### 2. MarketRegimeAgent

`regime` (`trending_up` / `trending_down` / `ranging` / `high_vol` / `any`)와 `sample_size`를 입력받아 INFO 결정 반환. confidence는 sample size에 비례.

### 3. StrategySelectionAgent

regime → 권장 strategy 매핑:
- `trending` → `sma_crossover`
- `trending_up`/`down` → `orb_vwap`
- `ranging` → `rsi_reversion`
- `high_vol` → `sma_crossover`

### 4. StockSelectionAgent

`candidates: list[str]` 중 첫 후보 선택 (단순화). 빈 목록은 HOLD.

### 5. PositionSizingAgent

`equity * risk_pct / 100 / price` → 정수 quantity 권장.

### 6. RiskOfficerAgent

emergency_stop / max_order_notional 사전 검토 advisory. 실제 가드는 `RiskManager.evaluate_order`가 수행.

### 7. EntryTimingAgent

`last_close > prev_close` → BUY, 아니면 HOLD. (단순 close-up 시그널, deterministic)

### 8. ExitTimingAgent

`unrealized_pct ≤ -stop_loss_pct/100` → SELL (stop_loss). `≥ +take_profit_pct/100` → SELL (take_profit). 그 외 HOLD.

### 9. NewsTrendAgent

`sentiment` (0..100): ≥70 INFO(positive), ≤30 WARN(negative), 그 외 INFO(neutral). 실 LLM 없이는 confidence 낮음 (40).

### 10. PostTradeReviewAgent

`realized_pnl + win_rate + trades` 기반 사후 평가. trades < 5는 INFO(insufficient_sample). pnl > 0 + win_rate ≥ 0.5는 verdict=good, 그 외 underperform WARN.

## chain_id 사용 패턴

ChiefTradingAgent.coordinate는 자동으로 새 chain_id를 발급해 모든 member decision에 부착한다. 사후 분석 시:

```python
from sqlalchemy import select
chain_decisions = db.execute(
    select(AgentDecisionLog)
    .where(AgentDecisionLog.chain_id == "<uuid>")
    .order_by(AgentDecisionLog.id)
).scalars().all()
# 1 chief + 9 members = 10 row
```

## 안전 invariant

1. **AI Key 없이 동작** — 모든 Agent는 deterministic stub. ANTHROPIC_API_KEY 미설정에도 정상 흐름.
2. **broker 직접 호출 0** — Agent 출력은 `AgentDecision` 데이터일 뿐. 호출자가 RiskManager → PermissionGate → OrderExecutor를 경유해야만 주문이 만들어진다.
3. **모든 결정 영구화** — `persist_decision(db, decision, mode)`로 AgentDecisionLog row 작성. 사후 분석 가능성 보장.

## 테스트

`backend/tests/test_agent_council.py` — 27 테스트:
- 9 member agent 각자 (16 테스트)
- ChiefTradingAgent orchestrator (8)
- persist_decision (3)

## 관련 문서

- [`docs/ai_virtual_execution_report.md`](ai_virtual_execution_report.md) — VIRTUAL_AI_EXECUTION 모드 + 가드
- [`docs/risk_guards_matrix.md`](risk_guards_matrix.md) — RiskPolicy 가드 매트릭스
- [`CLAUDE.md`](../CLAUDE.md) 절대 원칙 1, 2 — AI는 broker 직접 호출 X, 가드 경유 강제
