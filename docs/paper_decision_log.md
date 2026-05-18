# Step 4-10 — Paper AI Decision Log (`agent_decision_log` 재사용)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 로그는 *advisory* — Paper AI 판단의 영구 기록만 carry. 실 broker 호출 0건.

## 1. 목적

AI Paper 자동매매 흐름에서 발생하는 *모든* 판단을 사후 분석할 수 있도록
`agent_decision_log` 테이블 (기존 모델) 의 row 로 영구화한다. "왜 그런 결정을
했는가" 를 시계열로 추적해 운영자가 *판단의 근거 / risk veto 사유 / 가상 수량 /
시장 상황* 을 검증할 수 있다.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/auto_paper/decision_log.py` | writer + query + sanitizer (신규) |
| `backend/app/agents/paper_decision_bridge.py` | bridge 가 `db_session` 입력 시 매 PaperDecision → `AgentDecisionLog` 1줄 INSERT (4-10) |
| `backend/app/api/routes_paper_decision_log.py` | `GET /api/auto-paper/decision-log` read-only |
| `backend/app/db/models.py::AgentDecisionLog` | **기존 테이블 재사용** — 신규 컬럼 0건 (alembic migration 없음) |
| `backend/tests/test_agent_decision_log.py` | 32 backend tests |
| `frontend/src/components/tabs/PaperDecisionLogCard.jsx` | 운영자 read-only UI |
| `frontend/src/components/tabs/PaperDecisionLogCard.test.jsx` | 18 frontend tests |
| `docs/paper_decision_log.md` | 본 정책 |

## 3. 기록 대상

| 흐름 | source | 기록 위치 |
|---|---|---|
| 4-07 PaperDecisionBridge | `bridge_explanation_to_paper_decisions()` | 각 PaperDecision 1줄 |
| 4-08 Position sizing | (carry in PaperDecision.metadata) | 같은 row 의 `meta.sizing_verdict` / `meta.sizing_quantity` |
| 4-09 Risk veto | (carry in PaperDecision.metadata) | 같은 row 의 `meta.risk_veto` / `meta.risk_veto_reasons` / `meta.risk_veto_severity` |
| 4-05 PaperStartExplanation | (carry in bridge_report.explanation_verdict) | 같은 row 의 `meta.explanation_verdict` |
| 4-04 MarketRegime | (carry in explanation.market_regime) | 같은 row 의 `meta.market_regime` |
| 4-03 OverfitWarning | (carry in PaperDecision.metadata.overfit_verdict) | 같은 row 의 `meta.overfit_verdict` / `meta.overfit_flag` |

본 PR 은 *PaperDecision 시점* 만 기록 — 상위 단계의 *개별* row 작성은 후속 PR.

## 4. AgentDecisionLog row 매핑

| AgentDecisionLog 컬럼 | 값 |
|---|---|
| `id` | 자동 |
| `created_at` | 자동 (UTC) |
| `agent_name` | `"PaperDecisionBridge"` (또는 `source_module` 별) |
| `symbol` | `decision.symbol` |
| `mode` | **`"PAPER"`** (영구 — 실거래 로그와 절대 혼동되지 않음) |
| `decision` | `BUY` / `SELL` / `HOLD` / `EXIT` / `NO_OP` |
| `confidence` | `int(decision.confidence * 100)` (0~100) — None 시 NULL |
| `reasons` | `[decision.reason]` (배열) |
| `meta` | JSON: decision_id / paper_order_id / paper_fill_status / virtual_position_delta / pnl_estimate / source_direction / risk_flags / market_regime / explanation_verdict / overfit_verdict / overfit_flag / risk_veto / risk_veto_reasons / risk_veto_severity / sizing_verdict / sizing_quantity / bridge_bucket / paper_candidate_status / `is_order_signal=False` carry |
| `chain_id` | 매 bridge 호출마다 1 UUID — 한 호출의 모든 row 동일 `chain_id` |

## 5. 필수 필드 매핑 (사용자 spec → 본 PR)

| 사용자 spec 필드 | 본 PR 위치 |
|---|---|
| `decision_id` | `meta.decision_id` (UUID4) |
| `timestamp` | `created_at` (DB column) |
| `agent_name` | `agent_name` (DB column) |
| `strategy` | `meta.strategy` (PaperDecision.strategy carry; `meta.bridge_bucket` 도 carry) |
| `symbol` | `symbol` (DB column) |
| `decision_action` | `decision` (DB column — "BUY" 등) |
| `confidence` | `confidence` (DB column, 0~100) |
| `reason` | `reasons[0]` (DB column) |
| `risk_flags` | `meta.risk_flags` (배열) |
| `market_regime` | `meta.market_regime` |
| `overfit_flag` | `meta.overfit_flag` |
| `risk_veto_reasons` | `meta.risk_veto_reasons` (배열) |
| `position_size` | `meta.sizing_quantity` (sized) 또는 `meta.virtual_position_delta` |
| `paper_order_id` | `meta.paper_order_id` |
| `paper_fill_status` | `meta.paper_fill_status` |
| `source_module` | `meta.source_module` (`"paper_decision_bridge"`) |
| `is_order_signal=False` | `meta.is_order_signal` 영구 False (sanitizer + dataclass 가드) |
| `auto_apply_allowed=False` | 위 |
| `is_live_authorization=False` | 위 |

## 6. 절대 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `mode="PAPER"` 영구 | `PaperDecisionLogEntry.__post_init__` ValueError 가드 + `record_bridge_report` 상수 사용 |
| `is_order_signal/auto_apply_allowed/is_live_authorization=False` 영구 | 위 |
| INSERT only — DELETE / UPDATE 0건 | `test_no_delete_or_update_statements` (정적 AST + token 가드) |
| `db.add(` 한 곳 외 write surface 0건 | `test_module_only_inserts_via_db_add` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| secret key in meta → `SecretInDecisionLogError` | `TestSecretSanitizer` (5 cases) |
| BUY/SELL/HOLD/EXIT/NO_OP 각각 기록 | `TestRecordEachAction` (5 cases) |
| Risk veto 차단된 HOLD 도 기록 (`risk_veto=True`) | `TestVetoRecording` (2 cases) |
| Sizing carry — `sizing_verdict` / `sizing_quantity` in meta | `TestSizingCarry` |
| 한 bridge 호출의 모든 row 동일 `chain_id` | `TestChainId` |
| `db_session=None` → 0 rows (backwards compat) | `test_no_db_session_no_writes` |
| GET /api/auto-paper/decision-log → invariants in envelope | `TestApi` |
| GET `limit=0` 거절 (`Query(ge=1)`) | `TestApi` |
| Frontend: "Paper 전용 · 실거래 아님" 영구 배지 | `PaperDecisionLogCard.test.jsx` |
| Frontend: "투자 조언 아님" / "주문 신호 아님" / "실거래 활성화 아님" 영구 | 위 |
| Frontend: `button` / `textbox` 0개 | 위 |
| Frontend: "지금 매수" / "Place Order" / "ENABLE_*" 텍스트 0건 | 위 |
| Frontend: BUY/SELL/EXIT은 `<span>` label, `<button>` 0개 | `BUY label is span, not button` |

## 7. API

### `GET /api/auto-paper/decision-log`

**Query 인자**:

| 인자 | 타입 | default | 의미 |
|---|---|---|---|
| `limit` | int [1, 1000] | 50 | 최근 N 개 (`ge=1` 강제) |
| `strategy` | str optional | None | 정확 일치 (기록된 meta.strategy) |
| `symbol` | str optional | None | 정확 일치 |
| `action` | str optional | None | BUY / SELL / HOLD / EXIT / NO_OP 정확 일치 |

**응답 envelope**:

```jsonc
{
  "mode": "PAPER",
  "source_module": "paper_decision_bridge",
  "schema_version": "1.0",
  "entry_count": 2,
  "entries": [ { "decision_id": "...", "decision_action": "BUY", ... } ],
  "summary": { "by_action": { "BUY": 1 }, "veto_count": 0, "sizing_reduced": 0 },
  "advisory_disclaimer": "본 로그는 *advisory* ...",
  "is_order_signal": false,
  "auto_apply_allowed": false,
  "is_live_authorization": false
}
```

## 8. 저장 정책 — append-only

- INSERT only — `db.add()` 호출 1곳, 모든 다른 write surface 0건 (정적 가드).
- 본 PR 은 *수정 / 삭제 API 0개* — 운영자가 row 를 *변경* 하려면 별도 PR + 옵트인
  필요 (운영 정책으로 영구 보존).
- `mode="PAPER"` 영구 — `evaluator` / `query` 모두 PAPER 만 read.

## 9. Bridge caller contract

`bridge_explanation_to_paper_decisions()` 신규 인자 (모두 None default):

| 인자 | 타입 | 의미 |
|---|---|---|
| `db_session` | `sqlalchemy.orm.Session \| None` | None=skip logging (backwards compat) |
| `chain_id` | `str \| None` | None=auto UUID4. 같은 호출의 모든 row 동일 |

기존 호출 흐름 0 변경 — `db_session=None` 이면 record 단계 skip.

## 10. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ INSERT only — DELETE / UPDATE / bulk_update 0건
- ✅ secret 필드 0건 — sanitizer fail-closed (`SecretInDecisionLogError`)
- ✅ `mode="PAPER"` 영구 — 실거래 로그와 절대 혼동 안 됨
- ✅ 안전 flag default 변경 0건 (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 그대로)
- ✅ Frontend "지금 매수" / "Place Order" / "ENABLE_*" 라벨 button 0개

## 11. 후속 PR 권고

- 상위 단계별 row — `OverfitWarningAgent` / `MarketRegimeAgent` /
  `StrategyCombinationRecommender` 각각 별도 `AgentDecisionLog` row (현재는
  PaperDecisionBridge 시점에 모두 merge 된 단일 row).
- `chain_id` 검색 UI — 한 결정 사슬의 *전체 추적* (현재는 row 별 carry 만).
- `AgentDecisionLog` archived flag — 운영자가 "노이즈" 라벨로 숨김 (현재는 모두 노출).
- LIVE 흐름 로그 — 본 PR 은 Paper 전용. LIVE 로그는 `OrderAuditLog` 가 담당.
