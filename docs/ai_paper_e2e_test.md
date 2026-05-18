# Step 4-11 — AI Paper 자동매수/매도 E2E 테스트

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 테스트는 *advisory* — Paper 가상 체결 + AgentDecisionLog 기록만, 실 broker
> 호출 0건.

## 1. 목적

사용자가 원하는 *최종 흐름* 을 backend + frontend 한 컷의 E2E 시나리오로
영구 잠근다:

```
[시작 버튼] → Agent 추천 → PaperDecision 생성 → Paper ledger 기록
            → AgentDecisionLog 기록 → UI 에 최근 판단 표시
```

E2E 테스트는 *코드 변경 없이* 모듈 결합 결과를 검증한다. 새 운영 로직 추가 0건.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/tests/test_ai_paper_e2e.py` | 17 E2E backend cases (start → tick → ledger → decision-log) |
| `frontend/src/components/tabs/AutoPaperLoopCard.test.jsx::#4-11 AI Paper E2E UI flow` | 4 frontend E2E cases (시작 버튼 → 상태 → consumer strip) |
| `docs/ai_paper_e2e_test.md` | 본 정책 |

본 PR 은 *테스트 + 문서만 추가* — 운영 코드 (broker / OrderExecutor / route_order
/ Strategy / RiskManager / Alembic migration) 변경 0건.

## 3. 검증 흐름 (backend)

### 3.1 Full pipeline E2E (`TestFullPipelineE2E`)

- `_force_running(loop)` 으로 RUNNING 진입 + `monkeypatch` 으로 한국장 시계를
  OPEN 고정 → market-clock 의 lazy demote 가 테스트에 영향 0건.
- `loop.set_agent_consumer_runner(...)` 으로 deterministic provider 주입 (LLM
  / Anthropic / OpenAI / HTTP 호출 0건).
- `loop.tick()` 1회 호출.
- 검증 항목:
  - `cycle_count` 증가
  - `last_consumed=True`
  - `last_decision_count >= 1`
  - `last_decision_action == "BUY"`
  - `last_ledger_events >= 1`
  - `last_decision_log_count >= 1`
- `GET /api/auto-paper/status` 가 같은 카운터 carry.
- `GET /api/auto-paper/ledger?limit=10` 가 신규 BUY event carry.
- DB query → `AgentDecisionLog` 1+ row, `mode="PAPER"` + `decision="BUY"` +
  `agent_name="PaperDecisionBridge"`.
- `KisBrokerAdapter.place_order` / `cancel_order` spy 호출 **0건**.

### 3.2 Multiple ticks (`test_multiple_ticks_accumulate_state`)

- 5 ticks → 5 `AgentDecisionLog` rows, 모두 `mode="PAPER"`, 매 tick 신규
  `chain_id` (consumer runner 가 매번 새 chain_id 생성).
- broker spy 호출 **0건**.

### 3.3 Non-RUNNING 차단 (`TestNonRunningBlocksTick`)

- `PAUSED` / `STOPPED` / `EMERGENCY_STOP` / `MARKET_CLOSED` 4 상태 parametrized:
  - `loop.tick()` → `LoopNotRunningError`
  - consumer 호출 0건 → ledger / decision_log 신규 row 0건
  - broker spy 호출 0건

### 3.4 Risk veto 통합 (`TestRiskVetoE2E`)

- `risk_flags=["stale_data"]` 으로 provider 가 explanation 생성.
- 4-09 veto 가 BUY → HOLD 다운그레이드.
- `status.last_decision_action == "HOLD"`
- `AgentDecisionLog` row `meta.risk_veto=True`, `meta.risk_veto_reasons` 에
  `STALE_DATA` carry, severity=`BLOCK_NEW_ENTRY`.
- broker spy 호출 0건.

### 3.5 Endpoint envelope invariants (`TestEndpointEnvelopeInvariants`)

- `/api/auto-paper/status` / `/ledger` / `/events` / `/decision/latest` 4
  endpoint parametrized — 모두 `is_order_signal=False`, `forced_paper=True`
  (status만), advisory disclaimer carry.
