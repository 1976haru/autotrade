# AI Agent ↔ Paper Auto Loop 연결 (Paper Candidate Wire)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 흐름은 *advisory* — 운영자 승인 후에도 실거래 활성화 0건. Paper 가상
> 체결 + AgentDecisionLog 기록만.

## 1. 목적

3-15 `final_paper_candidates.select_paper_candidates()` 가 만든 후보 1~3개를
**운영자 승인을 거쳐** AI Agent → Paper Auto Loop → PaperDecision → Ledger /
AgentDecisionLog 흐름에 연결한다.

자동 연결 0건 — 후보가 있어도 `approve()` 호출 전까지는 어떤 후보도 Paper
Auto Loop 에 사용되지 않는다. 위험 라벨 carry 후보는 승인 자체가 차단된다.

## 2. 흐름

```
[3-15] FinalCandidateReport.candidates (PaperCandidate list)
   │
   │  candidate_registry.load_candidates(report) — 모두 PENDING_APPROVAL
   ▼
[Registry] in-memory CandidateRegistry
   │
   │  운영자 → POST /api/auto-paper/candidates/{id}/approve-paper
   │  → ApprovalStatus.APPROVED (HIGH_RISK / BLOCK / OVERFIT_RISK 차단)
   ▼
[active_candidate] = 최근 APPROVED 중 rank 가장 낮은 후보
   │
   │  AutoPaperLoop.tick() — consumer runner
   │  → build_candidate_provider() — PaperStartExplanation 생성
   ▼
[bridge_explanation_to_paper_decisions]
   │   ↓ Risk veto (4-09) ↓ Position sizing (4-08)
   │
   ▼
[PaperDecision] → ledger (#2-09) → AgentDecisionLog (#4-10)
                                  → AutoPaperStatus 의 last_* 필드 carry
                                  → UI 의 consumer strip / candidate banner
```

## 3. 4 가지 Readiness state

`ReadinessState` enum — AutoPaperState 6-state 모델과 **별개** (metadata 로 carry):

| State | 조건 |
|---|---|
| `NO_CANDIDATE` | registry 가 비어있거나 모두 REJECTED |
| `WAITING_APPROVAL` | PENDING 후보 ≥ 1, APPROVED 후보 0 |
| `CANDIDATE_READY` | APPROVED 후보 ≥ 1 (active_candidate 사용 가능) |

`AutoPaperStatus.candidate_readiness` + `has_active_candidate` 로 carry. UI
가 polling 으로 갱신.

## 4. 구현 파일

| 파일 | 의미 |
|---|---|
| `backend/app/auto_paper/candidate_registry.py` | `ApprovalStatus` / `ReadinessState` / `ApprovalBlockedError` / `ManagedCandidate` / `CandidateRegistry` (in-memory thread-safe) |
| `backend/app/auto_paper/candidate_provider.py` | `build_candidate_provider()` — registry → consumer recommendation_provider 어댑터 |
| `backend/app/api/routes_auto_paper.py` | 4 신규 endpoint — GET candidates / approve-paper / reject / active-candidate |
| `backend/app/auto_paper/loop.py` | `AutoPaperStatus` 에 `candidate_readiness` + `has_active_candidate` 추가 |
| `backend/tests/test_auto_paper_candidate_loader.py` | 27 backend tests (registry + provider + tick + API + static guard) |
| `frontend/src/components/tabs/PaperCandidateApprovalCard.jsx` | 운영자 승인 UI — 후보 row + 승인/거절 버튼 + 영구 disclaimer |
| `frontend/src/components/tabs/PaperCandidateApprovalCard.test.jsx` | 13 frontend tests |
| `frontend/src/services/backend/client.js` | 4 신규 client 메서드 |
| `docs/paper_candidate_wire.md` | 본 정책 |

## 5. REST API

### `GET /api/auto-paper/candidates`

전체 registry 상태 — 후보 list + readiness_state + 카운트.

```jsonc
{
  "schema_version": "1.0",
  "readiness_state": "WAITING_APPROVAL",
  "total": 2,
  "pending": 2,
  "approved": 0,
  "rejected": 0,
  "candidates": [
    {
      "candidate_id": "MOMENTUM::005930::rank1",
      "candidate": { /* PaperCandidate 전체 */ },
      "status": "PENDING_APPROVAL",
      "approved_by": null,
      "approved_at": null,
      "is_order_signal": false,
      "auto_apply_allowed": false,
      "is_live_authorization": false
    }, ...
  ],
  "active_candidate_id": null,
  "is_order_signal": false,
  "auto_apply_allowed": false,
  "is_live_authorization": false
}
```

### `POST /api/auto-paper/candidates/{candidate_id}/approve-paper`

Body: `{ "approved_by": "operator-id", "note": "optional" }`

응답: 승인된 `ManagedCandidate` (status="APPROVED") + 영구 invariant.

