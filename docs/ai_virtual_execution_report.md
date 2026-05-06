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

## 164: AI proposal auto-idempotency

`VirtualAiAgent.propose_and_route()`이 `client_order_id`를 안 받으면 자동으로 UUID v4 prefix `ai-`을 붙여 생성한다 (`f"ai-{uuid4()}"`). 호출자가 잊거나 외부 LLM 통합 흐름에서 빠져도 140 idempotency 가드가 항상 작동.

- 명시적 `client_order_id`는 그대로 유지 (회귀 가드).
- 같은 proposal로 두 번 호출해도 auto-gen UUID가 매번 다르므로 dup 차단 X (호출자가 *의도적으로* 같은 cid를 명시한 경우만 차단).
- audit row의 `client_order_id` 컬럼이 `ai-` prefix로 채워져 사후 분석 시 'AI 자동 생성 vs 운영자 명시' 구분 가능.

**효과**: AI 에이전트가 재시작 / network retry / 호출자 버그로 같은 proposal을 두 번 라우팅해도, 첫 호출은 auto cid A로 통과하고 두 번째 호출은 auto cid B로 통과 — 진짜 중복은 명시적 cid를 사용한 경우만. 운영자가 의도적 idempotency를 원하면 cid를 직접 보낸다 (예: 실 LLM 응답에 unique signal_id가 있을 때 그것을 cid로 사용).

## 163: AI agent feedback loop

지능형 에이전트의 self-correction. 과거 AI 발신 거래의 PnL을 144 FIFO
페어매칭으로 산출하고, win_rate에 따라 다음 제안의 confidence를 보정한다.

**산식** (`app/ai/feedback.py::_factor_from_win_rate`):

| win_rate | factor |
|---|---:|
| < 0.4 | 0.5 (절반 깎음) |
| 0.4–0.5 | 0.7 |
| 0.5–0.6 | 1.0 (변화 없음) |
| 0.6–0.7 | 1.1 |
| ≥ 0.7 | 1.2 (boost) |

표본이 `MIN_SAMPLE_TRADES=10` 미만이면 factor=1.0 (보수적 — 적은 표본에 의한 잘못된 보정 방지).

**흐름**:
```text
agent.propose_stub(...) → AiProposal {confidence: raw}
        │
        ▼ agent.calibrate_with_feedback(proposal, db)
        │   ├ compute_historical_accuracy(db, strategy)
        │   │   - requested_by_ai=True + executed=True audit row만
        │   │   - symbol별 FIFO BUY/SELL 페어매칭 → wins/losses
        │   │   - lookback_days 기본 30일
        │   ├ adjust_confidence(raw, factor) → clamped [0, 100]
        │   └ extra_meta에 raw_confidence/historical_factor/trades/win_rate 보존
        │
        ▼ AiProposal {confidence: adjusted, extra_meta: {...}}
        │
        ▼ propose_and_route → audit row의 ai_decision_meta에 보정 이력 영구화
```

**Closed loop**:
1. 에이전트가 raw confidence 70인 BUY 제안.
2. 같은 strategy의 historical win_rate가 0.3 → factor 0.5 → adjusted confidence 35.
3. RiskPolicy.min_ai_confidence=50이면 158 가드가 거부. 운영자에게 audit log surface.
4. 손실이 누적되면 자동으로 confidence가 낮아져 추가 진입 차단 — 손실 방어 자동화.

**read-only**: `compute_historical_accuracy`는 audit row를 수정 안 함. `adjust_confidence`는 새 proposal 객체 반환 (immutable patten).

## 162 + 165: AI agent self-evaluation stats

운영자가 AI 에이전트의 의사결정 품질을 audit log 기반으로 평가할 수 있는 read-only 분석. 결정/체결에 영향 X.

- HTTP: `GET /api/ai/agent-stats?lookback_days=7` — `lookback_days=0`이면 전체 기간.
- Backend: `app/ai/agent_stats.py::compute_ai_agent_stats(db, lookback_days, now)`.

