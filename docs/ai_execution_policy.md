# AI Execution 정책 (#45)

본 문서는 `LIVE_AI_EXECUTION` 운용모드와 `AIExecutionGate`(`app/risk/ai_execution_gate.py`)의 정책·invariant·운영 절차를 정의합니다. CLAUDE.md 절대 원칙(특히 1/2/3/5/6) + `ai_permission_gate.md`(#39) + `ai_assisted_trading_policy.md`(#44) + `risk_manager_contract.md`(#34) 위에서 동작하는 *최종 안전 레이어*입니다.

## 1. 목적

LIVE_AI_EXECUTION은 AI가 사람 승인 없이 자동으로 주문을 실행할 수 있는 *최종* 단계입니다. 본 PR(#45)은 그 단계의 안전 게이트(`AIExecutionGate`) + 정책 contract + audit 계약 + 문서/테스트만 정비하며, **실제 live AI execution은 활성화하지 않습니다**.

## 2. 기본 비활성 원칙

| flag / 정책 | 기본값 | 효과 |
|---|---|---|
| `ENABLE_AI_EXECUTION` | `false` | LIVE_AI_EXECUTION에서 AI 자동 실행 차단 — `can_ai_execute()` + AIExecutionGate 모두 검사 |
| `ENABLE_LIVE_TRADING` | `false` | 모든 LIVE_* 모드에서 broker 호출 차단 — RiskManager + AIExecutionGate 검사 |
| `AIExecutionPolicy.is_canary_mode` | `True` | 모든 통과 후보를 `CANARY_ONLY`로 분류 (broker 주문 X) |
| `AIExecutionPolicy.symbol_whitelist` | `frozenset()` (비어 있음) | **모든 종목 차단** (안전 측 — empty = block all) |
| `AIExecutionPolicy.max_notional_per_order` | `100_000` (10만원) | 극소액 시작 |
| `AIExecutionPolicy.max_orders_per_day` | `3` | 일일 한도 매우 보수적 |
| `AIExecutionPolicy.window_start_hour_kst` | `10` | 09:30 변동성 회피 후 10시 시작 |
| `AIExecutionPolicy.window_end_hour_kst` | `14` | 장마감 1.5시간 전 종료 |

**`AIExecutionPolicy()` default 정책으로 `evaluate_ai_execution(any_input)`을 호출하면 어떤 입력에서도 BLOCKED를 반환합니다** — 본 PR의 코드 경로 어디서도 ALLOW에 도달하지 않으며, 정적 테스트(`test_default_policy_blocks_any_input`)로 강제됩니다.

## 3. VIRTUAL_AI_EXECUTION vs LIVE_AI_EXECUTION

| 항목 | VIRTUAL_AI_EXECUTION | LIVE_AI_EXECUTION |
|---|---|---|
| 시세 | MockBroker (가상) | 실 KIS 시세 |
| broker 호출 | 가상 broker만 | KIS live (활성 시) |
| 사람 승인 | ✗ | ✗ (mode capability) |
| `enable_ai_execution` 검사 | ✗ (가상이므로 flag 무관) | ✓ 필수 |
| `enable_live_trading` 검사 | ✗ | ✓ 필수 |
| AIExecutionGate 적용 | (선택) — 가상 시뮬에서 게이트 시뮬레이션 가능 | ✓ 필수 (마지막 안전 레이어) |
| 본 PR 시점 활성 | (운영자 설정) | **비활성 (default)** |

VIRTUAL은 *AI 흐름 자체를 검증*하기 위한 sandbox, LIVE는 *실거래*입니다.

## 4. AIExecutionGate 조건 (12개 가드)

`evaluate_ai_execution(inp, policy)`이 검사하는 가드는 다음 순서로 누적되며, **한 가드라도 실패하면 BLOCKED** (모든 사유가 reasons에 누적).

| # | 가드 | 위반 시 reason 예시 |
|---|---|---|
| 1 | `mode == LIVE_AI_EXECUTION` + `capability.ai_can_execute=True` | `mode LIVE_AI_ASSIST is not LIVE_AI_EXECUTION` |
| 2 | `enable_ai_execution=True` (env) | `ENABLE_AI_EXECUTION=false (default — opt-in required)` |
| 3 | `enable_live_trading=True` (env) | `ENABLE_LIVE_TRADING=false (default — opt-in required)` |
| 4 | `confidence >= min_confidence` (default 80) | `confidence 70 < min 80` |
| 5 | `quality_score >= min_quality_score` (default 70) | `quality_score 50 < min 70` |
| 6 | `explanation` non-empty (require_explanation=True) | `explanation is required but missing` |
| 7 | `target_price > 0` + `stop_price > 0` (require_exit_plan=True) | `exit plan is required (target_price + stop_price > 0)` |
| 8 | `notional <= max_notional_per_order` (default 100,000) | `notional 1000000 > max_notional_per_order 100000` |
| 9 | `symbol in whitelist` (default `frozenset()` = block all) | `symbol_whitelist is empty — no symbol allowed` |
| 10 | KST 현재 시각이 [window_start, window_end) 안 (default [10:00, 14:00)) | `now KST 08:00 not in execution window [10:00, 14:00)` |
| 11 | `today_ai_order_count < max_orders_per_day` (default 3) | `today_ai_order_count 3 >= max_orders_per_day 3` |
| 12 | `risk_passed` + `permission_passed` + `order_guard_passed` (상위 가드 carry) | `RiskManager guard not passed (risk_passed=False)` |

**12번 가드**는 AIExecutionGate가 RiskManager / AiPermissionGate(#39) / OrderGuard(#38)의 결과를 *재검증하지 않고* carry만 합니다 — 책임 분리. 호출자가 이미 통과시킨 결과를 boolean flag로 전달.

## 5. 결정 매트릭스

| 가드 위반 | canary mode | 결정 |
|---|---|---|
| ≥1건 | (무관) | `BLOCKED` |
| 0건 | True (default) | `CANARY_ONLY` (실제 broker 주문 X) |
| 0건 | False | `ALLOW` (실제 broker 주문 가능) |

`ALLOW` 분기는 본 PR의 default code path에서 **도달 불가능**입니다 — `enable_ai_execution=False` + `is_canary_mode=True`가 default.

### canary mode 정책

- `is_canary_mode=True`이면 모든 통과 후보를 `CANARY_ONLY`로 분류
- `audit_note = "AI execution canary only; no broker order sent"` 고정 문자열
- `result.allowed_to_execute = False` (broker 주문 차단)
- `result.actual_broker_order_sent = False` (audit row carry)
- 향후 LIVE 실행 흐름이 추가될 때, OrderExecutor 진입 *전*에 `result.allowed_to_execute`로 분기 — CANARY_ONLY는 broker로 가지 않고 ShadowTrade(#43) / VirtualOrder(#152)로 기록만 (별도 PR)

### canary 운용 가이드

- 최소 **1~2주** canary mode 운용
- 매일 canary 결과(decision/audit_note/blocked reasons) review
- 같은 기간 LIVE_SHADOW(#43)와 PAPER 결과 함께 비교 — AI 자동 결정의 reasoning 품질 + 추정 체결가 vs 실 체결가 차이 측정
- canary 해제는 **별도 PR** + 운영자 명시 opt-in (`promotion_policy.md` 8개 조건 충족 후)

## 6. RiskManager / AI Permission Gate / OrderGuard / PermissionGate 연계

AIExecutionGate는 *대체*가 아닌 *추가* 레이어입니다. LIVE_AI_EXECUTION의 가드 체인:

```
OrderGuard          (#38, route_order 진입 시)
    ↓
RiskManager         (#34, 모든 한도/긴급정지/시세/손실)
    ↓
AiPermissionGate    (#39, AI 권한 매트릭스 — LIMITED_LIVE_EXECUTION level)
    ↓
AIExecutionGate     (#45, AI 자동 실행 전용 보수적 한도 + canary)
    ↓
PermissionGate      (LIVE_AI_EXECUTION은 사람 승인 없음 — 본 단계는 no-op)
    ↓
OrderExecutor → BrokerAdapter   (마지막 backstop)
```

각 게이트는 *독립적*으로 차단 권한을 가지며, AIExecutionGate는 그중 마지막 *AI-specific* 게이트로 RiskManager가 잡지 않는 추가 보수적 한도(symbol whitelist, time window, daily count, exit plan, canary)를 담당합니다.

## 7. 초기 제한 권장 (default 정책)

```python
AIExecutionPolicy(
    enable_ai_execution=False,        # opt-in 전용
    enable_live_trading=False,        # opt-in 전용
    is_canary_mode=True,              # canary 우선
    min_confidence=80,                # 높은 confidence만
    min_quality_score=70,
    require_explanation=True,         # reasoning 필수
    require_exit_plan=True,           # target + stop 필수
    max_notional_per_order=100_000,   # 10만원 (극소액)
    symbol_whitelist=frozenset(),     # 비어 있음 → 모두 차단
    max_orders_per_day=3,             # 일일 3건
    window_start_hour_kst=10,
    window_end_hour_kst=14,
)
```

운영자가 후속 PR에서 env 변수로 노출할 때도, **default는 본 보수적 값을 유지**합니다.

## 8. Audit 계약 (#45)

LIVE_AI_EXECUTION을 향후 활성화할 때 모든 AI 실행 후보는 `OrderAuditLog`에 다음 필드를 채워야 합니다. 본 PR은 새 컬럼을 추가하지 않고 *기존 컬럼 + JSON metadata*에 mapping해 backwards compat을 유지합니다.

| audit 계약 필드 | OrderAuditLog 매핑 |
|---|---|
| `requested_by_ai=true` | `OrderAuditLog.requested_by_ai` (bool) |
| `agent_name` | `ai_decision_meta.agent_name` |
| `agent_chain_id` | `ai_decision_meta.agent_chain_id` (#187 AgentDecisionLog과 cross-ref) |
| `strategy` | `OrderAuditLog.strategy` |
| `confidence` | `OrderAuditLog.signal_confidence` |
| `quality_score` | `OrderAuditLog.signal_strength` |
| `ai_decision_meta` | `OrderAuditLog.ai_decision_meta` (JSON) |
| `explanation` | `ai_decision_meta.supporting_reasons` + `opposing_reasons` (#44) 또는 `ai_decision_meta.explanation` |
| `risk_result` | `OrderAuditLog.decision` + `reasons` |
| `permission_result` | `ai_decision_meta.permission_result` (AiPermissionGate decision) |
| `order_guard_result` | `ai_decision_meta.order_guard_result` (OrderGuard decision) |
| `ai_execution_gate_result` | `ai_decision_meta.ai_execution_gate_result` (`AIExecutionResult.to_audit_meta()`) |
| `final_action` | `OrderAuditLog.executed` (bool) + `decision` |
| `actual_broker_order_sent` | `OrderAuditLog.executed` AND `OrderAuditLog.broker_order_id` non-null |

`AIExecutionResult.to_audit_meta()`가 dict를 반환하면 호출자가 `audit.ai_decision_meta["ai_execution_gate_result"] = result.to_audit_meta()`로 carry합니다 — DB 마이그레이션 0건.

본 PR에서 broker로 가는 코드 경로가 0건이므로 모든 시뮬 audit row의 `actual_broker_order_sent`는 `False`입니다 (canary / BLOCKED 둘 다).

## 9. API surface (read-only)

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/ai-execution/evaluate` | POST | AIExecutionInput 평가 → AIExecutionResult. **broker 호출 0건, audit row 0건** |
| `/api/ai-execution/policy` | GET | 현재 정책 read-only 조회 (UI 정책 카드용) |

두 endpoint 모두 **read-only**. `routes_ai_execution.py`는 broker / OrderExecutor를 import하지 않으며 정적 grep 가드(`test_routes_ai_execution_does_not_import_broker_or_executor`)로 강제됩니다.

## 10. UI

### AISignal 탭 — `AiExecutionPolicyCard`

- 현재 정책 12개 필드를 read-only 표로 표시
- 상태 배지: **비활성 (기본값)** (회색) → **canary 모드** (호박색) → **활성** (녹색)
- 비활성 시 disclaimer: "AI 자동 실행은 기본 비활성화입니다. ENABLE_AI_EXECUTION + ENABLE_LIVE_TRADING + 운영자 명시 opt-in이 모두 필요합니다. 본 화면에는 활성화 토글이 의도적으로 제공되지 않습니다."
- canary 시 disclaimer: "canary 모드: AI 자동 실행 후보가 모두 통과해도 실제 주문은 나가지 않습니다 (decision=CANARY_ONLY). 1~2주 canary 운용 후 결과 비교 후에만 해제합니다."
- "AI API Key는 주문 권한이 아닙니다" 안내 문구

### **명시적으로 추가하지 않는 UI** (테스트로 강제)

- `ENABLE_AI_EXECUTION` 토글 / 활성화 버튼
- 자동매매 시작 버튼
- canary mode 해제 버튼
- AI live 주문 직접 실행 버튼

`AiExecutionPolicyCard.test.jsx::"does NOT render any toggle / start button"`이 `<button>`/`<input>`/`<select>` 요소가 0개임을 검증합니다.

## 11. 사람 승인 정책

LIVE_AI_EXECUTION은 mode capability상 `requires_user_approval=False`입니다 — AI가 자동 실행하는 것이 본 모드의 정의입니다. 그러나:

- **활성화 자체**는 별도 옵트인 PR + 운영자 명시 승인 (`promotion_policy.md` 8개 조건)
- **canary mode**는 운영자가 별도 PR로 해제할 때까지 유지 (1~2주 권장)
- **사고 발생 시** `RiskManager.emergency_stop` 또는 `RiskPolicy.disable_ai_orders`로 즉시 차단 가능 — AIExecutionGate도 이 두 flag를 carry하면 BLOCKED (#39 invariant 상속 + 본 게이트의 12번 가드)

본 PR 이전에 권장되는 사전 검증:

1. **Shadow 단계** — `LIVE_SHADOW`(#43)에서 AI Assist 흐름의 would-have 통계 수집 (1~2주)
2. **Paper 단계** — `PAPER`에서 KIS 모의 broker 라우팅 검증
3. **AI Assist 단계** — `LIVE_AI_ASSIST`(#44)에서 사람 승인 + AI reasoning 품질 검증 (다수 trade)
4. **canary 단계** — 본 PR의 AIExecutionGate가 canary mode로 통과시킬 시뮬 (1~2주)

## 12. 금지사항 (절대 invariant)

- AI가 broker / OrderExecutor / route_order를 직접 호출 — `app.risk.ai_execution_gate` + `app.api.routes_ai_execution`은 broker import 0건 (정적 grep 가드)
- `ENABLE_AI_EXECUTION` / `ENABLE_LIVE_TRADING`을 코드에서 True로 변경 — 본 PR에서 0건
- 새 broker live order 호출 코드 경로 — 본 PR에서 0건
- 선물 LIVE 활성화 — `ENABLE_FUTURES_LIVE_TRADING` 변경 0건
- AI API Key를 주문 권한 조건으로 사용 — `AIExecutionPolicy` / `AIExecutionInput` 어떤 필드도 API key를 받지 않음

## 13. 변경 시 동기화

다음 변경은 본 문서 + `CLAUDE.md` + 관련 정책 문서를 함께 업데이트해야 합니다:

- `AIExecutionPolicy` 필드 추가/제거 또는 default 변경
- `AIExecutionDecision` enum 추가/제거
- `evaluate_ai_execution`의 가드 순서 또는 추가 가드
- `routes_ai_execution`의 새 endpoint
- canary mode 기본값 변경
- LIVE_AI_EXECUTION 활성화 — 별도 PR + 본 문서 §2/§3/§7/§11 갱신

## 관련 문서

- [`ai_permission_gate.md`](ai_permission_gate.md) — AI 권한 단계 (#39, 본 게이트의 한 단계 위)
- [`ai_assisted_trading_policy.md`](ai_assisted_trading_policy.md) — LIVE_AI_ASSIST (#44, 사람 승인 단계)
- [`risk_manager_contract.md`](risk_manager_contract.md) — `check_order` 표준 진입점 (#34)
- [`order_executor_contract.md`](order_executor_contract.md) — broker 단일 호출 진입점 (#40)
- [`promotion_policy.md`](promotion_policy.md) — LIVE_AI_EXECUTION 옵트인 8개 조건
- [`shadow_mode.md`](shadow_mode.md) / [`live_shadow_trade_policy.md`](live_shadow_trade_policy.md) — 사전 검증 단계
