# Step 4-09 — Risk Veto Priority (위험 거절 우선)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 veto 는 *advisory* — Paper 가상 체결 후보 생성을 *코드 단*에서 차단.
> 실 broker 호출 0건.

## 1. 목적

AI Agent 추천이 강하더라도 *Risk가 거절하면 절대로* Paper BUY/SELL/EXIT 후보가
생성되지 않도록 강제한다. AI 추천 → Paper Decision → Ledger 라는 파이프라인의
*가장 윗 단*에 위치하는 결정론적 위험 게이트.

```
[ 1. EMERGENCY_STOP ]
       ↓ (없으면)
[ 2. Pre-market BLOCK ]                       ←─ global veto (BLOCK)
       ↓ (없으면)
[ 3. RiskOfficer REJECT ]
       ↓ (없으면)
[ 4. risk_flags ]  ← stale / duplicate / high_correlation /  per-entry veto
                     overfit / low_liquidity                  (BLOCK_NEW_ENTRY)
       ↓ (없으면)
[ 5. AI 추천 → 방향 결정 ]
       ↓
[ 6. Position Sizing (#4-08) ]
       ↓
[ 7. PaperDecision (BUY/SELL/EXIT/HOLD/NO_OP) ]
       ↓
[ 8. Paper Ledger 기록 ]
```

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/auto_paper/risk_veto.py` | `RiskVetoReason` / `RiskVetoSeverity` / `RiskVetoDecision` / `RiskVetoReport` / `evaluate_risk_veto()` |
| `backend/app/agents/paper_decision_bridge.py` | bridge — veto 평가 → AI direction 적용 (4-09 통합) |
| `backend/tests/test_auto_paper_risk_veto.py` | 40 unit tests — priority / severity / serialization / static guard |
| `backend/tests/test_paper_decision_bridge.py::TestRiskVetoIntegration` | 14 integration tests |
| `frontend/src/components/tabs/RiskVetoCard.jsx` | 운영자 UI — global / per-entry veto 표시 + 영구 disclaimer 배지 |
| `frontend/src/components/tabs/RiskVetoCard.test.jsx` | 14 vitest cases — 배지 / 차단 표 / 0 order button invariant |
| `docs/risk_veto_priority.md` | 본 정책 |

## 3. 우선순위 매트릭스

| 우선순위 | Reason | 입력 위치 | Severity |
|---|---|---|---|
| 1 | `EMERGENCY_STOP` | `loop_state == "EMERGENCY_STOP"` | `BLOCK` (모든 trade 차단) |
| 2 | `PRE_MARKET_BLOCK` | `explanation.verdict == DO_NOT_START` | `BLOCK` |
| 3 | `RISK_OFFICER_REJECT` | `risk_officer_rejects[(strategy, symbol)]` | `BLOCK_NEW_ENTRY` |
| 4 | `STALE_DATA` | `entry.risk_flags` 또는 `extra_risk_flags` | `BLOCK_NEW_ENTRY` |
| 4 | `DUPLICATE_SIGNAL` | 위 | `BLOCK_NEW_ENTRY` |
| 4 | `HIGH_CORRELATION` | 위 | `BLOCK_NEW_ENTRY` |
| 4 | `OVERFIT_RISK` | `entry.risk_flags` / `entry.overfit_verdict` | `BLOCK_NEW_ENTRY` |
| 4 | `LOW_LIQUIDITY` | `entry.risk_flags` 또는 `extra_risk_flags` | `BLOCK_NEW_ENTRY` |

**낮은 우선순위 이유들이 동시에 발생해도** `RiskVetoDecision.reasons` 에 *우선순위 순서*로 carry — 운영자가 *가장 강한* 사유부터 본다.

## 4. Severity 의미

| Severity | BUY | SELL | EXIT (보유 시) | EXIT (포지션 없음) |
|---|---|---|---|---|
| `NONE` | 허용 | 허용 | 허용 | 허용 |
| `BLOCK_NEW_ENTRY` | **차단** → HOLD | **차단** → HOLD | **허용** (위험 축소) | 차단 → HOLD |
| `BLOCK` | **차단** → HOLD | **차단** → HOLD | **차단** → HOLD | **차단** → HOLD |

**EXIT 허용 정책**: `BLOCK_NEW_ENTRY` 는 RiskOfficer / risk_flags 가 *새 진입* 을
거절하는 상황이므로, 보유 포지션의 *위험 축소 EXIT* 은 허용한다. 다만 `BLOCK`
(EMERGENCY_STOP / Pre-market) 은 *어떤 신규 broker 흐름도* 안전을 위해 차단 —
운영자가 명시 청산 흐름을 거치도록 (`PermissionGate` 강제 청산 옵트인 별도).

## 5. Bridge 통합 (4-07 + 4-09)

`bridge_explanation_to_paper_decisions()` 가 매 호출마다 *맨 위에서* veto 평가:

```python
veto_report = evaluate_risk_veto(
    explanation=explanation,
    loop_state=loop_state,
    risk_officer_rejects=risk_officer_rejects,
    extra_risk_flags=extra_risk_flags,
)
```

각 entry 의 direction 결정 직후 `_apply_veto()` 호출:
- `BUY/SELL` + 모든 veto → `HOLD` (block_reason carry)
- `EXIT` + `BLOCK` severity → `HOLD`
- `EXIT` + `BLOCK_NEW_ENTRY` + 보유 → `EXIT` 유지 (허용)
- `EXIT` + `BLOCK_NEW_ENTRY` + 포지션 없음 → `HOLD`

veto 카운트 / 사유 / severity 는 다음 위치에 carry:
- `PaperDecision.metadata.risk_veto` (bool) / `risk_veto_reasons` / `risk_veto_severity`
- `BridgeReport.metadata.risk_veto` (전체 `RiskVetoReport.to_dict()`)
- `BridgeReport.block_reasons` (사람이 읽기 좋은 문자열 목록)

## 6. API / Caller Contract

`bridge_explanation_to_paper_decisions(...)` 신규 인자:

| 인자 | 타입 | default | 의미 |
|---|---|---|---|
| `risk_officer_rejects` | `dict[(strategy, symbol), str] \| None` | None | RiskOfficer / RiskManager 가 명시 거절한 키 + 사유 |
| `extra_risk_flags` | `dict[(strategy, symbol), list[str]] \| None` | None | runtime 감지 flag (예: KIS stale data) — `entry.risk_flags` 와 합집합 |

backwards compat: 두 인자 모두 None default — 기존 호출 흐름 0 변경.

## 7. 절대 invariant (테스트로 lock)

| 항목 | 강제 위치 |
|---|---|
| `RiskVetoDecision.is_order_signal=False` | `__post_init__` ValueError |
| `RiskVetoDecision.auto_apply_allowed=False` | 위 |
| `RiskVetoDecision.is_live_authorization=False` | 위 |
| `RiskVetoReport.*` 동일 | 위 |
| `vetoed=True` ⇒ `severity != NONE` | `__post_init__` 가드 |
| `vetoed=False` ⇒ `reasons == []` | `__post_init__` 가드 |
| EMERGENCY_STOP 입력 → 우선순위 첫 번째 | `test_emergency_first_in_reasons` |
| EMERGENCY_STOP + risk_flag → severity=BLOCK | `test_severity_emergency_overrides` |
| RiskOfficer REJECT → BUY 차단 | `test_risk_officer_reject_blocks_buy` |
| EMERGENCY_STOP → EXIT 도 차단 | `test_exit_blocked_under_emergency_stop` |
| BLOCK_NEW_ENTRY + 보유 → EXIT 허용 | `test_exit_allowed_under_block_new_entry` |
| risk_flag STALE_DATA → BUY 차단 | `test_stale_data_flag_blocks_buy` |
| risk_flag HIGH_CORRELATION → BUY 차단 | `test_high_correlation_flag_blocks_buy` |
| risk_flag OVERFIT_RISK → BUY 차단 | `test_overfit_risk_flag_blocks_buy` |
| risk_flag LOW_LIQUIDITY → BUY 차단 | `test_low_liquidity_flag_blocks_buy` |
| extra_risk_flags → BUY 차단 | `test_extra_risk_flags_block_buy` |
| 다른 entry 는 영향 없음 | `test_reject_only_applies_to_specified_key` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| DB write surface 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| frontend "지금 매수" / "Place Order" / "ENABLE_*" 버튼 0개 | RiskVetoCard.test.jsx |
| "Risk veto 우선 — Paper 주문 후보 생성 안 됨" 배지 영구 | 위 |
| "투자 조언 아님" / "실거래 활성화 아님" / "주문 신호 아님" 배지 영구 | 위 |

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 grep + AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ AI 추천이 강해도 BUY/SELL 생성 0건 — *Risk veto 우선*
- ✅ DB write 0건 — 순수 함수
- ✅ secret 필드 0건 (API key / 계좌번호 carry 0개)
- ✅ 안전 flag default 변경 0건 (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 그대로)
- ✅ Frontend 에 실거래 시작 / 지금 매수 / Place Order 버튼 0개 (테스트로 lock)

## 9. 후속 PR 권고

- `risk_officer_rejects` 자동 carry — `RiskManager.check_order(read-only=True)` 가
  반환하는 REJECTED 결과를 bridge caller (예: `auto_paper_loop`) 가 매 tick 마다
  채우도록.
- `extra_risk_flags` 자동 carry — `STALE_PRICE_MAX_AGE_SECONDS` 검사, `OrderGuard`
  duplicate / cooldown, `PortfolioCorrelationGuard` (#95) 의 BLOCK 결과를
  bridge 입력 dict 로 변환.
- AgentDecisionLog 통합 — 본 PR 은 `PaperDecision.metadata.risk_veto` 만 carry,
  `AgentDecisionLog` 영구 저장은 후속 PR.
- API endpoint — `POST /api/auto-paper/risk-veto/preview` (read-only) — frontend
  가 사전에 어떤 entry 가 차단될지 미리 보는 용도.
- LiveStrategyEngine 통합 — 본 PR 은 Paper bridge 만, LIVE 흐름은 별도.
