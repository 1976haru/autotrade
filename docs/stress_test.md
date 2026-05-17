# Step 3-05 -- Stress test

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> PASS / WARN / FAIL 라벨은 *분석 라벨* — paper 운용 / 실거래 활성화 / 자동
> promotion 변경 의미 X. 본 단계는 **3-04 Walk-forward 와 3-07 Paper 후보
> 생성 사이의 검증 단계** 다.

## 1. 목적

`MockMarketData` 가 아닌 실제 / 준실제 OHLCV 데이터에서 baseline backtest
(3-02) + parameter optimization (3-03) + walk-forward (3-04) 를 통과한
후보를, **10 가지 스트레스 상황** 하에서 한 번 더 검증한다. 어떤 시나리오
에서도 손실 한도 / 거절 / 데이터 누락에 취약하지 않은 전략만 paper 후보
검토 진입을 권장한다.

## 2. 10 시나리오

`backend/app/analytics/stress_test.py::StressScenario`:

| # | 시나리오 | 변형 종류 | 효과 |
|---|---|---|---|
| 1 | `CRASH`               | 데이터 변형 | 중간 지점부터 -8% 갭 + -0.1% 누적 하락 |
| 2 | `SURGE`               | 데이터 변형 | 중간 지점부터 +8% 갭 + +0.1% 누적 상승 |
| 3 | `SIDEWAYS`            | 데이터 변형 | close 를 전체 평균 ±0.5% 로 압축 (변동성 ↓) |
| 4 | `SLIPPAGE_SPIKE`      | 비용 가중   | `BacktestConfig.slippage_bps` 5 → 30 (6배) |
| 5 | `DATA_GAP`            | 데이터 변형 | 매 7번째 bar 제거 (~14% 누락) |
| 6 | `EXECUTION_REJECT`    | 행동 카운터 | 매 5번째 trade reject (rejected_order_count carry) |
| 7 | `STALE_PRICE`         | 행동 카운터 | 10% bars stale 로 카운트 (stale_data_violation_count) |
| 8 | `DUPLICATE_SIGNAL`    | 행동 카운터 | 연속 3봉 동방향 발생 횟수 카운트 (proxy) |
| 9 | `LOW_LIQUIDITY`       | 데이터 변형 | volume × 0.10 (가격 영향 X) |
| 10 | `CORRELATED_DRAWDOWN` | proxy      | 단일 symbol drawdown 을 informational proxy 로 carry |

모든 변형은 **결정론적** — 동일 입력 → 동일 결과 (CI 안전, 재현 가능).

## 3. 4단계 verdict

`StressVerdict`:

| Verdict | 조건 | 의미 |
|---|---|---|
| `PASS` | `expectancy > 0` AND `max_drawdown ≤ pass_max_drawdown` AND 중대한 위반 0건 | 스트레스 후에도 안정적 — 후보 검토 진입 권장 |
| `WARN` | 일부 임계 초과 (e.g. `warn_max_drawdown < MDD ≤ pass_max_drawdown`) OR `rejected_order_count > 0` OR `stale_data_violation_count > 0` | 수익성 약화 또는 위험 신호 — 검토 필요 |
| `FAIL` | `expectancy ≤ 0` OR `max_drawdown > pass_max_drawdown` | 중대한 손실 — 후보 박탈 권고 |
| `INSUFFICIENT_DATA` | `trade_count < min_trade_count` | 통계 의미 X — 데이터 / 윈도우 조정 필요 |

**우선순위**: `INSUFFICIENT_DATA` > `FAIL` > `WARN` > `PASS`.

`stress_score` 는 0-100 점수 (높을수록 좋음) — verdict 와 별개로 정렬 / 비교용.

## 4. 16 필수 metric (`StressResult`)

```text
scenario_name | strategy | symbol | total_return | expectancy |
profit_factor | max_drawdown | win_rate | trade_count | loss_streak |
rejected_order_count | stale_data_violation_count | duplicate_signal_count |
slippage_cost | stress_score | stress_verdict
```

JSON 결과 객체에 추가 invariant 3종 (테스트로 lock):

- `is_order_signal:        false`
- `auto_apply_allowed:     false`
- `is_live_authorization:  false`

## 5. CLI

