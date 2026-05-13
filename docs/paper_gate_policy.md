# Paper Gate Policy — 체크리스트 #72

> Paper 모드 4주 이상 운용 결과를 평가하는 코드 단 정책 + CLI / API.
> 본 게이트의 PASS는 **Live Manual Approval *검토 가능*** 을 의미하며
> **실거래 자동 허가가 아니다**.

---

## 1. 목적

- 실시간 환경 안정성 검증 — Paper 모드는 실 시세를 사용하는 KIS 모의투자
  환경이며, backtest와 달리 부분체결 / 거부 응답 / FillPolling / 데이터
  지연 등 *실 환경 요인*을 직접 경험한다.
- promotion_policy.md의 Paper 단계 정량 기준을 코드 단에서 일관 적용해
  운영자의 임의 판단 가능성을 줄인다.
- 결과는 markdown 리포트 / JSON 으로 보관해 추후 LIVE 승격 PR에서 근거 자료로
  활용.

본 게이트는 **수익 자동화가 아니라 안정성 검증**을 우선한다.

---

## 2. PASS 기준 (모든 항목 충족)

| # | 기준 | 임계 | 출처 |
|---|---|---|---|
| 1 | 운영 기간 | ≥ **28일** | `MIN_ACTIVE_DAYS` |
| 2 | 매매 신호 / 체결 주문 수 | ≥ **100건** | `MIN_TRADE_COUNT` |
| 3 | 기대값 (expectancy) | **> 0** | `MIN_EXPECTANCY` |
| 4 | Profit Factor | ≥ **1.2** | `MIN_PROFIT_FACTOR` |
| 5 | MDD (초기 자본 대비) | ≤ **15%** | `MAX_DRAWDOWN_PCT` |
| 6 | 손실한도 위반 일수 | **= 0** | `MAX_LOSS_LIMIT_VIOLATIONS` |
| 7 | OrderAuditLog 누락 | **= 0** | `MAX_AUDIT_MISSING` |
| 8 | stale / duplicate 위반 | **= 0** | `MAX_STALE_OR_DUPLICATE` |
| 9 | FillPolling 정합성 | OK | broker view ↔ audit 일치 |
| 10 | client_order_id idempotency | OK | 같은 ID 중복 주문 거부 확인 |

모든 임계는 `app/governance/paper_gate.py::PaperGateThresholds` dataclass의
default. 운영자가 평가 시 override 가능 (env / API payload).

---

## 3. CAUTION 기준 (PASS 라벨에도 운영자 검토 권장)

| 사유 | 임계 |
|---|---|
| 특정 하루 손익 의존 (best_day_pnl_share) | > **50%** |
| Rejection 비율 | > **30%** |
| 특정 시간대 손실 집중 (hourly_loss_top_share) | > **60%** |
| Paper ↔ Backtest PF 괴리 | \| paper_pf − bt_pf \| / bt_pf > **0.5** |

추가 CAUTION 후속 PR:
- `ai_low_confidence_burst` 비율
- `agent_warn_burst`
- 특정 종목 의존도 (top symbol pnl share)
- `active_days` < `period_days` 비율

---

## 4. FAIL 사유

다음 중 하나라도 위반 시 FAIL:

- 운영 기간 < 28일
- 매매 신호 < 100건
- 기대값 ≤ 0
- PF < 1.2 (또는 표본 부족으로 계산 불가)
- MDD > 15%
- 손실한도 위반 ≥ 1
- audit 누락 ≥ 1
- stale / duplicate 위반 ≥ 1
- FillPolling 정합성 실패
- client_order_id idempotency 실패

---

## 5. 결과 해석

| Verdict | 의미 |
|---|---|
| **PASS** | Live Manual Approval *검토 가능*. **실거래 자동 허가 아님**. |
| **CAUTION** | PASS 임계 충족이지만 CAUTION 사유 있음 — 운영자 검토 권장. |
| **FAIL** | Paper / Shadow 추가 운용으로 표본·지표 재확보 필요. |
| **UNKNOWN** | 데이터 부족 — 보수적으로 FAIL 취급 권장. |

### PASS = 실거래 허가가 아닌 이유

