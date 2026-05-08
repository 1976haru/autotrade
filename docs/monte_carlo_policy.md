# Monte Carlo Policy (체크리스트 #26)

## 1. 목적

백테스트 거래 로그를 입력으로 거래 순서를 섞거나 복원추출하여 N회 시뮬레이션하고, **운 좋게 나온 백테스트 결과를 필터링**한다. 같은 거래 표본이라도 발생 순서에 따라 MDD / 파산위험이 크게 달라질 수 있다 — 단 한 번의 운 좋은 시계열로 전략을 승격하지 않게 한다.

본 PR은 **read-only 분석/리포트 기능**만 추가한다. broker / RiskManager / PermissionGate / OrderExecutor / `BacktestEngine` 변경 0건 (CLAUDE.md 절대 원칙). MVP 흐름에 영향을 주지 않는 P2 고도화 항목.

## 2. 방법 (`method`)

| 방법 | 의미 | 보존되는 성질 |
|---|---|---|
| `shuffle` | 순서만 섞음 (복원추출 X) | 거래 집합·총 PnL 동일. 순서·MDD만 변동 |
| `bootstrap` | 복원추출 (각 거래 독립적으로 N번 다시 뽑음) | 표본 크기 동일, 거래 분포 변동 |
| `block_bootstrap` | 연속 `block_size` 거래를 한 단위로 추출 | 자기상관(연속손실 패턴 등) 부분 보존 |

권장:
- 단순 운 좋음 검증 → `shuffle`.
- 거래 표본의 다양성 검증 → `bootstrap`.
- 연속손실 / 군집 효과 검증 → `block_bootstrap`.

## 3. 주요 지표

| 지표 | 의미 | 해석 |
|---|---|---|
| `risk_of_ruin` | path 중 파산 임계 도달 비율 | `≥ 10%` → FAIL · `≥ 5%` → CAUTION |
| `p05_total_pnl` | 시뮬 PnL 분포의 5번째 백분위 | 최악 5% 시나리오의 손익 |
| `p50_total_pnl` | 중앙값 PnL | 일반적인 시나리오 |
| `p95_total_pnl` | 95번째 백분위 PnL | 운 좋은 시나리오 |
| `p05_max_drawdown` | MDD 분포의 5번째 백분위 | 가장 작은 MDD |
| `p95_max_drawdown` | MDD 분포의 95번째 백분위 | 큰 MDD 시나리오 |
| `worst_5pct_avg_mdd` | MDD 상위 5%의 평균 | 최악 5% 시나리오의 MDD 평균 |
| `longest_losing_streak` | 모든 시뮬 중 최장 연속손실 | 단타 운영 자본/심리 영향 |
| `median_final_equity` | 시뮬 종료 시 자산 중앙값 | 직관적 자산 변동 |

파산 임계 (`ruin_drawdown_pct`, 기본 `-0.5`): `equity ≤ initial_cash × (1 + ruin_drawdown_pct)` 가 한 번이라도 도달하면 ruin 카운트.

## 4. 자동 분류

`promotion_risk_flag` (FAIL / CAUTION / PASS):

| 조건 | 분류 |
|---|---|
| `risk_of_ruin ≥ 10%` | FAIL |
| `risk_of_ruin ≥ 5%` | CAUTION |
| 그 외 | PASS |

`stability_grade` (GOOD / WARNING / POOR):

| 조건 | 분류 |
|---|---|
| `risk_of_ruin ≥ 10%` 또는 `p05_total_pnl < -initial_cash × 0.2` | POOR |
| `p95_max_drawdown > initial_cash × 0.3` 또는 `risk_of_ruin ≥ 5%` | WARNING |
| 그 외 | GOOD |

`warnings` 배열 — 운영자에게 보일 친화적 한국어 메시지.

## 5. 승격 정책

**Monte Carlo PASS만으로 승격 금지.** Monte Carlo는 단일 거래 표본 위에서의 *순서/추출* 검증이며, 시장 체제 변동을 반영하지 않는다.

### 통합 승격 평가

