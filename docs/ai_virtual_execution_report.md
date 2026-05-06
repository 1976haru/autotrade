# Virtual AI Execution Report (152, MUST)

CLAUDE.md 절대 원칙:
- AI는 broker 주문 API를 직접 호출하지 않는다.
- 모든 AI 주문도 RiskManager → PermissionGate → OrderAuditLog를 통과한다.
- 실거래 AI 자동매매 (`LIVE_AI_EXECUTION` + 실 broker 라우팅)는 영구 비활성.

본 문서는 152에서 추가된 가상 AI 실행 환경의 invariant와 모드 비교를 정리한다.

## 새 모드: VIRTUAL_AI_EXECUTION

| Capability | LIVE_AI_EXECUTION | **VIRTUAL_AI_EXECUTION** | LIVE_AI_ASSIST |
|---|---|---|---|
| `real_market_data` | true | **false** (Mock) | true |
| `paper_order` | false | **false** | false |
| `live_order` | true | **false** | true |
| `requires_user_approval` | false | **false** | true |
| `ai_can_recommend` | true | **true** | true |
| `ai_can_execute` | true (flag) | **true (flag 무관)** | false |

핵심:
- `live_order=False` — `can_place_live_order()`가 어떤 flag 조합에서도 라이브 broker 라우팅을 허용하지 않는다. 즉 본 모드는 broker live endpoint를 건드릴 수 없다.
- `ai_can_execute=True` (env flag 무관) — 가상 모드는 정의상 AI 실행이 가능. `can_ai_execute(VIRTUAL_AI_EXECUTION, enable_ai_execution=False)` 도 True를 반환한다.
- `ENABLE_AI_EXECUTION` 플래그는 **LIVE 경로에만 영향**. VIRTUAL 모드와 무관.

## 흐름

```text
VirtualAiAgent.propose_stub(symbol, last_close, prev_close)
        │
        ▼ AiProposal {symbol, side, quantity, confidence, reasons}
        │
        ▼ to_order_request() → OrderRequest {
        │       trade_reason: "ai_recommendation",
        │       strategy: "ai_virtual",
        │       signal_strength/confidence: confidence,
        │       ai_decision_meta: {confidence, reasons, rejected_by_guard}
        │   }
        ▼
VirtualAiAgent.propose_and_route(...)
        │
        ▼ route_order(requested_by_ai=True, mode=VIRTUAL_AI_EXECUTION, ...)
        │       │
        │       ├─ Step 0: client_order_id idempotency 검사 (140)
        │       ├─ Step 1: broker.get_price/balance/positions
        │       ├─ Step 1.5: stale price 검사 (143)
        │       ├─ Daily realized PnL 갱신 (145)
        │       ├─ Step 2-7: notional / cash / positions / exposure / regime
        │       └─ AI 가드 (can_ai_execute) — VIRTUAL은 통과
        │
        ▼ RiskCheckResult
        │   ├ APPROVED → OrderExecutor → audit `executed=True`
        │   ├ NEEDS_APPROVAL → PermissionGate 큐 (LIVE 모드일 때만)
        │   └ REJECTED → audit `executed=False` + reasons
        │
        ▼ OrderAuditLog row {requested_by_ai=True, ai_decision_meta, ...}
```

## 가드 우회 0 — 검증

`backend/tests/test_virtual_ai_execution.py`의 14 테스트가 invariant를 검증:

| 케이스 | 검증 |
|---|---|
| `test_virtual_ai_execution_capability_matrix` | `live_order=False` + `ai_can_execute=True` |
| `test_can_place_live_order_false_for_virtual_ai_execution` | flag 양 조합 모두 False |
| `test_can_ai_execute_true_for_virtual_regardless_of_flag` | flag 양 조합 모두 True |
| `test_can_ai_execute_for_live_still_requires_flag` | LIVE_AI_EXECUTION은 flag 의존 (회귀 가드) |
| `test_virtual_ai_proposal_routes_to_audit_with_ai_meta` | requested_by_ai=True + ai_decision_meta 영구화 |
| `test_virtual_ai_proposal_blocked_by_emergency_stop` | 060 invariant 우회 X |
| `test_virtual_ai_proposal_blocked_by_max_order_notional` | RiskPolicy 한도 우회 X |
| `test_virtual_ai_proposal_in_live_manual_mode_goes_to_approval_queue` | mode가 LIVE_MANUAL이면 NEEDS_APPROVAL — 큐 우회 X |
| `test_virtual_ai_proposal_persists_audit_row_even_on_reject` | 거부도 audit에 남는다 |
| `test_virtual_ai_idempotent_with_client_order_id` | 140 invariant도 AI 경로에 적용 |

