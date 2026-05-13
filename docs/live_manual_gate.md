# Live Manual Gate Policy — 체크리스트 #73

> 초소액 실거래는 **반드시 `LIVE_MANUAL_APPROVAL` 모드에서만** 시작한다.
> 본 게이트는 *진입 readiness 평가*만 수행하며, **본 PR로 어떤 LIVE 플래그도
> 활성화되지 않는다**.

---

## 1. 목적

- 소액 실거래는 *수동승인 모드에서만* 시작 — 사용자가 직접 주문을 넣으며
  체결 / 슬리피지 / 부분체결 / 거부 응답 / 긴급정지 흐름의 *실 품질*을 확인.
- AI 자동매매 / 선물 LIVE / 무인 LIVE는 본 단계에서 *전면 금지*.
- 본 게이트는 코드 단 + 문서 + 테스트로 진입 조건을 고정한다 — 운영자가
  실수로 LIVE를 켜는 시나리오를 방어.

---

## 2. 전제조건 (모두 충족 시에만 PASS)

| # | 조건 | 출처 |
|---|---|---|
| 1 | Paper Gate(#72) PASS | `docs/paper_gate_policy.md` |
| 2 | Promotion Gate(#27) PASS for `LIVE_MANUAL_APPROVAL` target | `docs/strategy_promotion_gate.md` |
| 3 | 운영자 explicit opt-in (자동 X) | 사용자 명시 (별도 PR / 운영 노트) |
| 4 | `requires_user_approval=True` (LIVE_MANUAL_APPROVAL capability) | `app/core/modes.py` |
| 5 | `ENABLE_AI_EXECUTION=false` | `docs/promotion_policy.md` |
| 6 | `ENABLE_FUTURES_LIVE_TRADING=false` | `docs/futures_scope.md` |

본 게이트는 위 값들을 *입력으로만* 받는다 — 어떤 값도 *변경하지 않는다*
(정적 grep 가드, `test_live_manual_gate_modules_do_not_mutate_safety_flags`).

---

## 3. 극소액 정책 (default 임계, override 가능)

| 항목 | 권장 | 강제 임계 |
|---|---|---|
| 1회 주문 금액 (`max_order_notional`) | 10,000~50,000원 | ≤ **50,000원** |
| 일일 손실한도 (`max_daily_loss`) | 약 10,000원 | ≤ **10,000원** |
| 동시 보유 종목 (`max_positions`) | 1~3개 | ≤ **3개** |
| 허용 종목 (`allowed_symbols`) | watchlist 명시 권장 | 미지정 시 CAUTION |

`RiskPolicy` 한도가 위 임계를 초과하면 즉시 BLOCKED. 본 게이트는 `RiskPolicy`
자체를 *수정하지 않는다* — 운영자가 RiskPolicy를 보수적으로 조정한 후 본
게이트를 재평가한다.

---

## 4. 운영 기간 / 시스템 안정성 기준

| 항목 | 기준 |
|---|---|
| 최소 운영 일수 (CAUTION 기준) | ≥ **30일** |
| 시스템 오류 (`system_errors`) | **= 0** |
| OrderAuditLog 누락 (`audit_missing_count`) | **= 0** |
| Approval 우회 시도 (`approval_bypass_attempts`) | **= 0** |

특히 *Approval 우회 시도*는 `LIVE_MANUAL_APPROVAL` 모드에서 OrderAuditLog
가 `APPROVED + executed=True` 인데 `PendingApproval` 큐 row가 없는 경우를
탐지한다 — 정상 흐름은 *모든* live 주문이 큐를 거쳐야 한다.

---

## 5. 주문 흐름 (Approval API 강제)

```text
User / Strategy / AI Assist
    │
    ▼
route_order  ─────────────────  (단일 진입점 #34)
    │
    ▼
RiskManager.evaluate_order  (1차 검사)
    │
    ▼
LIVE_MANUAL_APPROVAL → PendingApproval 큐 등록 (NEEDS_APPROVAL)
    │
    │   broker.place_order 호출 X — 운영자 결재 전.
    │
    ▼
운영자가 POST /api/approvals/{id}/approve
    │
    ▼
PermissionGate.approve → RiskManager 재검증 (#070)
    │
    ▼
OrderExecutor.execute  ──────  (단일 유일 broker 호출 지점 #40)
    │
    ▼
broker.place_order (Mock 또는 KIS — `KIS_IS_PAPER`/`ENABLE_LIVE_TRADING` 에 의해 결정)
```

핵심 invariant:

1. **AI / Strategy / UI가 직접 broker.place_order 호출 0건** — 정적 grep 가드.
2. **승인 없이 OrderExecutor 진입 불가** — `OrderExecutor.execute` 가
   `audit.decision ∈ {APPROVED, NEEDS_APPROVAL}` 만 broker 호출, 그 외는
   `UnauthorizedOrderError` (마지막 backstop).
3. **approve 시점 RiskManager 재검증** — broker 호출 *전* `PermissionGate.approve`
   에서 다시 evaluate (#070). 실패 시 status=PENDING 유지 + attempts 누적.

---

## 6. 금지 사항

- 🚫 AI 자동 실행 (`ENABLE_AI_EXECUTION=true`) — 별도 옵트인 PR + 8개 조건.
- 🚫 무인 LIVE — `LIVE_MANUAL_APPROVAL` 모드의 capability 자체가 사람 승인 강제.
- 🚫 선물 LIVE — 9단계 blocker (`live_activation_blockers.md` §3.1).
- 🚫 approval 우회 — `OrderExecutor.execute` 단일 진입점 우회 시도는 즉시 차단.
- 🚫 본 게이트로 LIVE flag 변경 — `evaluate_live_manual_gate()` 는 *판단 결과*만
  반환하며, `settings.enable_live_trading=True` 같은 mutate 절대 0건.

---

## 7. Gate 결과 해석

| Verdict | 의미 | 다음 단계 |
|---|---|---|
| **PASS** | LIVE_MANUAL_APPROVAL 모드 진입 *검토 가능*. **실거래 자동 허가 아님**. | 별도 옵트인 PR + 사용자 명시 승인 + KIS 실주문 라우팅 활성화 PR |
| **CAUTION** | PASS 임계는 충족했지만 운영 기간 / `ENABLE_LIVE_TRADING=true` 등 검토 사유 존재 | 사유 점검 후 재평가 |
| **BLOCKED** | 전제 조건 / 안전 플래그 / 운영 데이터 중 하나 이상 미달 | required_actions 절차 후 재평가 |
| **UNKNOWN** | 입력 부족 — 보수적으로 BLOCKED 취급 권장 | 입력 데이터 확보 |

---

## 8. 운영 로그 평가

`summarize_live_manual_period(db, start_date, end_date)` 가 다음을 집계:

- `total_live_manual_orders`: LIVE_MANUAL_APPROVAL 모드 audit row 수
- `approved_orders` / `needs_approval_orders` / `rejected_orders`
- `pending_approval_rows` / `approved_via_queue` / `expired_or_cancelled`
- `approval_bypass_attempts`: 우회 의심 카운트
- `emergency_stops_in_period`
- `operating_days`

본 helper는 *read-only SELECT*만 수행한다 — DB write 0건 (정적 grep 가드).

API: `GET /api/governance/live-manual-gate/period-summary?period_start=...&period_end=...`

---

## 9. UI

`frontend/src/components/tabs/LiveManualGateCard.jsx`:

- 표시: verdict 배지 + 전제 조건 / 안전 플래그 / 극소액 정책 / blocked / cautions / actions
- 위험 문구 *항상* 노출: "PASS는 실거래 자동 허가가 아니라, 초소액 수동승인 검토 가능 상태입니다."
- **활성화 버튼 없음** — "활성화 가능성 평가" 버튼만 존재 (테스트로 lock).
- BUY/SELL/HOLD/긴급정지 토글/LIVE 켜기 버튼 0개 (테스트로 lock).
- Secret 패턴 0건 (테스트로 lock).

---

## 10. 실전 전 추가 검증 (PASS 후에도)

- 실제 KIS API 장애 대응 시나리오 — Circuit breaker / fallback.
- 부분체결 audit 정합성 — `filled_quantity` 부분 갱신.
- 슬리피지 측정 — 시장 깊이 / 호가 공백.
- 주문 취소 흐름 — `KisBrokerAdapter.cancel_order` 활성화 시.
- 긴급정지 drill — LEVEL_1 / LEVEL_2 / LEVEL_3 모의 훈련.

위 항목은 본 게이트가 *직접 검증하지 않는다* — 별도 PR / 운영자 매뉴얼.

---

## 11. 절대 원칙 — 본 모듈 강제

`tests/test_live_manual_gate.py`의 정적 grep 가드로 강제:

1. broker / OrderExecutor / route_order / paper_trader / 외부 HTTP / AI SDK import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` / `submit_candidate(` 호출 0건.
3. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건 (collector + evaluator).
4. `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건.
5. `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만 사용).
6. `LiveManualGateResult.is_live_authorization=True` 생성 불가 (ValueError).
7. `LiveManualGateResult.is_order_signal=True` 생성 불가 (ValueError).
8. UI 카드에 "실거래 활성화" / "실거래 시작" / "LIVE 켜기" / "Place Order" / "주문 실행" 라벨 버튼 0개.
9. UI / 응답 / 리포트에 BUY / SELL / HOLD / 긴급정지 토글 0건.
10. 응답에 Secret 패턴 (`KIS_APP_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `sk-`, `Bearer ` 등) 0건.

---

## 12. 후속 backlog

- env override (`LIVE_MANUAL_MAX_ORDER_NOTIONAL` 등)
- KIS Paper 운영에서 자동 metrics carry — 현재는 운영자 수동 입력
- Promotion Gate 통합 — 본 게이트가 promotion evaluator를 *자동 호출*하도록
- Notification 연계 — verdict 변동 시 운영자 알림
- Frontend Settings 탭에 "user_explicit_opt_in 토글" 추가 (현재는 입력으로만)
- `live_activation_blockers.md` 의 9단계 checklist 자동 검증

---

## 13. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격
- [`docs/manual_approval_policy.md`](manual_approval_policy.md) — Manual Approval (#41)
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — RiskManager 단일 진입점 (#34)
- [`docs/order_executor_contract.md`](order_executor_contract.md) — OrderExecutor 단일 진입점 (#40)
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 진입 blocker
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — Paper Gate (#72)
- [`docs/mvp_completion.md`](mvp_completion.md) — MVP 판정 (#71)
