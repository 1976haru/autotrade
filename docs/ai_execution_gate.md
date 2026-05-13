# AI Execution Activation Gate Policy — 체크리스트 #75

> `LIVE_AI_EXECUTION` 활성화의 *최종* readiness 평가 게이트.
> **본 게이트의 PASS(READY_FOR_REVIEW)는 실제 활성화가 *아니다***.
> 활성화는 별도 옵트인 PR + 사용자 명시 승인 + `.env` flag 변경 + 초소액 canary +
> 즉시 kill switch 가능 모두 필요.
>
> **선물 AI Execution은 본 게이트가 *영구* 허용하지 않는다** (`futures_allowed=false` 불변).

---

## 1. 목적

- AI 자동매매로 완전 자동화로 넘어가는 *가장 마지막 단계의 안전 게이트*.
- promotion_policy / PaperGate / AI Assist Gate / Manual Approval / Shadow / OrderGuard /
  RiskManager / AI Permission Gate / AuditLog / KillSwitch 등 *모든 전제 조건을
  강제로 확인* 후 readiness 판정만 반환.
- 실제 활성화는 별도 옵트인 PR 절차로 분리 — 본 게이트가 직접 활성화하지 *않는다*.

---

## 2. 전제조건 (모두 충족 시에만 READY_FOR_REVIEW)

### 2.1 전제 게이트

