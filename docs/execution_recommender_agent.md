# Execution Recommender Agent (#56)

본 문서는 [`ExecutionRecommenderAgent`](../backend/app/agents/execution_recommender.py)의 정책 contract를 정의한다. AI Assist 흐름의 *핵심* — 매수 / 매도 *제안*만 만들고, 직접 주문하지 않는다.

**본 Agent는 어떤 경우에도 `BrokerAdapter` / `OrderExecutor` / `route_order` / `place_order` / `cancel_order`를 직접 호출하지 않는다.** 큐 등록은 기존 sanctioned `app.ai.assist.submit_candidate` (#44)에 위임 — 그 함수가 `route_order` → `RiskManager` → `audit` → `PermissionGate.submit`을 단일 진입점에서 처리한다.

## 1. 목적

AI 매수/매도 *전환의 중간 단계*로, 운영자의 의사결정 부담을 완화한다:

- AI가 raw 후보(symbol/side/confidence/근거)를 만든다.
- 본 Agent가 후보 → ExecutionProposal로 변환 + 임계 필터 적용.
- 운영자가 본 카드에서 *명시적*으로:
  - "위험 사전검사" — RiskManager dry-run (audit row 0건)
  - "승인 대기 후보로 보내기" — 기존 결재 큐로 전달
- 결재 시점에 `PermissionGate.approve`가 RiskManager 재검증 후 실 broker 호출.

## 2. 핵심 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| 매수/매도 *제안*만 생성 | `ExecutionProposal` 데이터클래스 (주문 객체 X) |
| 직접 주문 금지 | `is_order_intent=False` / `can_execute_order=False` 불변 (`__post_init__` ValueError) |
| BrokerAdapter import 금지 | 정적 grep 가드 — `from app.brokers.kis` / `from app.brokers.mock_broker` / `from app.brokers.base import OrderRequest|BrokerAdapter` 0건 |
| OrderExecutor import 금지 | `from app.execution.executor` / `from app.execution.order_executor` 0건 |
| route_order 직접 호출 금지 | `from app.execution.order_router` import 0건; `await route_order(` / `= route_order(` 0건 |
| place_order / cancel_order 호출 금지 | `broker.place_order(` / `broker.cancel_order(` / `await broker.place_order` / `await broker.cancel_order` substring 0건 |
| approval queue 직접 INSERT 금지 | DB write 0건 (큐 등록은 ai.assist.submit_candidate가 처리) |
| 외부 AI / HTTP 호출 금지 | anthropic / openai / httpx / requests / urllib3 import 0건 |

위 invariant는 [`tests/test_execution_recommender.py`](../backend/tests/test_execution_recommender.py) `TestAgentStaticGuards` / `TestRoutesStaticGuards`로 강제.

## 3. ExecutionProposal 구조

`ExecutionProposal`은 *주문 요청 객체가 아니다* — 별도 frozen dataclass:

```python
@dataclass(frozen=True)
class ExecutionProposal:
    proposal_id:        str        # uuid hex — agent가 발급
    symbol:             str
    side:               ProposalSide  # BUY / SELL
    quantity:           int
    confidence:         int        # 0-100
    expires_at:         datetime   # 본 제안 유효 기간
    order_type:         ProposalOrderType = MARKET
    limit_price:        int | None = None
    target_price:       int | None = None
    stop_price:         int | None = None
    quality_score:      int | None
    supporting_reasons: tuple[str, ...]
    opposing_reasons:   tuple[str, ...]
    risk_note:          str | None
    expected_reward:    int | None       # 추정 수익 (KRW)
    expected_risk:      int | None       # 추정 손실 (KRW)
    risk_reward_ratio:  float | None     # reward / risk
    strategy:           str | None = "ai_assist:execution_recommender"
    model:              str | None
    analysis_log_id:    int | None
    market_regime:      str | None       # MarketObserver(#52) carry
    created_at:         datetime
    is_order_intent:    bool = False     # *항상 False* (가드)
    can_execute_order:  bool = False     # *항상 False* (가드)
```

`__post_init__`에서 `is_order_intent=True` 또는 `can_execute_order=True` 설정 시 즉시 `ValueError` — 어떤 PR도 invariant를 우회하지 못한다.

## 4. RiskManager 사전검사 (precheck)

`precheck_proposal(proposal, *, risk, broker, mode, requested_by_ai=True)`는:

1. 제안 만료 여부 확인 — 만료면 `REJECTED` 즉시 반환 (broker 호출 없이).
2. broker로부터 시세 / 잔고 / 포지션 *read-only* 스냅샷 수집 (`get_price` / `get_balance` / `get_positions`).
3. `RiskContext`를 생성하고 `risk.check_order(...)` 호출.
4. **audit row를 작성하지 않는다** — `check_order`는 `evaluate_order`만 위임하며 `OrderAuditLog` row를 만들지 않는다 (정적 grep 가드 + 통합 테스트로 검증).

반환 `RiskPrecheckResult`:
- `outcome`: `APPROVED` / `NEEDS_APPROVAL` / `REJECTED` / `BLOCKED` / `REDUCED`
- `reasons`, `warnings`, `risk_score`, `blocked_by`, `required_action`
- `proposal_id` carry

운영자는 이 결과를 보고 "큐 등록 가치가 있나"를 판단. 사전검사 통과는 *큐 등록 시 통과를 보장하지 않는다* — 결재 시점에 broker 상태가 다르면 RiskManager가 다시 거부할 수 있다 (#070 invariant 그대로).

## 5. Approval Queue 연계 (submit)

`submit_proposal(proposal, *, risk, broker, db, mode, enable_*_flags)`는 *완전히* `app.ai.assist.submit_candidate`에 위임:

1. 만료 제안은 `RuntimeError` raise — 큐에 등록하지 않음.
2. `ExecutionProposal.to_ai_candidate()` → `AICandidate` 변환.
3. `submit_candidate(candidate=...)` 호출:
   - AI Permission Gate (#39) 평가 — 차단 시 `AiAssistPermissionDeniedError`.
   - `route_order(requested_by_ai=True, mode=LIVE_AI_ASSIST)` 호출.
   - RiskManager → `OrderAuditLog` (audit 작성, 단계는 `NEEDS_APPROVAL` / `REJECTED` / 등).
   - `NEEDS_APPROVAL`이면 `PermissionGate.submit` → `PendingApproval` row 생성.
4. `AiAssistSubmissionResult` 반환 — `routing.decision`, `audit.id`, `approval.id`.

본 모듈은 `route_order` / `OrderExecutor` / broker class를 *직접* import하지 *않으며*, runtime에 `submit_candidate`만 호출한다. 그 함수가 sanctioned 흐름을 단일 트랜잭션으로 처리.

## 6. 직접 주문 금지 보장 방식

다층 가드:

1. **dataclass `__post_init__`** — `is_order_intent=True` / `can_execute_order=True` 시 ValueError.
2. **enum 가드** — `PrecheckOutcome` enum에 BUY/SELL/HOLD 값 0개 (테스트로 lock).
3. **모듈 import grep** — 본 모듈에서 broker / executor / order_router / 외부 HTTP / AI SDK import 0건.
4. **모듈 호출 grep** — `broker.place_order(` / `broker.cancel_order(` / `await broker.place_order` / `await broker.cancel_order` / `route_order(` 호출 0건.
5. **routes 모듈 grep** — 본 PR에서 추가한 `routes_execution_recommender.py`도 동일한 가드 적용.
6. **OrderRequest 미참조** — 본 모듈은 `OrderRequest`를 import하지 *않으며*, type annotation / 변수 선언 / 직접 생성 0건. 변환은 `AICandidate.to_order_request()` 내부에서만.
7. **AI Permission Gate (#39)** — `LIVE_AI_ASSIST` 외 모드에서 `submit`이 자동 차단 (mode mismatch → 403).
8. **Approval Queue 의존성** — 본 Agent는 `PendingApproval` row를 *직접* 생성하지 않음. `PermissionGate.submit` 호출은 ai.assist 내부에서만.

## 7. UI

[`frontend/src/components/tabs/ExecutionRecommenderCard.jsx`](../frontend/src/components/tabs/ExecutionRecommenderCard.jsx) — Approvals / Dashboard / Agent 탭에 마운트.

**필수 표시**:
- 추천 후보 (proposals) 목록
  - 종목, 방향 (BUY/SELL), 수량, confidence
  - 전략 (`ai_assist:execution_recommender`)
  - expected reward, expected risk, risk/reward ratio
  - 만료 시각
  - 주요 근거 / 반대 근거 / risk_note
- "주문 아님 · 승인 필요" 배지 (prominent)
- disclaimer notice ("주문이 아닙니다 / audit row를 만들지 않습니다 / RiskManager 재검증 필수")
- 임계 미달 (skipped) 리스트 (collapsed)

**버튼** (모두 직접 주문 X):
- "위험 사전검사" — `/api/agents/execution-recommender/precheck` 호출
- "승인 대기 후보로 보내기" — `/api/agents/execution-recommender/submit` 호출

**금지된 UI 요소** (테스트로 lock):
- "매수 실행" / "매도 실행" / "즉시 주문" / "Place Order" / "Submit Order" 버튼
- "주문 발생" / "주문 보내기" / `broker.*order` 라벨

**Approvals 탭**:
- ExecutionProposal에서 온 항목은 audit row의 `trade_reason="ai_assist"` + `ai_decision_meta.source="AI_ASSIST"`로 표시 — 기존 `routes_approvals._derive_request_source`가 자동으로 "AI 제안" 라벨 surface.

## 8. PR / 승인 흐름

```
운영자가 본 카드에서:
  1. POST /recommend (input: candidates) → 제안 목록 반환
  2. 제안 1건에 대해 "위험 사전검사" 클릭 → POST /precheck → outcome 표시
  3. 결과가 APPROVED / NEEDS_APPROVAL이면 "승인 대기 후보로 보내기" 클릭
     → POST /submit (LIVE_AI_ASSIST 모드에서만)
       → AI Permission Gate(#39) 평가 → 차단 시 403
       → ai.assist.submit_candidate
         → route_order(requested_by_ai=True)
           → RiskManager.evaluate_order
             → OrderAuditLog row 작성 (audit 진실)
             → NEEDS_APPROVAL → PendingApproval row 생성
       → 응답에 approval_id 포함
  4. 운영자가 결재 탭에서 PendingApproval 검토
     → PermissionGate.approve → RiskManager 재검증 → OrderExecutor → broker.place_order
       (실 주문은 *여기서만* 발생)
```

LIVE_AI_EXECUTION 모드는 본 PR 시점 default OFF — 운영자 승인 없이 자동 실행되는 경로는 어디에도 없다.

## 9. API surface

| Endpoint | 메서드 | 의미 | DB write | broker 호출 |
|---|---|---|---|---|
| `/api/agents/execution-recommender/recommend` | POST | candidates → ExecutionProposal 목록 | 0건 | 0건 |
| `/api/agents/execution-recommender/precheck` | POST | RiskManager 사전검사 (audit row X) | 0건 | read-only (시세/잔고/포지션) |
| `/api/agents/execution-recommender/submit` | POST | ai.assist.submit_candidate에 위임 | OrderAuditLog + (조건부) PendingApproval | route_order 경유, 본 PR에서 broker.place_order 비활성 (LIVE_AI_ASSIST default OFF) |

`/submit` 응답 status code:
- 200: routing.decision 모두 (NEEDS_APPROVAL / REJECTED / BLOCKED / APPROVED)
- 403: AI Permission Gate 차단 또는 mode != LIVE_AI_ASSIST
- 410: 제안 만료
- 422: payload validation 실패 (FastAPI)

## 10. 한계 / 다음 단계

본 Agent의 출력을 단독 결정 근거로 쓰면 안 되는 이유:

| 위험 | 영향 |
|---|---|
| AI 신뢰도 calibration 오류 | confidence가 실제 정확도를 반영 못 할 수 있음 |
| Expected reward / risk 추정 오차 | target / stop은 *희망*이지 *예측*이 아니다 |
| Regime change | 추천 시점의 시장이 결재 시점에 변할 수 있음 |
| Slippage / 부분체결 | 사전검사는 단순 quote 기반 — 실 체결 미세구조 미반영 |
| Stale 시세 | 시세 timestamp가 stale이면 RiskManager가 거부 |

**다음 단계 backlog (별도 PR)**:
- 실 LLM 통합 — 현재는 결정론적 규칙 기반. anthropic SDK로 자연어 근거 생성.
- 다중 candidate 비교 / ranking — 같은 종목에서 여러 전략 결과를 통합.
- 시장 regime carry — MarketObserver(#52) 출력을 입력에 자동 주입.
- 자동 expire 정리 — 만료된 제안을 dashboard에서 dim out 처리.
- AgentDecisionLog 통합 — 본 Agent의 출력을 #51 audit trail에 기록.
- LIVE_AI_EXECUTION 모드 — 본 PR 시점 default OFF. 운영자 명시 옵트인 후 별도 PR (`promotion_policy.md` 8개 조건 필수).

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51)
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`ai_assisted_trading_policy.md`](ai_assisted_trading_policy.md) — AI Assist 흐름 (#44)
- [`ai_permission_gate.md`](ai_permission_gate.md) — AI 권한 단계 (#39)
- [`ai_execution_policy.md`](ai_execution_policy.md) — LIVE_AI_EXECUTION 안전 게이트 (#45)
- [`risk_manager_contract.md`](risk_manager_contract.md) — RiskManager 표준 진입점 (#34)
- [`order_executor_contract.md`](order_executor_contract.md) — broker 단일 호출 진입점 (#40)
- [`manual_approval_policy.md`](manual_approval_policy.md) — Manual approval (#41)
- `app/agents/execution_recommender.py` — 본 Agent 구현
- `app/api/routes_execution_recommender.py` — API endpoints
- `frontend/src/components/tabs/ExecutionRecommenderCard.jsx` — UI
- `CLAUDE.md` — 절대 원칙 1, 2 (AI 직접 호출 금지 + RiskManager → PermissionGate → OrderExecutor 순서)
