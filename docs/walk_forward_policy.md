# Walk-Forward Policy (체크리스트 #25)

## 1. 목적

특정 구간의 한 번 대박이나 과거 데이터에 과적합된 파라미터로 전략이 승격되는 사고를 방지한다. 학습기간 / 검증기간 / 최근 holdout 기간으로 데이터를 분할하고, 각 fold의 *out-of-sample* 결과만 집계해 **한 번의 운 좋은 구간이 전체를 끌어올리지 못하게** 한다.

본 PR은 검증 프로토콜과 리포트 + read-only API + 정책 문서다. broker / RiskManager / PermissionGate / OrderExecutor / `BacktestEngine` 변경 0건 (CLAUDE.md 절대 원칙).

## 2. Rolling vs Anchored

| 모드 | train_start | 의미 |
|---|---|---|
| `rolling` (기본) | 매 fold마다 `step_days`만큼 앞으로 이동 | 최근 데이터에 적응. 시장 체제 변동에 더 민감 |
| `anchored` | 첫 fold의 시작에 고정 | train_end만 늘어남. 누적 학습 — 표본이 점점 커짐 |

권장:
- 단타 전략 — `rolling` (시장 체제 변동 추적).
- 장기 추세 전략 — `anchored` (표본 크기 ↑).

## 3. Train / Validation / Holdout 정의

```
[==== train ====][== val ==] [==== train ====][== val ==] ... [== holdout ==]
       fold 0                       fold 1                   마지막 N일
```

- **train_days**: 한 fold의 학습 기간 (예: 60일).
- **validation_days**: 학습 직후 *out-of-sample* 검증 (예: 20일). 본 모듈은 validation 결과만 집계.
- **step_days**: 다음 fold로 이동하는 슬라이딩 폭. 0이면 `validation_days`로 자동 (인접 fold 비중첩).
- **holdout_days**: 데이터 끝의 N일을 분리. fold 어디에도 사용되지 않는 *최종 out-of-sample* 검증.
- **min_fold_count** (기본 3): 본 수치 미만이면 `FAIL` (검증 표본 부족).

## 4. 한 번의 대박 방지 기준

| 지표 | 정의 | 한도 (기본) |
|---|---|---|
| `positive_fold_ratio` | 양수 PnL fold 수 / 전체 fold | ≥ 0.6 (60% 이상) |
| `single_best_fold_pnl_share` | 최대 fold PnL / 양수 fold PnL 합 | ≤ 0.7 (한 fold가 70% 초과 차지 금지) |
| `holdout PnL` | holdout 구간 총 PnL | ≥ 0 (손실이면 FAIL) |
| `overfit_risk_score` | (avg_train − avg_valid) / avg_train × 100 | < 50 |

`single_best_fold_pnl_share`가 0.95라면 한 fold가 거의 모든 수익을 만들었다는 신호 — 우연 의존 가능성이 매우 크다.

## 5. 승격 의견 (자동 산출)

`run_walk_forward` 결과는 자동으로 `FAIL / CAUTION / PASS` 추천을 산출한다 — 단, **최종 승인은 운영자**.

| 추천 | 조건 |
|---|---|
| `FAIL` | fold 수 < min_fold_count, 또는 holdout PnL 손실, 또는 양수 fold 비율 미달 |
| `CAUTION` | single_best_fold_pnl_share 초과, 또는 overfit_risk_score ≥ 50 |
| `PASS` | 위 모든 기준 충족 |

`warnings` 배열 — 운영자에게 보일 친화적 한국어 메시지. `overfit_flags` 배열 — 학습/검증 격차 경고.

## 6. 비용/슬리피지 미반영 결과는 승격 금지

체크리스트 #23의 `BacktestConfig`를 walk-forward에도 그대로 적용한다 (`POST /api/backtest/walk-forward`의 `config` 필드).

| 단계 | 권장 BacktestConfig |
|---|---|
| 본 walk-forward 실행 | `next_open` + `slippage_bps≥5` + `commission_bps≥5` + `tax_bps=23` 명시 필수 |
| `same_close` 결과 | 검증 / 디버깅용. **승격 평가에 사용 금지** ([`backtest_policy.md`](backtest_policy.md)) |

