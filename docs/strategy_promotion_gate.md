# Strategy Promotion Gate (체크리스트 #27)

## 1. 목적

`docs/promotion_policy.md`의 단계별 승격 기준을 **코드 단으로 강제**한다. 검증되지 않은 전략이 Paper / Live Manual / AI Assist / AI Execution 단계로 올라가지 못하도록 `app/governance/strategy_promotion.py`에서 *판단 결과*를 산출한다.

본 모듈은 **판단만** 한다 — 실제 모드 변경 / broker 호출 / `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` 플래그 변경은 하지 않는다 (CLAUDE.md 절대 원칙). 호출자(운영자 또는 별도 옵트인 PR)가 결과를 보고 직접 결정한다.

## 2. 단계별 승격 기준

| 현재 → 목표 | 핵심 추가 기준 |
|---|---|
| `BACKTEST` → `LIVE_SHADOW` | 공통 백테스트 + Walk-forward 필수 + Data Quality |
| `LIVE_SHADOW` → `PAPER` | Shadow 운영 ≥28일 + audit 누락 0회 |
| `PAPER` → `LIVE_MANUAL_APPROVAL` | Paper ≥28일 + violations 0회 + Monte Carlo 필수 + 사람 승인 |
| `LIVE_MANUAL_APPROVAL` → `LIVE_AI_ASSIST` | LIVE_MANUAL ≥28일 + AI 추천 정확도 ≥60% + 사람 승인 |
| `LIVE_AI_ASSIST` → `LIVE_AI_EXECUTION` | **영구 BLOCKED** — 별도 옵트인 PR + `ENABLE_AI_EXECUTION=true` 필요 |

**한 번에 한 단계씩만** — `BACKTEST → PAPER` 같은 다단 점프는 즉시 BLOCKED.

## 3. 공통 코드 기준

모든 단계에 적용:

| 기준 | 한도 |
|---|---|
| `trade_count ≥ 100` | 표본 부족 시 통계 신뢰도 낮음 |
| `expectancy > 0` | 평균적으로 손실이면 승격 불가 |
| `profit_factor ≥ 1.2` | 손실 0건이면 None → FAIL (표본 신뢰성 부족) |
| `max_drawdown ≤ initial_cash × 15%` | 운영 자본 보호 |
| `max_consecutive_losses ≤ 5` | 운영 자본 / 운영자 심리 |
| `cost_adjusted == True` | 수수료/세금 미반영 백테스트 거부 (#23) |
| `slippage_adjusted == True` | 슬리피지 미반영 백테스트 거부 (#23) |

### Walk-forward (#25)
- `walk_forward_recommendation == "PASS"` 권장. `FAIL`은 즉시 차단.
- `positive_fold_ratio ≥ 0.6` (양수 fold 60% 이상).
- `holdout_pnl > 0` (out-of-sample 양수).
- `single_best_fold_pnl_share ≤ 0.7` (한 fold 의존 금지).

### Monte Carlo (#26)
- `risk_of_ruin ≤ 5%` (LIVE 단계). LIVE_AI_EXECUTION은 1% 한도.
- `worst_5pct_avg_mdd ≤ initial_cash × 30%`.
- `longest_losing_streak ≥ 8` 면 사이즈 축소 검토 CAUTION.

### Data Quality (#21)
- `data_quality_score ≥ 75` 권장 (PASS 기준).
- 60~74 → CAUTION, 60 미만 → FAIL.
- `data_quality_grade == "EXCLUDE"` → 즉시 FAIL.

### Paper / Shadow Reality
- `daily_loss_limit_violations == 0`.
- `risk_policy_violations == 0`.
- `audit_log_missing_count == 0`.
- `partial_fill_audit_ok == True`.
- 단계별 운영 일수 ≥28일.

## 4. AI 추천 차단 원칙

**AI 추천만으로는 승격 불가** (CLAUDE.md 절대 원칙):

- `ai_recommended=True`이고 `human_approved=False`이면 LIVE 단계 자동 BLOCKED.
- `failed_criteria` / `warnings` 배열에 "AI 추천만으로는 승격 불가" 메시지 명시.
- AI가 어떤 정확도를 보고하더라도 코드 기준 미달이면 FAIL.

## 5. 사람 승인 필요 조건

- `target_stage in {LIVE_MANUAL_APPROVAL, LIVE_AI_ASSIST, LIVE_AI_EXECUTION}` 일 때 `human_approved == True` 필수.
- 사람 승인 부재 → 코드 기준 PASS여도 BLOCKED.
- 사람 승인 + 코드 기준 미달 → FAIL.

## 6. LIVE_AI_EXECUTION 영구 BLOCKED

본 모듈은 **모든 코드 기준 + 사람 승인까지 PASS여도** LIVE_AI_EXECUTION 승격에 대해 BLOCKED를 반환한다:

- `ruin 1% 한도`까지 통과해도 `decision = BLOCKED`.
- `required_actions`에 "별도 옵트인 PR로 ENABLE_AI_EXECUTION=true + LIVE_AI_EXECUTION 모드 활성화" 명시.
- 본 결과는 *"승격 검토 가능"까지만* — 실제 활성화는 운영자 직접 옵트인 + 환경변수 명시 + 별도 PR.

## 7. PromotionInput / PromotionResult

`PromotionInput` (frozen dataclass):
- `strategy_name`, `current_stage`, `target_stage`.
- 백테스트 metric: `trade_count`, `expectancy`, `profit_factor`, `max_drawdown`, ...
- Walk-forward: `walk_forward_passed`, `walk_forward_recommendation`, `positive_fold_ratio`, ...
- Monte Carlo: `monte_carlo_run`, `monte_carlo_risk_of_ruin`, ...
- Data Quality: `data_quality_score`, `data_quality_grade`.
- 운영: `shadow_days`, `paper_days`, `daily_loss_limit_violations`, `audit_log_missing_count`, ...
- 승인: `human_approved`, `ai_recommended`, `ai_recommendation_accuracy`.

`PromotionResult`:
- `decision`: PASS / CAUTION / FAIL / BLOCKED.
- `failed_criteria`: 실패 사유 배열 (한국어).
- `cautions`: 통과는 했지만 주의 사항.
- `warnings`: 운영자 권고.
- `required_actions`: 다음에 무엇을 해야 하는지.
- `passed_criteria`: 통과한 기준 (감사 로그 / 운영자 신뢰).
- `mode_changed: false`, `live_flag_changed: false` invariant.

## 8. API

`POST /api/governance/strategy-promotion/evaluate` — read-only.

- 요청: `PromotionInputPayload` (위 dataclass와 1:1 매핑).
- 응답: `PromotionResultPayload` + `mode_changed=false` / `live_flag_changed=false` invariant.
- DB write 0건. broker 호출 0건. LIVE flag 변경 0건.

## 9. UI (Frontend)

`PromotionGateCard` (Backtest 탭의 결과 카드 + Monte Carlo 카드 다음에 자동 노출):
- "AI 추천만으로 승격 불가 · 사람 승인 + 코드 기준 모두 필요" 배지 항상.
- 현재/목표 단계 select + `human_approved` 체크박스.
- 평가 후 `decision` 배지 (PASS/CAUTION/FAIL/BLOCKED) + failed_criteria / cautions / warnings / required_actions / passed_criteria 표시.
- **LIVE 활성화 / 모드 변경 / AI Execution 활성화 버튼 없음** (영구 금지).

## 10. 실패 사유 예시 (failed_criteria)

| 사유 (한국어) | 의미 |
|---|---|
| `거래 수 부족: 50 < 100회` | 표본 부족 |
| `기대값(expectancy) 양수 아님: -10.00` | 평균적 손실 |
| `Profit Factor 1.0 < 기준 1.2` | 수익성 부족 |
| `MDD 2,000,000 > 한도 1,500,000 (운영 자본의 15%)` | 자본 보호 위반 |
| `수수료/세금 미반영 백테스트 — 승격 불가` | #23 비용 모델 미적용 |
| `Walk-forward 추천 FAIL` | #25 검증 실패 |
| `holdout PnL -1000 ≤ 0 — out-of-sample 실패` | 미래 검증 실패 |
| `파산위험 10.0% > 한도 5%` | #26 Monte Carlo 차단 |
| `데이터 품질 EXCLUDE — 백테스트 결과 신뢰 불가` | #21 차단 |
| `Paper 운영 10일 < 28일` | 운영 데이터 부족 |
| `audit 로그 누락 1회 > 0 — 승격 불가` | 감사 누락 |
| `LIVE 단계 승격에는 사람 승인 필수` | #27 invariant |

## 11. 안전 invariant (본 PR이 지키는 것)

- `app/governance/strategy_promotion.py`는 broker / RiskManager / PermissionGate / OrderExecutor / `route_order` 어떤 모듈도 import하지 않는다.
- `app/api/routes_governance.py`는 read-only — DB write 0건, broker 호출 0건.
- 결과 dict에 `mode_changed: false`, `live_flag_changed: false` invariant.
- frontend 카드에 BUY/SELL/Activate/모드 변경/AI Execution 활성화 버튼 0건.
- 외부 네트워크 호출 0건.
- `ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.
- 기존 `/api/backtest/run` `/compare` `/walk-forward` `/monte-carlo` 응답 변경 0건 — 신규 endpoint 추가만.

## 12. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| Cooldown rule (최근 FAIL 전략은 N일 재승격 금지) | 운영 데이터 누적 후 |
| Promotion result DB 영구화 (감사 추적) | 승격 의사결정이 늘어날 때 |
| Strategy Scoreboard에 promotion gate 결과 통합 | scoreboard 확장 PR |
| AI 추천 정확도 자동 산출 (agent_decision_log 분석) | AI 운영 데이터 누적 후 |
| 운영자가 임계값 (`MIN_PROFIT_FACTOR` 등) UI에서 조정 | 운영자 요청 시 |
| Paper/Shadow 일수 자동 산출 (audit_log 기반) | 운영자 자동화 요청 시 |
| frontend가 backtest run에서 자동으로 PromotionInput 생성 | UX 개선 PR |

## 관련 문서

- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 정책 (본 모듈의 정책 출처)
- [`backtest_policy.md`](backtest_policy.md) — 비용 모델 (#23)
- [`backtest_metrics.md`](backtest_metrics.md) — metric 정의 (#24)
- [`walk_forward_policy.md`](walk_forward_policy.md) — fold 평가 (#25)
- [`monte_carlo_policy.md`](monte_carlo_policy.md) — risk_of_ruin (#26)
- [`data_quality_report.md`](data_quality_report.md) — 데이터 품질 (#21)
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — RiskManager 가드 (직접 무관)
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 잔여 작업