```bash
# 1) 대표 10종 × 6 전략 × 10 시나리오 default 실행.
python scripts/run_stress_test.py

# 2) 3-04 walk_forward_summary.json (HEALTHY 만) 입력.
python scripts/run_stress_test.py \
    --from-walk-forward reports/walk_forward/walk_forward_summary.json

# 3) 단일 (strategy, symbol).
python scripts/run_stress_test.py \
    --strategy sma_crossover --symbol 005930

# 4) 특정 시나리오만.
python scripts/run_stress_test.py --scenarios CRASH SURGE SIDEWAYS

# 5) dry-run.
python scripts/run_stress_test.py --dry-run

# 6) yfinance 옵트인.
python scripts/run_stress_test.py --enable-yfinance

# 7) 임계값 / 비용 override.
python scripts/run_stress_test.py \
    --pass-max-dd 0.20 --warn-max-dd 0.15 --min-trade-count 5 \
    --commission-bps 15 --tax-bps 23 --slippage-bps 5
```

## 6. 산출물 (`reports/stress_test/`, gitignore)

- `stress_test_summary.json` — per_run 결과 (scenario × candidate) + 16 metric +
  invariant.
- `stress_test_ranking.csv`  — verdict 정렬 (PASS 위, FAIL 아래) + score.
- `stress_test_report.md`    — 운영자 검토용 markdown.

**테스트 / CI**: 본 산출물은 `reports/*` gitignore 로 차단. 테스트는 `tmp_path`
에 생성 후 검증만 한다 (`test_writes_three_artifacts` lock).

## 7. 3-05 위치 (3-04 ↔ 3-07 사이)

```text
3-02 baseline backtest
   ↓
3-03 parameter optimization → paper_candidate_config.json (3-03 결과)
   ↓
3-04 walk-forward             → walk_forward_summary.json (HEALTHY/OVERFIT_RISK/...)
   ↓
3-05 stress test (본 단계)    → stress_test_summary.json (PASS/WARN/FAIL/...)
   ↓
3-06 성과 지표 통합 (별도 PR)
   ↓
3-07 paper 후보 최종 export   → 운영자 검토 → Paper Auto Loop 수동 입력
```

권장 흐름:
- **3-04 HEALTHY** 후보를 본 3-05 입력으로 `--from-walk-forward` adapter 로 carry.
- 모든 10 시나리오에서 `PASS` 인 후보 → 3-07 paper 후보 검토 권장.
- 한 시나리오라도 `FAIL` → 후보 박탈 / 별도 PR 로 grid 재정의.
- `WARN` 다수 → 운영자 판단 (보수적으로 박탈 권장).

## 8. 3-05 완료 기준

| 항목 | 본 PR 상태 |
|---|---|
| 10 시나리오 정의 (`StressScenario`) | ✓ |
| 4단계 verdict (`StressVerdict`) | ✓ |
| 16 필수 metric (`StressResult`) | ✓ |
| 데이터 변형 결정론적 (동일 input → 동일 output) | ✓ `test_crash_deterministic` |
| 비용 가중 (SLIPPAGE_SPIKE) BacktestConfig 갱신 | ✓ `_apply_scenario_to_btconfig` |
| 행동 카운터 (REJECT / STALE / DUPLICATE) carry | ✓ `StressResult.rejected_order_count` 등 |
| 3-04 walk_forward_summary.json adapter | ✓ `read_candidates_from_walk_forward` (HEALTHY 만) |
| CLI + 산출물 (JSON / CSV / markdown) | ✓ `scripts/run_stress_test.py` |
| broker / OrderExecutor / route_order / KIS 주문 0건 | ✓ 정적 grep + 테스트 lock |
| 안전 flag default 변경 0건 | ✓ `.env.example` 미변경 |
| 모든 결과 객체 invariant (`is_order_signal=false` 등) | ✓ |

## 9. 3-06 (성과 지표 통합) 진입 조건

본 3-05 PR 머지 후 별도 PR 에서:

1. 3-02 ~ 3-05 의 모든 산출물 (`*_summary.json`) 을 *통합 메타 리포트* 로
   집계.
2. 전략 별 통과 단계 추적 (3-02 PASS → 3-03 PAPER_CANDIDATE → 3-04 HEALTHY
   → 3-05 PASS 모든 시나리오).
3. 운영자 검토용 *최종 paper 후보 권장 리스트* — 자동 적용 X, 자동 promotion X.

## 10. CLAUDE.md 절대 원칙

- broker / OrderExecutor / route_order import 0건 (정적 grep + 테스트 lock).
- KIS 주문 API 호출 0건. yfinance 는 read-only 시세 fetch 만.
- 실제 매수 / 매도 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- 안전 flag default 변경 0건: `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false` /
  `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- `PASS` / `WARN` / `FAIL` 라벨은 *분석 라벨* — paper 운용 / 실거래 활성화 /
  자동 promotion 변경 의미 X.
- `reports/*` gitignore 유지 — 산출물 git 미커밋.
- 테스트는 `tmp_path` 에서 산출 확인만.