## 데이터: ai_decision_meta

`OrderAuditLog.ai_decision_meta` (JSON, nullable, 0010 마이그레이션):

```json
{
  "confidence":         80,
  "reasons":            ["earnings_beat", "regime_match"],
  "rejected_by_guard":  false,
  // 자유 확장 가능 — VirtualAiAgent.AiProposal.extra_meta가 그대로 surface
}
```

NULL은 AI 외 경로 주문 (수동 / strategy 신호). 사후 분석:
- AI 신호 강도 → 실제 PnL 상관관계 추적.
- 거부된 AI 제안의 reason 분포 (RiskManager가 거부한 사유 + AI가 만든 사유).

## 158: AI confidence threshold gate

운영자가 `RiskPolicy.min_ai_confidence`(또는 `MIN_AI_CONFIDENCE` env)를 1-100 사이로 설정하면, `requested_by_ai=True`인 주문은 `signal_confidence` 가 임계 미달일 때 RiskManager가 REJECTED. AI 외 경로 주문은 영향 받지 않음.

- 임계 0 (기본): 검사 비활성 — backwards compat.
- `signal_confidence=None` + 임계 ≥ 1: 거부 (안전 측 — confidence를 모르는 AI 제안은 통과시키지 않음).
- 다른 위반(notional / stale price / daily loss)과 함께 누적 거부 가능 — reasons 모두 surface.

이 가드는 `route_order` / `PermissionGate.approve` 양쪽 모두에서 적용된다 (146 일관성).

## VirtualAiAgent (`backend/app/ai/virtual_agent.py`)

- `propose_stub(symbol, last_close, prev_close, confidence=70)` — 결정적 신호.
  - 종가가 직전 종가보다 높으면 BUY, 낮으면 SELL, 같으면 BUY (테스트 안정성).
- `propose_and_route(proposal, *, mode, broker, risk, db, client_order_id)` — `route_order(requested_by_ai=True)` 경유 → 가드 통과 시 OrderExecutor가 가상 broker에 체결 → audit row + (옵션) PermissionGate 큐.
- 실 LLM (Anthropic / OpenAI) 호출은 본 PR에 없음 — `AiClient.analyze`는 `/api/ai/analyze` 라우트의 read-only 분석에만 사용. 본 에이전트는 결정적 stub.

## 라이브 AI 자동매매 활성화 차단

본 PR에서 `LIVE_AI_EXECUTION` 모드의 동작은 변하지 않았다.
- `can_ai_execute(LIVE_AI_EXECUTION, enable_ai_execution=False)` = `False` (env flag 강제).
- `enable_ai_execution=True`로 켜도 broker live endpoint 라우팅은 별도 PR. KIS adapter LIVE place_order는 여전히 `NotImplementedError` (또는 is_paper guard).
- 따라서 본 PR의 모든 변경은 **가상 환경에 국한**.

활성화에 필요한 변경 매트릭스는 `docs/live_activation_blockers.md` 참조.

## 테스트 통계

- backend 591 → 605 (+14 신규).
- 모든 invariant 테스트 통과. 회귀 0.

## 관련 문서

- [`CLAUDE.md`](../CLAUDE.md) 절대 원칙 1, 2, 5.
- [`docs/risk_policy.md`](risk_policy.md) — RiskManager 평가 순서.
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 시 필요한 변경.
- [`backend/app/ai/virtual_agent.py`](../backend/app/ai/virtual_agent.py) — 본 모듈 소스.