응답 shape (165 확장 포함):
```json
{
  "lookback_days": 7,
  "total_proposals": 100,
  "approved": 80, "rejected": 15, "needs_approval": 5,
  "approval_rate": 0.842,        // approved / (approved + rejected)
  "avg_confidence": 76.3,        // executed=True + confidence!=null만
  "top_rejection_reasons": {
    "low_confidence": 8,
    "emergency_stop": 4,
    "rate_limit": 3
  },
  "per_strategy": [
    {"strategy": "ai_virtual", "total": 100, "approved": 80, ...,
     "approval_rate": 0.842, "avg_confidence": 76.3,
     "wins": 12, "losses": 5, "realized_pnl": 8400}  // 165: FIFO 매칭
  ],
  // 165 신규: confidence 분포 히스토그램.
  "confidence_histogram": {
    "0-25":   2,
    "25-50":  10,
    "50-75":  45,
    "75-100": 43
  },
  "confidence_histogram_missing": 0
}
```

**165 추가**:
- `confidence_histogram`: 4개 bucket(0-25/25-50/50-75/75-100). lower-bound inclusive. confidence 분포가 한쪽으로 치우쳐 있으면 calibration 문제 신호.
- `confidence_histogram_missing`: confidence=None인 row 수 (159 reasoning enforcement 활성 후엔 0이어야 정상).
- `per_strategy[i].wins / losses / realized_pnl`: 163 `compute_historical_accuracy`를 같은 윈도우에서 재사용. AI 전략의 실제 PnL을 FIFO 페어매칭으로 산출.

운영자 활용:
- approval_rate가 갑자기 떨어졌다 → strategy / market regime 변화 신호.
- top_rejection_reasons.low_confidence가 dominant → 158 임계 조정 검토.
- top_rejection_reasons.rate_limit이 dominant → 161 max_count 조정 또는 에이전트 buggy.

## 161: AI proposal rate limit

LLM bug / 무한 루프 / 동일 신호 spam 등으로 AI 에이전트가 같은 (strategy,
symbol)에 대해 짧은 시간 내 다수 제안을 만드는 사고를 audit log를 walk해
사전 차단.

- `RiskPolicy.ai_rate_limit_max_count`(기본 0=비활성) + `ai_rate_limit_window_seconds`(기본 60s).
- `route_order` Step 0.5 (idempotency 다음, broker 호출 전): `requested_by_ai=True` + `max_count > 0`이면 `count_recent_ai_proposals`로 누적 카운트 검사.
- 임계 도달 시 RiskManager 결과를 REJECTED로 덮고 reason `"AI rate limit exceeded: N proposals in Ws window..."` 누적.
- (strategy, symbol)별 격리 — 한 strategy 한도가 다른 strategy에 영향 X.
- non-AI 주문은 검사 우회 (회귀 가드).

기본 비활성인 이유: 운영자 의도 없이 stress test나 정상 흐름이 차단되지 않도록. 옵트인 권장값:
- 개발 / 검증: 비활성 (기본).
- VIRTUAL_AI_EXECUTION 운영: 60s window / 10 max (실제 시장 대비 과한 빈도 차단).
- LIVE_AI_EXECUTION 활성화 시: 60s / 5 max (보수적).

## 159: AI proposal reasoning required

CLAUDE.md '감사 로그 우선' invariant의 AI 영역 강제. `RiskPolicy.enforce_ai_reasoning` (기본 True)일 때 `requested_by_ai=True`인 주문이 `ai_decision_meta`가 None이거나 `reasons`가 비어있으면 RiskManager가 REJECTED. AI 외 경로 주문(운영자 수동 / strategy 신호)은 영향 받지 않음.

목적:
- 미래 에이전트(LiveAiAgent / 3rd-party)가 reasoning 없이 주문 만드는 것 사전 차단.
- 모든 AI 주문 audit row가 사후 분석 가능한 sufficient context를 가진다는 invariant.

운영 가이드: LIVE 단계에서는 절대 `enforce_ai_reasoning=False`로 끄지 말 것. 개발 단계에서 backwards-compat 위해 일시적 끔만 허용 (`docs/promotion_policy.md` LIVE 단계 가드).

`VirtualAiAgent.AiProposal.to_order_request()`는 자동으로 `reasons` 채우므로 정상 흐름엔 영향 없음.

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
