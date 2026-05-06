# Agent Stress Test Report

10-Agent Council의 스트레스 시나리오 + invariant 검증.

## 테스트 환경

- 실 LLM 호출 0건 — 모든 agent는 deterministic stub.
- ANTHROPIC_API_KEY 미설정 환경에서 동작 가능.
- AgentDecisionLog 테이블 (alembic 0014)에 모든 결정 영구화.

## 검증된 invariant

### 1. AI Key 없이 동작

```python
# AnthropicAiClient는 사용하지 않는다. Council의 모든 결정은 deterministic stub.
agent = ChiefTradingAgent()
decision, members = agent.coordinate(ctx)  # AI Key 무관
```

`backend/tests/test_agent_council.py` — 27 테스트 모두 LLM 호출 없이 통과.

### 2. broker 직접 호출 0

Agent 출력은 `AgentDecision` 데이터일 뿐. 호출자가 결정에서 OrderRequest를 만들고 `route_order(requested_by_ai=True)` 경유 — RiskManager / PermissionGate / OrderAuditLog 가드 체인 우회 0.

### 3. 모든 결정 audit 영구화

`persist_decision(db, decision, mode)` 호출 시 AgentDecisionLog row 작성. chain_id로 같은 의사결정 사슬의 10 agent 판단 일괄 조회 가능.

테스트:
```python
chief, members = chief_agent.coordinate(ctx, chain_id="test-chain-001")
persist_decision(db, chief, mode="VIRTUAL_AI_EXECUTION")
for m in members:
    persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
db.commit()

# 1 chief + 9 members = 10 row.
rows = db.query(AgentDecisionLog).filter_by(chain_id="test-chain-001").all()
assert len(rows) == 10
```

### 4. ChiefTradingAgent 종합 규칙

| 시나리오 | Chief 결정 |
|---|---|
| `risk_agent.decision == REJECT` | REJECT (최우선) |
| `exit.decision == SELL` | SELL (청산 우선) |
| `entry.decision == BUY` AND `news.decision != WARN` | BUY |
| 그 외 | HOLD |

### 5. 운영자가 가드 우회 못 함

- emergency_stop ON → ChiefTradingAgent.coordinate가 RiskOfficerAgent를 통해 REJECT로 surface. 호출자가 무시하고 OrderRequest를 만들어도 RiskManager (060) hard-reject.
- max_order_notional 초과 → RiskOfficerAgent REJECT. 다중 가드.

## 스트레스 시나리오 (`backend/tests/test_agent_council.py`)

| # | 시나리오 | invariant | 결과 |
|---|---|---|---|
| 1 | MarketRegimeAgent regime / sample size | INFO + meta.regime / confidence 비례 | ✅ |
| 2 | StrategySelectionAgent 5 regime 매핑 | trending → sma / ranging → rsi_reversion / 등 | ✅ |
| 3 | StockSelectionAgent empty candidates | HOLD | ✅ |
| 4 | StockSelectionAgent first candidate | symbol carry | ✅ |
| 5 | PositionSizingAgent 정상 | qty = equity * risk_pct / price | ✅ |
| 6 | PositionSizingAgent invalid inputs (equity=0/price=0) | HOLD | ✅ |
| 7 | RiskOfficerAgent emergency_stop | REJECT | ✅ |
| 8 | RiskOfficerAgent oversized notional | REJECT | ✅ |
| 9 | RiskOfficerAgent normal | APPROVE | ✅ |
| 10 | EntryTimingAgent close-up vs not-up | BUY / HOLD | ✅ |
| 11 | ExitTimingAgent stop_loss | SELL + reason_code='stop_loss' | ✅ |
| 12 | ExitTimingAgent take_profit | SELL + reason_code='take_profit' | ✅ |
| 13 | ExitTimingAgent within band | HOLD | ✅ |
| 14 | NewsTrendAgent sentiment 분기 | INFO/WARN | ✅ |
| 15 | PostTradeReviewAgent insufficient sample | INFO + reason | ✅ |
| 16 | PostTradeReviewAgent underperform | WARN + verdict | ✅ |
| 17 | Chief BUY when entry up + news neutral | BUY | ✅ |
| 18 | Chief HOLD when close not up | HOLD | ✅ |
| 19 | Chief REJECT when emergency_stop | REJECT | ✅ |
| 20 | Chief REJECT when oversized notional | REJECT | ✅ |
| 21 | Chief SELL when stop_loss | SELL + meta.exit_reason_code | ✅ |
| 22 | Chief HOLD when news WARN (entry BUY but news bad) | HOLD | ✅ |
| 23 | Chief chain_id links all 9 members | 모두 같은 chain_id | ✅ |
| 24 | Chief explicit chain_id 사용 | 운영자 명시 chain_id 보존 | ✅ |
| 25 | persist_decision 1 chief + 9 members | 10 rows | ✅ |
| 26 | persist_decision empty meta | 정상 영구화 | ✅ |
| 27 | Chief.decide ABC 인터페이스 만족 | decision in BUY/SELL/HOLD/REJECT | ✅ |

## 누락 / 향후 follow-up

- 실 LLM 통합 — `LiveAiAgent` 별도 PR (LIVE 영역, 사용자 옵트인 필요).
- News API 통합 — `NewsTrendAgent`는 현재 sentiment 정수 입력 stub. 실 RSS / 뉴스 API 통합은 외부 비용 발생 영역이라 옵트인.
- Council 가중치 학습 — chain_id 기반 historical 결정 → 가중치 자동 보정 (163 feedback loop 패턴 확장).

## 관련 문서

- [`docs/agent_decision_schema.md`](agent_decision_schema.md) — Schema 명세
- [`docs/ai_virtual_execution_report.md`](ai_virtual_execution_report.md) — VIRTUAL_AI_EXECUTION 흐름
- [`docs/stress_test_report.md`](stress_test_report.md) — 시스템 전체 stress