| # | 조건 | 출처 |
|---|---|---|
| 1 | Promotion Gate(#27) PASS | `docs/strategy_promotion_gate.md` |
| 2 | Paper Gate(#72) PASS | `docs/paper_gate_policy.md` |
| 3 | AI Assist Gate(#74) PASS | `docs/ai_assist_gate.md` |
| 4 | Live Manual Gate(#73) PASS | `docs/live_manual_gate.md` |
| 5 | 운영자 explicit opt-in | 사용자 명시 (별도 PR / 운영 노트) |

### 2.2 안전 인프라

| # | 조건 |
|---|---|
| 6 | RiskManager 활성 (#34) |
| 7 | OrderGuard 활성 (#38) |
| 8 | AI Permission Gate 활성 (#39) |
| 9 | AuditLog 완전 (누락 0건) |
| 10 | KillSwitch 준비됨 (3-Level Kill Switch #37 drill 완료) |
| 11 | Circuit Breaker 설정됨 (비정상 손실 / API 오류 자동 중단) |

### 2.3 운영 기간

| # | 조건 | 임계 |
|---|---|---|
| 12 | Live Manual 운영 일수 | ≥ **28일** |
| 13 | AI Assist 운영 일수 | ≥ **28일** |

---

## 3. 초기 제한 (극소액 정책)

본 게이트가 강제하는 *기본 차단 원칙* — `LIVE_MANUAL_APPROVAL` (#73)보다 *더
보수적*. AI가 *완전 자동*으로 주문하는 단계이므로 한도가 더 작다.

| 항목 | 권장 / 임계 |
|---|---|
| 1회 주문 금액 (`max_order_notional`) | ≤ **30,000원** |
| 일일 손실한도 (`max_daily_loss`) | ≤ **5,000원** |
| 일일 주문 수 (`max_daily_order_count`) | ≤ **10건** |
| 동시 보유 종목 (`max_open_positions`) | ≤ **2개** |
| 허용 종목 수 (`allowed_symbols`) | **1 ~ 5개** (whitelist 강제) |
| 거래 시간 (KST) | **09:30 ~ 14:30** (시가 직후 + 동시호가 배제) |
| AI confidence 임계 | ≥ **75 / 100** |
| signal quality 임계 | ≥ **70 / 100** |

위 정책은 운영자가 *더 엄격하게* override 가능. 본 게이트는 RiskPolicy /
스케줄 설정 자체를 *수정하지 않는다* — 운영자가 설정한 *현재값*만 검사.

---

## 4. 시스템 안정성 기준

| 항목 | 기준 |
|---|---|
| 시스템 오류 (`system_errors`) | **= 0** |
| OrderAuditLog 누락 (`audit_missing_count`) | **= 0** |
| Approval 우회 시도 (`approval_bypass_attempts`) | **= 0** |

---

## 5. 선물 정책 — 영구 불허

본 게이트는 *주식 단타*를 위한 게이트다. **선물 AI Execution은 본 게이트가
*영구* 허용하지 않는다.**

- `AIExecutionActivationGateResult.futures_allowed=False` 불변 (dataclass
  `__post_init__` ValueError 가드 — True 생성 자체 불가).
- `GET /api/governance/ai-execution-gate/policy` 가 항상 `"futures_allowed": false`
  반환.
- 입력 `futures_target=True` → 즉시 BLOCKED.
- 입력 `enable_futures_live_trading=True` → 즉시 BLOCKED.

선물 AI Execution은 [`live_activation_blockers.md`](live_activation_blockers.md) §3.1
의 9단계 blocker 통과 + 별도 게이트 + 별도 PR 필요. 본 게이트로는 *어떤
시나리오로도* 활성화 검토 대상이 아니다.

---

## 6. PASS / CAUTION / BLOCKED 기준

| Verdict | 의미 |
|---|---|
| **READY_FOR_REVIEW** | 모든 전제 조건 충족 — *활성화 검토 가능* 상태. **실제 활성화 아님**. |
| **CAUTION** | 기준 충족이지만 CAUTION 사유 있음 — 운영자 점검 후 재평가. |
| **BLOCKED** | 전제 조건 / 안전 인프라 / 한도 중 하나 이상 미달. |
| **UNKNOWN** | 데이터 부족 — 보수적으로 BLOCKED 취급 권장. |

### READY_FOR_REVIEW = 실제 활성화가 아닌 이유

1. **별도 옵트인 PR 필요** — `ENABLE_AI_EXECUTION=true` 전환은 코드 변경 PR
   리뷰 절차로만.
2. **사용자 명시 승인 필요** — PR review에 명시 코멘트.
3. **초소액 canary** — 활성화 직후 며칠은 *극소액 1주 1건* 수준으로 운영.
4. **즉시 kill switch** — KillSwitch(#37) LEVEL_3 (청산 후보 표시) 토글이
   언제든 가능해야 함.
5. **RiskManager / PermissionGate / OrderExecutor 우회 금지** (CLAUDE.md 원칙 1~2).

`AIExecutionActivationGateResult.is_live_authorization=False`,
`is_order_signal=False`, `is_investment_advice=False`, `futures_allowed=False`
모두 invariant.

---

## 7. 실제 활성화 절차 (본 게이트와 분리)

본 절은 *참고용*. 본 게이트는 다음 절차를 *수행하지 않는다*.

1. 본 게이트 평가: `POST /api/governance/ai-execution-gate/evaluate` → READY_FOR_REVIEW.
2. **별도 PR 생성** — `ENABLE_AI_EXECUTION=true` 변경 + KIS AI 라우팅 활성화 코드.
3. PR review에 사용자 명시 승인 ("이 PR로 AI 자동매매를 활성화한다" 코멘트).
4. PR 머지 후 `.env` 적용 + 백엔드 재시작.
5. **초소액 canary 기간 (최소 1주)** — 1일 1주 정도로 극소액 운영.
6. KillSwitch drill — 활성화 첫날 LEVEL_1 / LEVEL_2 / LEVEL_3 토글 확인.
7. 매일 `/api/monitoring/health` 확인 + 알림 채널 점검.
8. 비정상 손실 / API 오류 / 운영자 의심 → 즉시 KillSwitch + `ENABLE_AI_EXECUTION=false`로 되돌림.

---

## 8. API

### `POST /api/governance/ai-execution-gate/evaluate`

read-only readiness 평가. 안전 플래그 / opt-in / 한도 / 운영 로그를 입력으로
받아 verdict 반환. **본 endpoint는 어떤 값도 mutate 하지 않는다.**

### `GET /api/governance/ai-execution-gate/policy`

기본 제한 + required gates + `futures_allowed=false` 정보. settings / DB / broker
접근 0건.

응답 예:
```json
{
  "futures_allowed": false,
  "activation_requires_separate_pr": true,
  "limits": { "max_order_notional_krw": 30000, ... },
  "required_gates": ["promotion_gate", "paper_gate", "ai_assist_gate", "live_manual_gate"],
  "required_infrastructure": ["risk_manager_active", "order_guard_active", ...],
  "disclaimer": "본 게이트의 PASS(READY_FOR_REVIEW)는 *실제 활성화가 아니다*. ..."
}
```

---

## 9. UI

`frontend/src/components/tabs/AIExecutionGateCard.jsx`:

- 표시: verdict 배지 + 전제 게이트 / 안전 인프라 / 극소액 정책 / 거래 시간 / blocked / cautions / actions
- 위험 문구 *항상* 노출: "이 화면은 활성화 *평가만* 하며 실제 모드를 켜지 않습니다."
- 선물 영구 차단 banner *항상* 노출: "선물 AI Execution은 본 게이트가 *영구* 허용하지 않습니다 (futures_allowed=false)."
- **활성화 버튼 0개** — "활성화 검토 평가" 버튼만 (테스트로 lock).
- "AI 자동매매 켜기" / "AI 자동매매 시작" / "AI 자동매매 활성화" /
  "LIVE_AI_EXECUTION 활성화" / "ENABLE_AI_EXECUTION" / "주문 시작" / "Place Order" /
  "실거래 활성화" / "활성화 토글" 라벨 0건 (테스트로 lock).
- BUY / SELL / HOLD / 긴급정지 토글 문구 0건.
- Secret 패턴 0건.

---

## 10. 절대 원칙 — 본 모듈 강제

`tests/test_ai_execution_gate_activation.py`의 정적 grep 가드:

1. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
3. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건.
4. `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건.
5. `from app.core.config import` / `get_settings(` 호출 0건 — evaluator는
   안전 플래그를 *입력 DTO*로만 받음.
6. `AIExecutionActivationGateResult.is_live_authorization=True` 생성 불가.
7. `AIExecutionActivationGateResult.is_order_signal=True` 생성 불가.
8. `AIExecutionActivationGateResult.is_investment_advice=True` 생성 불가.
9. `AIExecutionActivationGateResult.futures_allowed=True` 생성 *영구* 불가.
10. UI: "AI 자동매매 켜기" / "ENABLE_AI_EXECUTION" / "활성화 토글" / "주문 시작" /
    "Place Order" / "실거래 활성화" 라벨 버튼 0개.
11. UI / 응답 / 리포트에 BUY / SELL / HOLD / 긴급정지 토글 문구 0건.
12. 응답에 Secret 패턴 0건.

---

## 11. 후속 backlog

- env override (`AI_EXECUTION_MAX_ORDER_NOTIONAL` 등)
- 자동 collector — 다른 게이트들의 verdict / metrics를 자동 가져와 입력 채움
- KillSwitch drill 자동 검증 — 최근 N일 내 토글 이력 확인
- Circuit Breaker 설정 자동 추적
- 실시간 monitoring 메트릭과 게이트 평가 연동
- 별도 PR로 `LIVE_AI_EXECUTION` 활성화 가이드 (`live_ai_execution_activation_runbook.md`)
- AIExecutionGate(#45 order-time)와 본 게이트(#75 activation)의 *결합 평가* 페이지

---

## 12. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격
- [`docs/strategy_promotion_gate.md`](strategy_promotion_gate.md) — #27 Promotion Gate
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — #72 Paper Gate
- [`docs/live_manual_gate.md`](live_manual_gate.md) — #73 Live Manual Gate
- [`docs/ai_assist_gate.md`](ai_assist_gate.md) — #74 AI Assist Gate
- [`docs/ai_permission_gate.md`](ai_permission_gate.md) — #39 AI Permission Gate
- [`docs/ai_execution_policy.md`](ai_execution_policy.md) — #45 AI Execution Gate (order-time, 12 가드)
- [`docs/emergency_stop_policy.md`](emergency_stop_policy.md) — #37 3-Level Kill Switch
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager 단일 진입점
- [`docs/order_executor_contract.md`](order_executor_contract.md) — #40 OrderExecutor 단일 진입점
- [`docs/order_guard_policy.md`](order_guard_policy.md) — #38 OrderGuard
- [`docs/manual_approval_policy.md`](manual_approval_policy.md) — #41 Manual Approval
- [`docs/audit_log_policy.md`](audit_log_policy.md) — #68 Audit Event facade
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 진입 blocker (선물 9단계)
- [`docs/futures_scope.md`](futures_scope.md) — 선물 범위 (본 게이트로 *영구* 차단)
