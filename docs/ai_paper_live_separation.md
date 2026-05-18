# Step 4-Live-Separation — AI Paper vs Live 영구 분리 정책

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 정책은 *advisory* — AI Paper 흐름이 어떤 코드 경로로도 실 broker 에
> 도달하지 못하도록 *영구* 강제.

## 1. 목적

AI Paper 자동매매 흐름과 Live 실 broker 주문 경로가 *완전히 분리* 되어 있음을
코드 단 + 테스트 단으로 영구 잠근다. AI Paper 에서 BUY / SELL / EXIT 판단을
만들어도 *실제 돈이 나가는 경로는 0개* 임을 cross-cutting 테스트로 매 CI 마다
검증.

## 2. AI Paper vs AI Live

| 항목 | AI Paper (현재) | AI Live (별도 옵트인) |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` / `PAPER` | `LIVE_*` |
| broker | `MockBroker` / `PaperBroker` (`assert_paper_broker`) | `KisBrokerAdapter(is_paper=False)` *(현재 NotImplementedError)* |
| 주문 흐름 | bridge → `PaperDecision` → `paper ledger` | `route_order` → `RiskManager` → `PermissionGate` → `OrderExecutor` |
| `is_order_signal` | `False` 영구 | LIVE 코드는 별도 메타 (`OrderRequest` / `OrderAuditLog`) |
| AgentDecisionLog row | `mode="PAPER"` 영구 | `mode="LIVE_*"` (별도 PR) |
| 실 KRW 이동 | **0** | LIVE 옵트인 + 운영자 명시 승인 후에만 |
| 활성화 게이트 | `ENABLE_LIVE_TRADING=false` 기본 | `ENABLE_LIVE_TRADING=true` + 8개 옵트인 조건 (#promotion_policy) |
| AI 직접 실행 | **불가** — Permission Gate 5 단계 (#39) | LIVE_AI_EXECUTION + `AIExecutionGate` (#45) 통과 후에만 |

## 3. PaperDecision 은 *실제 주문이 아니다*

- `PaperDecision.is_order_signal == False` (`__post_init__` ValueError 가드).
- `auto_apply_allowed == False` 영구.
- `is_live_authorization == False` 영구.
- `paper_fill_status` 가 `PAPER_FILLED` 라도 *가상 체결만* — broker round-trip
  0건.
- AgentDecisionLog row 의 `mode == "PAPER"` 영구 — Live 흐름의 audit row 와
  *물리적으로 분리* (`PaperDecisionLogEntry.__post_init__` 가드).

## 4. Live 주문은 Live Gate / Canary 전까지 *불가*

본 PR 시점의 LIVE 흐름은:

- `KisBrokerAdapter.place_order(is_paper=False)` → `NotImplementedError`
- `KisBrokerAdapter.cancel_order(...)` → `NotImplementedError`
- 별도 LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST / LIVE_AI_EXECUTION 단계가
  순차로 통과되어야만 활성화 (`promotion_policy.md` + `live_activation_blockers.md`).

## 5. `ENABLE_LIVE_TRADING=false` 가 보장하는 것

- `.env.example` 기본값 `false` — 테스트로 lock.
- 모든 운영자 PC 의 *기본 설치 상태*에서 LIVE 흐름 0건.
- 운영자가 `ENABLE_LIVE_TRADING=true` 로 수동 변경해도 *추가 단계 4 개* 통과
  필요 — `AIExecutionGate(#45)` + `RiskManager` + `PermissionGate` +
  `OrderExecutor` 단일 진입점 (#40).

## 6. AI 가 Paper 에서 매수/매도 판단해도 *실제 돈은 나가지 않는다*

본 PR 의 cross-cutting 테스트 (`test_ai_paper_live_separation.py`) 가 매 CI
마다 다음을 검증:

| 검증 항목 | 테스트 클래스 |
|---|---|
| `auto_paper/` 모든 모듈에 broker / route_order / OrderExecutor / KisClient import 0건 | `TestStaticGuardsAutoPaper` |
| `auto_paper/` 모든 모듈에 Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `auto_paper/` 모든 모듈에 `settings.enable_live_trading=` mutation 0건 | 위 |
| AST 단으로 OrderExecutor / route_order / `.place_order` 호출 0건 | `test_ast_no_OrderExecutor_or_route_order_calls` |
| `PaperDecision` / `ConsumerResult` / `SizingResult` / `RiskVetoDecision` / `RiskVetoReport` / `BridgeReport` / `PaperDecisionLogEntry` 모든 invariant 기본값 False | `TestPaperDataclassInvariants` |
| 위 dataclass 의 invariant True 설정 시 ValueError | `TestPaperDecisionWriteProtection` |
| `PaperDecisionLogEntry.mode` 가 `"PAPER"` 외 거부 | `test_paper_decision_log_entry_mode_paper_only` + `_simulation_blocked` |
| BUY decision 발생 시 `KisBrokerAdapter.place_order` spy 호출 0건 | `test_buy_decision_does_not_call_live_broker` |
| EXIT decision 발생 시 spy 호출 0건 | `test_sell_decision_does_not_call_live_broker` |
| RUNNING 3 tick consumer 실행 후에도 spy 호출 0건 | `test_loop_tick_consumer_does_not_call_live_broker` |
| Risk veto 경로에서도 spy 호출 0건 | `test_risk_veto_path_does_not_call_live_broker` |
| EMERGENCY_STOP 단락 경로에서도 spy 호출 0건 | `test_emergency_stop_short_circuit_no_live` |
| `KisBrokerAdapter.place_order(is_paper=False)` → NotImplementedError | `test_place_order_live_raises_not_implemented` |
| `KisBrokerAdapter.cancel_order(...)` → NotImplementedError | `test_cancel_order_raises_not_implemented` |
| `.env.example` 의 4 안전 flag default | `test_env_example_defaults_safe` |
| AgentDecisionLog row 의 `mode == "PAPER"` 영구 | `test_paper_rows_always_mode_paper` |
| `auto_paper/decision_log.py` 자체에 broker / route_order / OrderExecutor import 0건 | `test_decision_log_module_no_live_broker_imports` |
| End-to-end (BUY + EXIT + risk_veto HOLD) — spy 0건 + 모든 row mode=PAPER | `test_full_cycle_emits_zero_live_calls` |

## 7. 실전 전환은 별도 PR + 운영자 명시 승인 필요

`ENABLE_LIVE_TRADING=true` 로의 전환은 본 PR 의 범위가 **아니다**. 전환은
다음 단계를 *순차로* 통과한 별도 옵트인 PR 에서만 수행:

1. **Paper Gate (#72)** — 4 주 운용 + ≥100 건 + expectancy > 0 + PF ≥ 1.2 + MDD ≤ 15% + 손실 한도 위반 0
2. **Live Manual Gate (#73)** — Paper Gate PASS + 운영자 explicit opt-in + AI execution disabled + 1회 주문 ≤ 5만원 + 일일 손실 ≤ 1만원 + 보유 ≤ 3개
3. **AI Assist Gate (#74)** — AI 제안 품질 검증 (LIVE_AI_ASSIST 모드)
4. **AI Execution Activation Gate (#75)** — *최종* 게이트, READY_FOR_REVIEW + 운영자 explicit opt-in + 8개 안전 조건
5. **AIExecutionGate (#45)** — order-time 보수적 12 가드
6. **canary 운용** — 초소액 (≤ 1만원/거래), 즉시 kill switch 가능

본 정책은 [`docs/promotion_policy.md`](promotion_policy.md) + [`docs/live_activation_blockers.md`](live_activation_blockers.md) 를 그대로 carry.

## 8. CLAUDE.md 절대 원칙 매핑

| 절대 원칙 | 본 정책에서의 강제 위치 |
|---|---|
| 1. AI 가 broker 주문 API 직접 호출하는 코드 0건 | `TestStaticGuardsAutoPaper` + `test_agents_no_direct_order_guard.py` (#4-06) |
| 2. 모든 주문 RiskManager → PermissionGate → OrderExecutor 순서 | Live 흐름의 `route_order` (#34/#40) — Paper 흐름은 그 흐름을 *우회하지 않고 아예 LIVE 경로를 갖지 않는다* |
| 3. 기본 운용모드 SIMULATION / PAPER | `DEFAULT_MODE=SIMULATION` + `mode="PAPER"` 영구 |
| 4. API Key / Secret / 계좌번호 / Anthropic Key frontend 0건 + git 0건 | `#93 security_scan` (별도 정책) + `_FORBIDDEN_HTTP_AI_PATTERNS` (본 모듈) |
| 5. 프론트엔드는 관제 / 승인 / 설정 — broker / AI 호출 backend 전용 | 본 PR 변경 0건 — 기존 정책 유지 |

## 9. 본 PR 의 작업 범위

- 신규 테스트 1개: `backend/tests/test_ai_paper_live_separation.py` (54 cases)
- 신규 문서 1개: `docs/ai_paper_live_separation.md` (본 문서) + README 링크
- **운영 코드 변경 0건** — broker / OrderExecutor / route_order / 안전 flag /
  `.env.example` / Strategy / RiskManager / Alembic migration 변경 없음.
- 본 PR 은 *기존 정책을 cross-cutting 으로 검증* 하는 정적 + 동적 잠금 역할
  만 수행.
