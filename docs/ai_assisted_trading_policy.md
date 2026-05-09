# AI Assisted Trading 정책 (#44)

본 문서는 `LIVE_AI_ASSIST` 운용모드의 흐름·invariant·역할분담을 정의합니다. CLAUDE.md 절대 원칙(특히 1/2/5/6) + `ai_permission_gate.md`(#39) + `manual_approval_policy.md`(#41) 위에서 동작하는 *AI 제안 + 사람 승인* 단계입니다.

## 1. 목적

- 사용자(운영자)의 의사결정 부담을 줄이되, 모든 주문은 사람의 승인을 거치도록 한다.
- AI는 분석·후보 제안에 한정되며, broker 호출 권한이 *없다*.
- LIVE_AI_EXECUTION으로 가기 전 중간 단계 — Shadow / Paper에서 검증된 AI가 실시세에서 어떤 주문을 *제안*할지 운영자가 사후 검토할 수 있다.

## 2. 전체 흐름

```
AI 분석 / 운영자 입력
    ↓
AICandidate (app/ai/assist.py)
    ↓
AiPermissionGate.evaluate_ai_permission(SUBMIT_FOR_APPROVAL)   ← #39
    ↓ allowed
route_order(requested_by_ai=True, mode=LIVE_AI_ASSIST)
    ↓
RiskManager.evaluate_order  (사전검사: 한도/긴급정지/시세 stale/일일손실 등)
    ↓
   ┌─ REJECTED          → audit row 작성, approval queue 등록 X (UI에 거부 사유 surface)
   └─ NEEDS_APPROVAL    → audit row + PendingApproval row 등록
                        → 운영자가 결재 탭에서 승인/거절
                            ↓ 승인
                          PermissionGate.approve  (승인 시점 RiskManager 재검증)
                            ↓ 통과
                          OrderExecutor → BrokerAdapter.place_order
```

각 단계는 *별개의* 가드 — 한 단계가 깨져도 다음 단계가 차단합니다 (defense in depth).

## 3. 역할 분담

### AI의 역할 (할 수 있는 것)

- 매수/매도 *후보* 생성 — symbol, side, quantity, confidence, supporting_reasons, opposing_reasons, risk_note, target_price, stop_price.
- 본인의 reasoning을 `ai_decision_meta` JSON으로 carry — 운영자가 결재 카드에서 양면(찬성/반대)을 함께 본다.
- `analysis_log_id`로 원본 분석 row를 cross-reference — 결재 카드에서 AI 분석 원문으로 drill-down 가능.

### AI가 *못 하는* 것

- broker / OrderExecutor / route_order 직접 호출 — `app.ai.assist`는 `route_order` *함수*만 import (broker / OrderExecutor 인스턴스를 절대 생성하지 않음). 정적 grep 가드로 강제: `tests/test_ai_assist.py::test_assist_module_does_not_import_broker_or_executor`.
- approval row 직접 insert — 항상 RiskManager 사전검사를 거쳐야만 큐에 들어감.
- API key / 계좌번호 / Secret을 인자로 받기 — `AiPermissionFlags`는 boolean flag만 받음 (#39 invariant).

### 운영자(사람)의 역할

- 결재 탭에서 AI 제안 검토 — 양면 reason / risk_note / confidence / 해당 종목 현재 시세를 한눈에 본다.
- **승인** → `PermissionGate.approve` → 승인 시점 RiskManager 재검증 → broker로 진행.
- **거절** → 큐에서 제거, audit에 사유 기록.
- **취소** → 신호가 stale해진 경우 중립적으로 dismiss.
- 긴급정지 / `disable_ai_orders` flag로 AI 흐름 자체를 차단할 수 있음.

## 4. RiskManager 사전검사

AI 제안은 broker 호출 *전*에 두 번의 RiskManager 검사를 거칩니다:

### A. submit 시점 (route_order 내부)

`risk_manager.py`의 모든 26+ rule을 적용 — 1회 주문 한도, 종목별 노출, 일일/주간 손실, 시세 stale, 긴급정지, AI rate limit 등. `LIVE_AI_ASSIST` 모드는 정상 통과 시 자동으로 `NEEDS_APPROVAL`로 변환됩니다 (`risk_manager.py:465-479`).

- **REJECTED**가 나오면 audit row만 남고 approval queue 등록 X — UI는 “AI 제안 거부됨 / RiskManager 사유”를 surface하지만 운영자가 승인/거절할 큐 항목은 없음.

### B. approve 시점 (PermissionGate.approve)

`permission/gate.py:201-235`. submit 이후 broker 상태가 변할 수 있으므로 (가격 이동, 다른 주문으로 잔고 소진, 긴급정지 토글, ENABLE_LIVE_TRADING flag flip) approve 직전에 *현재* 상태로 재검사합니다. 실패 시 `ApprovalRiskCheckFailedError` — approval은 `PENDING` 그대로 + attempts 배열에 사유 누적 → 운영자가 조건이 회복되면 재시도.

- AI invariant(`requested_by_ai=True`)도 approve 시점에 다시 적용 — `enable_ai_execution` flag, AI rate limit 등이 다시 검사됩니다.

## 5. AICandidate ↔ OrderRequest 매핑

| AICandidate 필드 | OrderRequest / audit 매핑 |
|---|---|
| `symbol`, `side`, `quantity`, `order_type`, `limit_price` | OrderRequest 동일 필드 |
| `confidence` | `OrderRequest.signal_confidence` (0-100 클램프) |
| `quality_score` | `OrderRequest.signal_strength` (0-100 클램프) |
| `supporting_reasons`, `opposing_reasons`, `risk_note` | `ai_decision_meta` JSON |
| `model`, `analysis_log_id` | `ai_decision_meta` JSON |
| `target_price`, `stop_price` | `ai_decision_meta` JSON (정보성, 자동 OCO 미설정) |
| `strategy` (default `"ai_assist"`) | `OrderRequest.strategy` → audit row strategy 컬럼 |
| (고정) | `OrderRequest.trade_reason = "ai_assist"` |
| (고정) | `route_order(requested_by_ai=True)` |
| (고정) | `audit.source = "AI"` (#40) |

`ai_decision_meta.source = "AI_ASSIST"`는 frontend 결재 카드가 AI Assist row를 식별하는 sentinel — `routes_approvals._derive_request_source`가 이를 보고 `request_source=AI`로 라벨링합니다.

## 6. AI Permission Gate (#39) 적용

`AiAction.SUBMIT_FOR_APPROVAL`은 `AiPermissionLevel.APPROVAL_REQUIRED` 이상에서 허용됩니다. LIVE_AI_ASSIST의 default level은 `APPROVAL_REQUIRED` (`ai_permission_gate.py:117`).

다음 조건에선 `AiAssistPermissionDeniedError`가 raise되며, **audit row도 작성되지 않습니다** — AI는 흐름 자체에 진입하지 못함:

- `RiskManager.emergency_stop = True` (kill switch)
- `RiskPolicy.disable_ai_orders = True` (#178 AI-only kill switch)

## 7. LIVE_AI_ASSIST vs LIVE_AI_EXECUTION 차이

| 항목 | LIVE_AI_ASSIST (#44, 본 문서) | LIVE_AI_EXECUTION (별도 옵트인) |
|---|---|---|
| AI 권한 level | `APPROVAL_REQUIRED` | `LIMITED_LIVE_EXECUTION` |
| AI가 broker 호출 | ✗ (사람 승인 후 OrderExecutor가 호출) | ✗ (여전히 OrderExecutor만 호출 — 다만 사람 승인 단계 없음) |
| 결재 큐 등록 | ✓ 모든 AI 제안 | ✗ (승인 자동) |
| 활성화 flag | mode=LIVE_AI_ASSIST + `ENABLE_LIVE_TRADING=true` | + `ENABLE_AI_EXECUTION=true` |
| 기본값 | 비활성 | **비활성 (8개 옵트인 조건, `promotion_policy.md`)** |

## 8. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/ai/assist/submit` | POST | AICandidate 제출 → RiskManager 사전검사 + 큐 등록. 200 (NEEDS_APPROVAL/REJECTED) / 403 (permission/mode) / 409 (duplicate) / 422 (validation) |
| `/api/ai/assist/pending` | GET | 현재 PENDING + AI Assist 출처만. supporting_reasons / opposing_reasons / risk_note 포함 |
| `/api/ai/assist/summary` | GET | Dashboard 카드용 요약 — pending + 24h 통계 |

기존 API contract는 **변경 없음**:

- `/api/approvals` (PendingApproval list) — AI Assist row가 자연스럽게 포함됨, request_source=AI로 라벨링
- `/api/approvals/{id}/approve` / `/reject` / `/cancel` — 동일 contract
- `/api/audit/orders` — `requested_by_ai=True` + `trade_reason=ai_assist` row가 검색 가능

## 9. UI

### Dashboard
- `AiAssistSummaryTile` (4개 타일: 결재 대기 / 24h 승인 / 24h 거부 / 24h 총 제안)
- 결재 대기 타일 클릭 시 결재 탭으로 점프
- "AI 제안만 / 사람 승인 후 주문" 배지 + notice 문구

### AI 탭 (AISignal)
- `AiAssistProposalCard` — symbol/side/quantity/confidence/supporting/opposing/risk_note/target/stop 입력 + "📤 승인 대기 등록" 버튼
- 응답: NEEDS_APPROVAL → 결재 ID 표시 + "결재 탭에서 승인하세요"
- 응답: REJECTED → RiskManager 거부 사유 그대로 표시 + "큐에 등록되지 않음"
- 응답: 403 → AI Permission Gate 차단 사유 (emergency_stop / disable_ai_orders)

### 결재 탭 (Approvals)
- 기존 RequestSourceBadge가 AI 제안에 violet 배지 (`AI 제안`)
- AI Assist row는 추가 `approval-ai-assist-meta` 박스에 supporting_reasons (`+ ...`) / opposing_reasons (`− ...`) / risk_note (`⚠ ...`) 양면 노출
- "AI 제안 — 사람 승인 후에만 broker로 진행됩니다" disclaimer

### **명시 금지** (본 PR 범위 외 + 안전상)
- AI 자동 실행 버튼
- LIVE_AI_EXECUTION 토글
- broker 직접 주문 버튼 (별도 LIVE_MANUAL_APPROVAL UI에서)

## 10. 실전 활성화 전 조건 (체크리스트)

본 PR은 LIVE_AI_ASSIST *흐름 구축*만 다룹니다. 실거래 활성화 전에는:

1. **Shadow / Paper 검증** — LIVE_SHADOW(#43)에서 AI Assist가 만든 후보의 would-have 통계가 안정적인지 확인
2. **수동승인 기간** — 최소 N건의 AI 제안을 운영자가 승인/거절하며 reasoning 품질 검증
3. **audit 누락 0** — 모든 AI 제안이 audit row + 결재 row로 영구화됐는지 reconciliation 통과
4. **손실한도 위반 0** — 일일/주간/연속 손실한도 위반이 audit에 0건
5. **`ENABLE_LIVE_TRADING=true`** — global safety flag (없으면 RiskManager가 `live trading is disabled by global safety flag` reason으로 거부)
6. **AI Permission Gate `APPROVAL_REQUIRED` 이상**으로 자동 강등되지 않은 상태
7. **AI rate limit 정상 동작** — flooding 시 자동 차단되는지 audit log로 확인

## 11. 절대 invariant 요약

| invariant | 보장 |
|---|---|
| AI는 broker.place_order를 직접 호출하지 않는다 | `app/ai/assist.py`는 broker import 0건. `routes_ai_assist`도 0건. `tests/test_ai_assist.py`의 정적 grep 가드 |
| AI 제안은 RiskManager 사전검사를 통과해야 큐 등록 | `route_order`만이 PendingApproval을 만든다 — `submit_candidate`가 직접 큐 insert하지 않음 |
| 승인 시점에도 RiskManager 재검증 | `PermissionGate.approve`가 broker 호출 전 evaluate_order 재실행 (#070 hardening) |
| AI permission gate가 차단하면 audit row도 작성 X | `submit_candidate`가 permission 검사를 *route_order 호출 전*에 수행 |
| `audit.requested_by_ai=True` + `trade_reason=ai_assist`는 immutable | OrderExecutor가 audit row를 수정해도 두 필드는 carry |
| API key는 어떤 함수의 인자도 아니다 | `AiPermissionFlags`는 boolean만 받음 (#39 invariant 상속) |

## 12. 변경 시 동기화

다음 변경은 본 문서 + `CLAUDE.md` + `ai_permission_gate.md` 함께 업데이트:

- `AICandidate` 필드 추가/제거 또는 `ai_decision_meta` 스키마 변경
- `AI_ASSIST_TRADE_REASON` 상수 변경
- 새 AI Permission Gate level 또는 action
- LIVE_AI_ASSIST 정책 게이트 추가/제거 (RiskManager 또는 `submit_candidate`)
- LIVE_AI_EXECUTION 활성화 — 별도 옵트인 PR + 본 문서 §7 표 갱신

## 관련 문서

- [`ai_permission_gate.md`](ai_permission_gate.md) — AI 권한 단계 (#39)
- [`manual_approval_policy.md`](manual_approval_policy.md) — PermissionGate + approve-time recheck (#41)
- [`risk_manager_contract.md`](risk_manager_contract.md) — `check_order` 표준 진입점 (#34)
- [`order_executor_contract.md`](order_executor_contract.md) — broker 단일 호출 진입점 (#40)
- [`promotion_policy.md`](promotion_policy.md) — LIVE_AI_EXECUTION 옵트인 8개 조건