| 검증 단계 | 자세한 정책 |
|---|---|
| Backtest 비용 반영 결과 | [`backtest_policy.md`](backtest_policy.md) |
| Backtest metrics (#24) | [`backtest_metrics.md`](backtest_metrics.md) |
| Walk-forward (#25) PASS | [`walk_forward_policy.md`](walk_forward_policy.md) |
| Data Quality (#21) GOOD/WARNING | [`data_quality_report.md`](data_quality_report.md) |
| **Monte Carlo (#26) PASS** | 본 문서 |
| Paper / Shadow 운영 4주 이상 | [`paper_mode.md`](paper_mode.md), [`shadow_mode.md`](shadow_mode.md) |

이 모두를 통과해야 LIVE_MANUAL_APPROVAL 승격 검토 진입.

### 추가 금지

- `risk_of_ruin > 5%` 인 전략은 LIVE 승격 보류 (CAUTION 단계 자동 도달).
- `p05_total_pnl`이 운영 자본의 -20%를 초과 — 사이즈 축소 또는 전략 재검토.
- `worst_5pct_avg_mdd`가 운영 자본의 30% 초과 — 자본 여유 확인 또는 사이즈 축소.

## 6. 한계

- **미래 시장 구조 변화 반영 불가** — 거래 표본이 바뀌면 결과도 바뀐다. 새 시장 체제에서는 새 백테스트 + Monte Carlo 필요.
- **거래 손익 독립성 가정 문제** — 실제로 거래는 자기상관이 있다 (이긴 후 이긴다 / 진 후 진다). `block_bootstrap`이 부분 완화하지만 완전한 해결은 아님.
- **슬리피지/호가 공백 단순화** — Monte Carlo는 PnL 시퀀스만 다루고, 봉별 슬리피지/체결 지연은 백테스트 단계에서 이미 반영된 것으로 가정 (`backtest_config`).
- **Bootstrap의 표본 크기 가정** — 표본이 작으면 (< 30 거래) bootstrap 분포의 의미가 약함.
- **실제 운용 전 Shadow / Paper 필수** — Monte Carlo는 사후 검증, 실시간 시장에서의 슬리피지 / 정지 / 호가 공백은 별도 검증.

## 7. API

`POST /api/backtest/monte-carlo` — read-only.

```json
{
  "trades": [{"pnl": 100}, {"pnl": -50}, ...],   // 또는 backtest_run_id
  "backtest_run_id": null,
  "config": {
    "method": "shuffle",            // shuffle | bootstrap | block_bootstrap
    "iterations": 1000,             // 1 ~ 100000
    "seed": 42,                     // 결정성 (선택)
    "initial_cash": 10000000,
    "ruin_drawdown_pct": -0.5,
    "block_size": 5
  }
}
```

응답 — `MonteCarloResponse` (위 [3절] 지표). JSON 직렬화 안전 (NaN/inf → null).

`trades`와 `backtest_run_id`는 **둘 중 정확히 하나만** — 둘 다 / 둘 다 안 보내면 400. `backtest_run_id`는 `BacktestRun.trades_json`에서 pnl만 추출.

## 8. UI

Backtest 탭의 결과 카드 아래에 `MonteCarloCard` 추가:
- 상단 "주문 신호 아님 · 전략 리스크 검증" 배지 (항상).
- method (shuffle / bootstrap / block_bootstrap) + 시뮬 횟수 (500/1000/5000) 선택.
- 실행 후 결과 tile (파산위험 / 최악 5% MDD / p05 PnL / p95 MDD + 자산 중앙값 / 최장 연속손실 / 시뮬 횟수 + promotion_risk_flag 배지).
- warnings 배열 표시.
- BUY/SELL 버튼 0건. 주문 결정과 분리.

## 9. 안전 invariant (본 PR이 지키는 것)

- broker / RiskManager / PermissionGate / OrderExecutor / `route_order` / `BacktestEngine` 변경 0건.
- `app/backtest/monte_carlo.py`는 `app.brokers` / `app.risk` / `app.permission` / `app.execution` import 0건.
- 외부 네트워크 호출 0건 — `random.Random(seed)` + 거래 PnL 리스트만 사용.
- 결정성 — `seed` 고정 시 같은 결과 (CI 안정).
- 기존 `/api/backtest/run` `/compare` `/walk-forward` 응답 변경 0건 — 신규 endpoint 추가만.
- `ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.
- 응답에 `side / order_type / decision / quantity / BUY / SELL / HOLD` 필드 0.
- NaN / inf는 None / null로 sanitize — JSON 응답 항상 안전.

## 10. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| Regime별 Monte Carlo (시장 체제 분리 후 재추출) | regime classifier 안정화 후 |
| Strategy 간 correlation 기반 Monte Carlo | 멀티 전략 운영 단계 |
| Portfolio-level Monte Carlo (여러 전략 합산) | 멀티 전략 운영 단계 |
| Intraday path simulation (분/시간 단위 변동) | tick 데이터 통합 후 |
| Orderbook-aware slippage simulation | 호가 데이터 통합 후 |
| Monte Carlo 결과 영구화 (DB 테이블) | 시계열 분석 요구 누적 시 |
| Equity sample paths chart (frontend) | UI 요청 시 |
| Strategy Scoreboard에 MC 통합 | scoreboard 확장 PR |
| `BacktestRun.trades_json`에서 자동으로 MC 트리거 | 운영자 워크플로 통합 시 |

## 관련 문서

- [`backtest_policy.md`](backtest_policy.md) — 비용 모델 (#23)
- [`backtest_metrics.md`](backtest_metrics.md) — 단일 백테스트 metric (#24)
- [`walk_forward_policy.md`](walk_forward_policy.md) — train/val/holdout (#25)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + Monte Carlo 요건
- [`data_quality_report.md`](data_quality_report.md) — 데이터 품질 (POOR/EXCLUDE 제외)
