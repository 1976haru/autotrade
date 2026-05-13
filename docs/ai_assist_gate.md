# AI Assist Gate Policy — 체크리스트 #74

> LIVE_AI_ASSIST 모드의 AI 제안 품질을 검증하는 *read-only 분석 게이트*.
> 본 게이트의 PASS는 **`LIVE_AI_EXECUTION` 자동 허가가 *아니다***.
> AI 자동매매 활성화는 `AIExecutionGate`(#45) + 별도 옵트인 PR + 사용자
> 명시 승인 모두 필요하다.

---

## 1. 목적

- AI 자동매매로 넘어가기 전 **AI Assist 단계의 품질을 시스템 단에서 정량 검증**.
- AI 제안이 사람 승인 / RiskManager 사전검사 / 실제 결과 / 위험 이벤트와 어떤
  관계가 있었는지 추적.
- AI 자동 실행(`LIVE_AI_EXECUTION`) 진입의 *필수* 검증 단계.
- 본 리포트는 **투자 조언이 아니라 시스템 검증 자료**.

---

## 2. PASS 기준 (모두 충족 시에만 PASS)

| # | 기준 | 임계 | 출처 |
|---|---|---|---|
| 1 | AI 제안 수 (`proposal_count`) | ≥ **100건** | `MIN_PROPOSAL_COUNT` |
| 2 | 운영 기간 (`period_days`) | ≥ **28일** | `MIN_ACTIVE_DAYS` |
| 3 | 승인 제안 expectancy (`approved_expectancy`) | **> 0** | `MIN_APPROVED_EXPECTANCY` |
| 4 | 승인 제안 손실율 (`approved_loss_rate`) | ≤ **55%** | `MAX_APPROVED_LOSS_RATE` |
| 5 | RiskManager 거절율 (`risk_rejection_rate`) | ≤ **60%** | `MAX_RISK_REJECTION_RATE` |
| 6 | 운영자 거절율 (`operator_rejection_rate`) | ≤ **50%** | `MAX_OPERATOR_REJECTION_RATE` |
| 7 | Confidence calibration | ≥ **0.5** | `MIN_CONFIDENCE_CALIBRATION` |
| 8 | AI 결정 audit 누락 (`ai_decision_audit_drift`) | **= 0** | `MAX_AI_DECISION_AUDIT_DRIFT` |
| 9 | 긴급정지 (`emergency_stops_in_period`) | ≤ **2회** | `MAX_EMERGENCY_STOPS_IN_PERIOD` |

---

## 3. CAUTION 기준 (PASS 임계 통과해도 운영자 검토 권장)

| 사유 | 임계 |
|---|---|
| 만료 / 취소 비율 (`expired_or_cancelled_rate`) | > **30%** |
| Confidence calibration 약함 | < **0.65** (but ≥ 0.5) |
| 거절했으나 사후 유리했던 비율 | > **25%** (rejected_but_would_have_won) |
| 단일 failure reason 집중 | > **40%** of total failures |

추가 CAUTION 후속 PR:
- ai_low_confidence_burst (분 단위 burst)
- 종목별 / 시간대별 AI 신호 분포 편향
- AI vs 비-AI 주문 결과 비교

---

## 4. FAIL 사유

다음 중 하나라도 위반 시 FAIL:

- proposal_count < 100
- period_days < 28
- approved_expectancy ≤ 0
- approved_loss_rate > 55%
- risk_rejection_rate > 60%
- operator_rejection_rate > 50%
- confidence_calibration < 0.5
- ai_decision_audit_drift ≥ 1
- emergency_stops_in_period > 2

---

## 5. Failure Reason 태그 (advisory only)

`AIAssistFailureReason` enum:

| Tag | 의미 |
|---|---|
| `low_confidence`              | AI confidence 낮음 + 거절 |
| `data_stale`                  | 데이터 freshness 위반 |
| `price_gap`                   | 가격 갭 / 분기 |
| `liquidity`                   | 거래량 / 호가 부족 |
| `risk_limit`                  | max_daily_loss / notional / exposure 위반 |
| `operator_rejected`           | 운영자가 결재 거절 |
| `approval_expired`            | TTL 만료 / 취소 |
| `emergency_stop`              | 긴급정지 활성 시 거절 |
| `regime_mismatch`             | 시장 regime ↔ 전략 매칭 실패 |
| `news_or_theme_overheated`    | 테마 과열 |
| `duplicate_or_cooldown`       | OrderGuard 차단 |
| `uncategorized`               | 자동 분류 실패 |

**BUY / SELL / HOLD 같은 *주문 신호*는 enum에 없다** — 본 enum은 advisory tag
전용 (테스트로 lock).

---

## 6. 결과 해석

| Verdict | 의미 |
|---|---|
| **PASS** | AI Assist 품질이 *다음 검증 단계*로 진입 검토 가능. **LIVE_AI_EXECUTION 자동 허가 아님**. |
| **CAUTION** | PASS 임계 충족이지만 CAUTION 사유 있음 — 운영자 검토 권장. |
| **FAIL** | AI 제안 품질 미달 — 추가 운용으로 표본 / 지표 보완 필요. |
| **UNKNOWN** | 데이터 부족 — 보수적으로 FAIL 취급 권장. |

### PASS = LIVE_AI_EXECUTION 허가가 아닌 이유

1. **AIExecutionGate(#45) 별도 통과 필요** — 12개 가드 + canary mode default.
2. **`ENABLE_AI_EXECUTION=true` 전환은 별도 옵트인 PR** — 현재 default false.
3. **8개 옵트인 조건** — `promotion_policy.md`의 LIVE_AI_EXECUTION 진입 조건.
4. **RiskManager / PermissionGate / OrderExecutor 우회 금지** (CLAUDE.md 원칙 1~2).

`AIAssistGateResult.is_live_authorization=False` 는 dataclass `__post_init__`
에서 강제 (True 생성 시 ValueError). 마찬가지로 `is_order_signal=False`,
`is_investment_advice=False` 도 invariant.

---

## 7. 리포트 생성 방법

### CLI

```bash
# 운영 DB + 자동 28일 윈도우 (JSON)
python scripts/evaluate_ai_assist_gate.py --strategy ai_signals --format json

# markdown 리포트 파일 저장 (수익 메트릭 명시)
python scripts/evaluate_ai_assist_gate.py \
  --strategy ai_signals \
  --period-start 2026-04-15 --period-end 2026-05-13 \
  --approved-expectancy 250 \
  --approved-win-count 35 --approved-loss-count 25 \
  --format markdown --output reports/ai_assist_ai_signals.md

# DB 없이 dry-run (수동 메트릭)
python scripts/evaluate_ai_assist_gate.py --dry-run \
  --strategy ai_signals \
  --proposal-count 150 --approved-proposals 60 \
  --risk-rejected-proposals 30 --operator-rejected-proposals 40 \
  --approved-expectancy 200 --approved-win-count 35 --approved-loss-count 25 \
  --confidence-calibration 0.7
```

exit code: PASS/CAUTION/UNKNOWN=0, FAIL=1, 실행 오류=2.

### API

```http
POST /api/governance/ai-assist-gate/evaluate
Content-Type: application/json

{
  "strategy_name":              "ai_signals",
  "proposal_count":             150,
  "approved_proposals":         80,
  "risk_rejected_proposals":    30,
  "operator_rejected_proposals": 30,
  "expired_or_cancelled":       10,
  "approved_expectancy":        250.0,
  "approved_winning_pnl_sum":   200000,
  "approved_losing_pnl_sum":    120000,
  "approved_win_count":         50,
  "approved_loss_count":        30,
  "confidence_calibration":     0.75,
  "active_days":                22
}
```

응답에는 `is_live_authorization=false`, `is_order_signal=false`,
`is_investment_advice=false`, `live_flag_changed=false`, `mode_changed=false`
invariant 필드가 *항상* 포함된다.

---

## 8. 데이터 소스 — read-only

| 테이블 | 용도 | 본 모듈 사용 |
|---|---|---|
| `OrderAuditLog` (`trade_reason='ai_assist'` 또는 `requested_by_ai=True`) | proposal_count / decision / executed | ✅ SELECT only |
| `PendingApproval` (audit_id join) | operator approve/reject/expired/cancelled | ✅ SELECT only |
| `EmergencyStopEvent` | 기간 내 긴급정지 | ✅ SELECT only |
| `AgentDecisionLog` | (선택) AI 결정 추적 보강 | — (후속 PR) |

수익 메트릭(approved_expectancy / win_count / loss_count)은 trade ledger / 운영자
입력 — 본 collector는 직접 계산하지 않는다.

---

## 9. UI

`frontend/src/components/tabs/AIAssistGateCard.jsx`:

- 표시: verdict 배지 + 핵심 메트릭 8개 + 실패 사유 태그 / FAIL 사유 / CAUTION
- 위험 문구 *항상* 노출: "본 리포트는 *투자 조언이 아니라* AI Assist 기능의 시스템 검증 자료입니다."
- **AI 자동매매 / LIVE_AI_EXECUTION 활성화 버튼 0개** — "AI Assist 품질 평가" 버튼만 (테스트로 lock).
- BUY / SELL / HOLD / 긴급정지 토글 / 주문 실행 라벨 0건 (테스트로 lock).
- Secret 패턴 0건 (테스트로 lock).

---

## 10. 절대 원칙 — 본 모듈 강제

`tests/test_ai_assist_gate.py`의 정적 grep 가드:

1. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
3. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건 (evaluator + collector).
4. `settings.enable_*_trading =` / `os.environ["ENABLE_*"]` mutate 0건.
5. `from app.core.config import` / `get_settings(` 호출 0건 (evaluator는 입력 DTO만).
6. `AIAssistGateResult.is_live_authorization=True` 생성 불가 (ValueError).
7. `AIAssistGateResult.is_order_signal=True` 생성 불가 (ValueError).
8. `AIAssistGateResult.is_investment_advice=True` 생성 불가 (ValueError).
9. `AIAssistFailureReason` enum 에 BUY / SELL / HOLD 값 0개.
10. UI 카드에 "AI 자동매매 시작" / "LIVE_AI_EXECUTION 활성화" / "ENABLE_AI_EXECUTION" /
    "AI 자동 실행" / "Place Order" / "주문 실행" / "실거래 활성화" 라벨 버튼 0개.
11. UI / 응답 / 리포트에 BUY / SELL / HOLD / 긴급정지 토글 문구 0건.
12. 응답 / 리포트에 Secret 패턴 0건.

---

## 11. 후속 backlog

- env override (`AI_ASSIST_MIN_PROPOSAL_COUNT` 등)
- 수익 메트릭 자동 산출 (trade ledger 통합)
- confidence calibration 정교화 (현재는 ≥70 heuristic)
- 종목 / 시간대 / regime별 AI 신호 분포 분석
- AI vs 비-AI 주문 결과 비교 (선택성 검증)
- Notification 연계 — 매주 자동 평가 + 운영자 알림
- AgentDecisionLog 통합 — chain_id 별 의사결정 사슬 trace

---

## 12. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격
- [`docs/ai_assisted_trading_policy.md`](ai_assisted_trading_policy.md) — #44 LIVE_AI_ASSIST 흐름
- [`docs/ai_permission_gate.md`](ai_permission_gate.md) — #39 AI 권한 5×5 매트릭스
- [`docs/ai_execution_policy.md`](ai_execution_policy.md) — #45 AI 실행 게이트 (12개 가드, default BLOCKED)
- [`docs/manual_approval_policy.md`](manual_approval_policy.md) — #41 Manual Approval
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager 단일 진입점
- [`docs/order_executor_contract.md`](order_executor_contract.md) — #40 OrderExecutor 단일 진입점
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — #72 Paper Gate
- [`docs/live_manual_gate.md`](live_manual_gate.md) — #73 Live Manual Gate
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 진입 blocker