거절 케이스:
- `404 candidate_not_found` — 존재하지 않는 id
- `409 approval_blocked_risk` — `risk_flags` 또는 verdict 에 HIGH_RISK / BLOCK / OVERFIT_RISK / STRESS_FAILED / BLOCKED_REGIME / FAIL 라벨 carry
- `409 approval_state_conflict` — 이미 REJECTED 상태

### `POST /api/auto-paper/candidates/{candidate_id}/reject`

Body: `{ "rejected_by": "operator-id", "note": "optional" }`

응답: 거절된 `ManagedCandidate` (status="REJECTED"). 이미 APPROVED 인 후보
는 409.

### `GET /api/auto-paper/active-candidate`

```jsonc
{
  "has_active": true,
  "readiness_state": "CANDIDATE_READY",
  "active": { /* ManagedCandidate */ },
  "is_order_signal": false,
  ...
}
```

## 6. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `ManagedCandidate.is_order_signal / auto_apply_allowed / is_live_authorization=False` 영구 | `__post_init__` ValueError |
| `ManagedCandidate.candidate.requires_operator_approval=True` 영구 (False carry 거부) | 위 |
| `approve()` 차단: HIGH_RISK / BLOCK / OVERFIT_RISK / STRESS_FAILED / BLOCKED_REGIME / FAIL | `ApprovalBlockedError` |
| approve → reject 또는 reject → approve 전이 금지 | RuntimeError |
| 후보 없으면 readiness = NO_CANDIDATE → active_candidate=None | `test_no_candidate_when_registry_empty` |
| 승인 0개면 readiness = WAITING_APPROVAL → provider None | `TestReadinessState` |
| 승인 1+개면 readiness = CANDIDATE_READY → active_candidate 반환 | 위 |
| `build_candidate_provider()` 가 None 일 때 consumer 가 0 decision | `TestEndToEnd.test_no_decisions_when_no_candidate` |
| 승인 후 tick → BUY + ledger row + AgentDecisionLog row (mode=PAPER) | `test_decisions_after_approve` |
| `AutoPaperStatus.candidate_readiness` / `has_active_candidate` carry | `TestStatusSnapshot` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` (registry + provider) |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| DB write 0건 — registry 는 in-memory | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| API: 404 on unknown id, 409 on risk block, 409 on state conflict | `TestApiEndpoints` (8 cases) |
| Frontend: 영구 "승인 후 Paper에서만 사용" + "실거래 활성화 아님" 배지 | `PaperCandidateApprovalCard.test.jsx` |
| Frontend: "지금 매수" / "Place Order" / "Live 활성화" / "ENABLE_LIVE_TRADING=true" 라벨 0건 | 위 |
| Frontend: 승인 버튼 라벨이 "Paper 승인" — "Live" 단어 0건 | 위 |
| Frontend: text 입력 form 0개 | 위 |

## 7. UI (`PaperCandidateApprovalCard`)

- 영구 배지: "승인 후 Paper에서만 사용" + "실거래 활성화 아님"
- readiness pill: "Paper 후보 없음" / "승인 대기" / "승인된 후보 있음"
- 후보 row 별: status 배지 / name / symbol / regime / composite_score /
  매매기법군 / 전략 / 추천 사유 top 3 / risk_flags chips
- 승인 / 거절 버튼 — `defaultOperatorId` 자동 전달
- APPROVED row: 승인자 / 시각 표시 (버튼 미노출)
- REJECTED row: 거절자 / 시각 표시 (버튼 미노출)
- 영구 footer note — `is_order_signal=false / auto_apply_allowed=false /
  is_live_authorization=false` 텍스트
- "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" / "Live 활성화" /
  "ENABLE_LIVE_TRADING" 라벨 button 0개 (테스트로 lock)
- secret 입력 form 0개

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — registry 는 *in-memory* (재기동 시 다시 승인 필요 — 감사 보존)
- ✅ `is_live_authorization=False` 영구 — 승인된 후보도 실거래 허가 아님
- ✅ `requires_operator_approval=True` 영구 carry
- ✅ HIGH_RISK / BLOCK / OVERFIT_RISK / STRESS_FAILED 후보 승인 자체 차단
- ✅ AutoPaperState 6-state 모델 변경 0건 — readiness 는 별도 metadata
- ✅ 안전 flag default 변경 0건

## 9. 후속 PR 권고

- **registry 영구화** — 본 PR 은 in-memory. 운영자 검토 흐름이 자리 잡으면
  별도 PR 로 DB persistence + audit log 추가.
- **AutoPaperLoopCard 통합** — 본 카드 가 별도 위치에 렌더되지만, 후속 PR
  에서 AutoPaperLoopCard 위쪽 / 옆에 자동 노출 (start 버튼 비활성화 조건 -
  `has_active_candidate==false` 일 때 disabled).
- **revoke 옵션** — APPROVED 후보 취소 (operator-only audit 추가).
- **자동 후보 로드** — 운영자 명시 trigger 없이 reports/final_paper 디렉토리
  변경 시 registry 자동 reload (시스템적 polling 권장 — 자동 적용은 *여전히*
  금지).