## 7. 승격 기준 (promotion_policy.md와 lockstep)

- **전략 승격 전 walk-forward PASS 필요** — `FAIL` 또는 `CAUTION`은 운영자 별도 검토.
- **특정 구간 한 번의 대박으로 승격 금지** — `single_best_fold_pnl_share ≤ 0.7`.
- **train ≫ validation은 overfit 의심** — `overfit_risk_score < 50`.
- **holdout 구간 PnL 양수 필수** — out-of-sample 최종 검증.
- 단타 전략: `min_fold_count ≥ 5` 권장 (충분한 표본).
- 장기 전략: `holdout_days ≥ 30` 권장.

## 8. API

`POST /api/backtest/walk-forward`

```json
{
  "strategy": "sma_crossover",
  "params": {"short": 5, "long": 20},
  "initial_cash": 10000000, "quantity": 1,
  "bars": [...] /* 또는 (symbol, start, end) */,
  "config": {  /* #23 BacktestConfig — 비용 모델 */
    "execution_model": "next_open", "slippage_bps": 5,
    "commission_bps": 5, "tax_bps": 23
  },
  "walk_forward": {
    "mode": "rolling",
    "train_days": 60, "validation_days": 20, "step_days": 20,
    "holdout_days": 30,
    "min_fold_count": 3,
    "min_positive_fold_ratio": 0.6,
    "max_single_fold_pnl_share": 0.7,
    "min_holdout_pnl": 0
  }
}
```

응답:

```json
{
  "config": { ... },
  "folds": [
    {
      "window": {"fold_index": 0, "train_start": "...", "train_end": "...",
                 "valid_start": "...", "valid_end": "..."},
      "train_metrics":      { /* summarize_metrics(train trades) */ },
      "validation_metrics": { /* summarize_metrics(validation trades) */ },
      "validation_bar_count": 20
    }, ...
  ],
  "holdout_metrics":  { /* summarize_metrics(holdout trades) */ },
  "holdout_window":   {"start": "...", "end": "...", "bar_count": 30},
  "summary": {
    "fold_count": 5,
    "positive_fold_ratio": 0.8,
    "single_best_fold_pnl_share": 0.45,
    "stability_score": 80.0,
    "overfit_risk_score": 15.0
  },
  "promotion_recommendation": "PASS",
  "warnings":      [],
  "overfit_flags": [],
  "bars_processed": 200
}
```

## 9. 안전 invariant (본 PR이 지키는 것)

- broker / RiskManager / PermissionGate / OrderExecutor / `route_order` / `BacktestEngine` 변경 0건.
- `app/backtest/walk_forward_runner.py`는 broker / risk / permission / execution 어떤 모듈도 import하지 않는다.
- 외부 네트워크 호출 0건 — `BacktestEngine` + `summarize_metrics`만 사용.
- 기존 `/api/backtest/run` `/compare` 응답 변경 0건 — 신규 endpoint 추가만.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.
- NaN / inf는 None으로 sanitize — JSON 응답 항상 안전.
- fold 사이 strategy 상태 격리 — `strategy_factory()`가 fold마다 새 인스턴스 생성.

## 10. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| Walk-forward 결과 영구화 (DB 테이블) | 운영자가 시계열 분석 요구 시 |
| Frontend 결과 UI (fold별 PnL bar / 추천 배지) | UI 요청 시 |
| param sweep + walk-forward 결합 (best params 자동 탐색) | 별도 옵트인 PR |
| Strategy Scoreboard에 walk-forward 추천 통합 | scoreboard 확장 PR |
| Quality `EXCLUDE` 일자 자동 fold 제외 (#21 연동) | 별도 옵트인 PR |
| 시간대별 손익(`hourly_pnl`)을 fold별로 분리 표시 | UI 요청 시 |
| Calmar / Sortino 기반 fold 비교 | metrics 확장 후 |

## 관련 문서

- [`backtest_policy.md`](backtest_policy.md) — 체결 모델 + 비용 모델 (#23)
- [`backtest_metrics.md`](backtest_metrics.md) — fold metrics (#24, summarize_metrics 재사용)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + walk-forward 요건
- [`data_quality_report.md`](data_quality_report.md) — 데이터 품질 (POOR/EXCLUDE 제외)