- `POST /api/auto-paper/tick` 도 모든 invariant carry + broker spy 0건.

### 3.6 Decision-log endpoint (`TestDecisionLogEndpoint`)

- 빈 DB 상태에서 200 + `entries=[]` envelope.
- `limit=0` → 422 (`Query(ge=1)`).

### 3.7 Paper/Live separation E2E (`TestPaperLiveSeparationE2E`)

- start → 3 tick → stop 전체 lifecycle 에서 broker spy 호출 0건.
- `GET /status` 가 매 단계마다 invariant carry.

## 4. 검증 흐름 (frontend)

### 4.1 시작 → RUNNING → consumer strip (`E2E: start button → ...`)

- `_mockApiSequence(initial_paused, running_after_tick)` 으로 polling 시뮬레이션.
- 시작 버튼 클릭 → `autoPaperStart` 호출 검증.
- 다음 polling 으로 RUNNING + BUY 라벨 + 카운트 + Paper-only 배지 모두 표시.

### 4.2 BUY 라벨 = `<strong>`, 절대 `<button>` 아님 (`BUY label is a span`)

- consumer-action-BUY testid 의 tagName 이 `strong` 임을 확인 (button 거부).

### 4.3 금지 라벨 0건 (`zero forbidden order labels`)

- 카드 전체 textContent 에 `지금 매수` / `지금 매도` / `Place Order` /
  `실거래 시작` / `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `AI 자동매매 켜기` 0건.

### 4.4 필수 UI element 9종 동시 존재 (`required UI elements all present`)

- 시작/정지/긴급정지 버튼 + 상태 pill + consumer strip 5필드 (last_tick,
  decision_action, decision_count, ledger_events, decision_log_count) +
  Paper-only 배지 + 상단 safety badges 3종.

## 5. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| 전 흐름 `KisBrokerAdapter.place_order` 호출 0건 | `kis_place_order_spy` fixture |
| 전 흐름 `KisBrokerAdapter.cancel_order` 호출 0건 | `kis_cancel_order_spy` fixture |
| 모든 응답 envelope `is_order_signal=False` | 4 endpoint parametrized |
| `forced_paper=True` 영구 (status) | `test_running_status_after_setup` |
| `mode="PAPER"` 영구 (AgentDecisionLog row) | `test_tick_produces_decision_ledger_and_log` |
| RUNNING 외 4 상태 모두 tick → `LoopNotRunningError` | parametrized × 4 |
| Risk veto 활성 시 BUY → HOLD + 로그 carry | `TestRiskVetoE2E` |
| Frontend BUY 라벨이 `<strong>` (button 0개) | `BUY label is a span` |
| Frontend 금지 라벨 0건 | `zero forbidden order labels` |

## 6. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order 호출 0건 (E2E 흐름 전체에서 spy 카운트 0)
- ✅ Anthropic / OpenAI / httpx / requests import 추가 0건
- ✅ DB write 추가 0건 — 본 PR 은 테스트 + 문서만
- ✅ `ENABLE_LIVE_TRADING=false` / `ENABLE_AI_EXECUTION=false` /
  `ENABLE_FUTURES_LIVE_TRADING=false` / `KIS_IS_PAPER=true` defaults 그대로
- ✅ secret 추가 / `.env` 변경 0건
- ✅ Frontend "지금 매수" / "Place Order" / "ENABLE_*" 라벨 button 0개

## 7. 본 PR 의 작업 범위

- 신규 테스트 1개 (backend): `backend/tests/test_ai_paper_e2e.py` (17 cases)
- 기존 테스트 1개 확장 (frontend): `AutoPaperLoopCard.test.jsx` (+4 cases)
- 신규 문서 1개: `docs/ai_paper_e2e_test.md` + README 링크
- **운영 코드 변경 0건** — broker / OrderExecutor / route_order / Strategy /
  RiskManager / Alembic migration / `.env.example` 모두 그대로