1. AI Assist / AI Execution 단계는 별도 Gate(`AIPermissionGate` #39, `AIExecutionGate` #45) 필요.
2. LIVE 모드 진입에는 `ENABLE_LIVE_TRADING=true` 환경변수 + `LIVE_MANUAL_APPROVAL`
   라우팅 PR + 사용자 명시 승인 필요.
3. 선물 LIVE는 `live_activation_blockers.md` §3.1 9단계 blocker 통과 필요.
4. RiskManager / PermissionGate / OrderExecutor 우회 금지 (CLAUDE.md 원칙 1~2).

`PaperGateResult.is_live_authorization=False` 가 dataclass `__post_init__` 에서
강제 — True로 생성 불가 (ValueError).

---

## 6. 리포트 생성 방법

### CLI

```bash
# 운영 DB + 자동 28일 윈도우 (JSON)
python scripts/evaluate_paper_gate.py --strategy sma_cross --format json

# 명시 기간 + markdown 파일 저장
python scripts/evaluate_paper_gate.py \
  --strategy sma_cross \
  --period-start 2026-04-15 --period-end 2026-05-13 \
  --format markdown --output reports/paper_gate_sma_cross.md

# DB 미연결 dry-run (수동 메트릭)
python scripts/evaluate_paper_gate.py --dry-run \
  --strategy sma_cross \
  --trade-count 120 --active-days 22 --expectancy 350 \
  --pf-numerator 200000 --pf-denominator 150000 \
  --max-drawdown-value 800000 \
  --format markdown
```

CLI exit code:
- `0`: PASS / CAUTION / UNKNOWN
- `1`: FAIL
- `2`: 실행 오류 (DB 연결 실패 등)

### API

```http
POST /api/governance/paper-gate/evaluate
Content-Type: application/json

{
  "strategy_name":      "sma_cross",
  "trade_count":        120,
  "active_days":        22,
  "winning_pnl_sum":    200000,
  "losing_pnl_sum":     150000,
  "expectancy":         350.0,
  "max_drawdown_value": 800000
}
```

응답에는 `is_live_authorization=false`, `live_flag_changed=false`,
`mode_changed=false` invariant 필드가 항상 포함된다.

---

## 7. 데이터 소스 — read-only

| 테이블 | 용도 | 본 모듈 사용 |
|---|---|---|
| `OrderAuditLog` | trade_count / rejection / active_days / audit drift | ✅ SELECT only |
| `PendingApproval` | (후속) approval queue health | — |
| `EmergencyStopEvent` | (후속) loss limit / emergency 사례 | — |
| `AgentDecisionLog` | (후속) AI confidence burst CAUTION | — |
| `VirtualOrder` | (선택) virtual 자금 흐름 보조 | — |
| `BacktestRun` | (선택) backtest PF 비교 | — |

수익 메트릭 (expectancy / winning / losing / mdd)은 *별도 trade ledger*
또는 운영자가 산출한 값을 옵션 인자로 받는다. 본 collector는 OrderAuditLog
만으로 손익을 추정하지 않는다 — 부분체결 / 슬리피지 등을 정확히 다루지 못함.

---

## 8. 실거래 전 다음 단계

1. **Live Manual Approval 검토** — `LIVE_MANUAL_APPROVAL` 라우팅 PR + 사용자
   명시 승인. 본 단계 PASS 후에만 진입 가능.
2. **초소액 LIVE 시작** — 1주 이상 *초소액*으로 실제 broker 흐름 검증.
3. **별도 Promotion Gate** — `evaluate_promotion()` (#27) 이 LIVE 단계
   추가 기준 (사람 승인 + 코드 기준 + 데이터 품질) 검사.
4. **AI 단계** — LIVE_AI_ASSIST / LIVE_AI_EXECUTION 은 추가 8개 옵트인
   조건 (promotion_policy.md) 모두 필요.

---

## 9. 절대 원칙 — 본 모듈 강제

`tests/test_paper_gate.py`의 정적 grep 가드로 강제:

1. broker / OrderExecutor / route_order / paper_trader / external HTTP /
   AI SDK import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` 호출 0건.
3. `submit_candidate(` (`app.ai.assist`) 호출 0건.
4. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건.
5. `settings.enable_*_trading =` 등 안전 플래그 mutate 0건.
6. `os.environ["ENABLE_*"] = ` 형태 환경변수 mutate 0건.
7. `PaperGateResult.is_live_authorization=True` 생성 불가 (ValueError).
8. `PaperGateResult.is_order_signal=True` 생성 불가 (ValueError).
9. 응답 / 리포트에 BUY / SELL / HOLD 등 주문 신호 문구 0건 (테스트로 lock).
10. 응답에 Secret 패턴 (`KIS_APP_KEY`, `ANTHROPIC_API_KEY`,
    `TELEGRAM_BOT_TOKEN`, `sk-`, `Bearer ` 등) 0건.

---

## 10. 후속 backlog

- env override (`PAPER_GATE_MIN_PROFIT_FACTOR` 등)
- 자동 백테스트 ↔ paper PF drift 계산 (현재 운영자 수동 입력)
- 시간대 손실 집중 자동 계산
- 일별 손익 자동 산출 (체결 ledger 통합)
- LIVE_SHADOW 사전 통과 검증 연동 (#43 ShadowTrade row)
- 운영자 승인 / reject 이력 carry (signed by + note)
- 알림 시스템 연계 (PASS / FAIL → NotificationService)

---

## 11. 참고

- [`docs/promotion_policy.md`](promotion_policy.md) — 전체 승격 단계 정책
- [`docs/paper_mode.md`](paper_mode.md) — Paper 모드 운영 가이드
- [`docs/manual_approval_policy.md`](manual_approval_policy.md) — Live Manual Approval (#41)
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — LIVE 진입 blocker
- [`docs/strategy_promotion_gate.md`](strategy_promotion_gate.md) — 전체 Promotion Gate
- [`docs/risk_policy.md`](risk_policy.md) — RiskPolicy 손실한도 기준
- [`docs/mvp_completion.md`](mvp_completion.md) — MVP 판정 (#71)
