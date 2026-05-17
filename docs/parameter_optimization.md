# Step 3-03 — 실제 데이터 기반 파라미터 최적화

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> `PAPER_CANDIDATE` 라벨은 paper 운용 *검토 가능* 표시 — 자동 실거래 활성화 X.
> `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
> default false 유지.

## 1. 목적

3-02 의 실제 데이터 백테스트 러너 위에 *제한된* parameter grid search 를
적용해 6 전략 × 10 종목 매트릭스에서 paper 운용 후보를 추출한다.

핵심: search space 폭주 차단 (overfit / 자원 / 인지 부하). 각 전략당 최대
6 조합, 총 29 조합으로 의도적 보수.

## 2. 6 전략 × parameter grid

`backend/app/backtest/real_data/grid_search.py::PARAMETER_GRIDS`:

| Strategy | 파라미터 (2개 영향력 위주) | 조합 수 |
|---|---|---|
| `sma_crossover` | `short` ∈ {5, 10}, `long` ∈ {20, 30, 40} | 4 |
| `rsi_reversion` | `period` ∈ {10, 14}, `oversold` ∈ {25, 30} | 4 |
| `vwap_strategy` | `stop_loss_pct` ∈ {1.0, 1.5, 2.0}, `take_profit_pct` ∈ {2.0, 2.5, 3.0, 4.0} | 6 |
| `orb_vwap` | `orb_bars` ∈ {3, 6, 9} | 3 |
| `volume_breakout` | `volume_multiplier` ∈ {1.5, 2.0, 2.5}, `stop_loss_pct` ∈ {1.5, 2.0, 2.5} | 6 |
| `pullback_rebreak` | `min_impulse_pct` ∈ {1.0, 1.5, 2.0}, `pullback_max_pct` ∈ {3.0, 4.0} | 6 |
| **합계** | | **29** |

10 종목 × 29 조합 = **최대 290 runs** (실 fixture 가 있는 symbol 만 실행).

## 3. 5단계 verdict

`backend/app/backtest/real_data/optimization_verdicts.py::OptimizationVerdict`:

| 우선순위 | Verdict | 조건 |
|---|---|---|
| 1 | `INSUFFICIENT_DATA`   | `trade_count < 10` |
| 2 | `NEGATIVE_EXPECTANCY` | `expectancy <= 0` |
| 3 | `HIGH_DRAWDOWN`       | `max_drawdown > 15%` |
| 4 | `LOW_QUALITY`         | `profit_factor < 1.10` |
| 5 | `PAPER_CANDIDATE`     | 위 4 가지 모두 통과 |

**`PAPER_CANDIDATE` 는 실거래 자격이 *아니다*.** paper 운용 *검토 가능* 라벨
— 운영자가 별도 검토 후 Paper Auto Loop 에 수동 입력. 자동 paper trader
시작 / 자동 실거래 활성화 / mode 변경 의미 0건.

## 4. 비용 모델

3-02 와 동일 (수수료·세금·슬리피지 default):

- `commission_bps = 15` (0.15% 양방향)
- `tax_bps        = 23` (0.23% SELL only — 한국 거래세)
- `slippage_bps   = 5`  (0.05% — 호가 갭 보수 추정)

비용 반영은 `BacktestEngine` 내부에서 적용 — CLI 인자로 override 가능.

## 5. CLI

```bash
# 1) repo CSV 만 (CI / 자동 테스트 안전).
python scripts/run_parameter_optimization.py

# 2) yfinance 옵트인 (실패 graceful).
python scripts/run_parameter_optimization.py --enable-yfinance

# 3) 특정 전략 / 종목만.
python scripts/run_parameter_optimization.py \
    --symbol 005930 --strategies sma_crossover rsi_reversion

# 4) dry-run.
python scripts/run_parameter_optimization.py --dry-run

# 5) 임계값 / 비용 override.
python scripts/run_parameter_optimization.py \
    --min-trade-count 10 --min-profit-factor 1.10 --max-drawdown-pct 0.15 \
    --commission-bps 15 --tax-bps 23 --slippage-bps 5
```

## 6. 산출물 (`reports/parameter_optimization/`, gitignore)

- `paper_candidate_config.json` — 상위 N (default 2) PAPER_CANDIDATE 후보.
  후보 0건도 파일 생성 + `reasons_no_candidate` carry.
- `parameter_optimization_summary.json` — 전체 grid run 결과.
- `parameter_optimization_ranking.csv`  — 전체 runs CSV (운영자 분석용).
- `parameter_optimization_report.md`    — 운영자 검토용 markdown 요약.

JSON 최상위 + 각 candidate 객체 invariant (테스트로 lock):

- `is_order_signal:        false`
- `auto_apply_allowed:     false`
- `is_live_authorization:  false`

## 7. 3-03 완료 기준

| 항목 | 본 PR 상태 |
|---|---|
| 6 전략 grid catalog 정의 | ✓ `PARAMETER_GRIDS` 6 키 (총 29 조합) |
| Grid 키가 strategy `__init__` 시그니처와 일치 | ✓ `validate_grid_keys()` 테스트로 lock |
| 5 verdict 분류기 (INSUFFICIENT_DATA / NEGATIVE_EXPECTANCY / HIGH_DRAWDOWN / LOW_QUALITY / PAPER_CANDIDATE) | ✓ `classify_optimization_run()` |
| 수수료·세금·슬리피지 반영 | ✓ `BacktestConfig` `commission_bps` / `tax_bps` / `slippage_bps` |
| 후보 0건도 파일 생성 + 사유 명시 | ✓ `build_paper_candidate_config` `reasons_no_candidate` |
| broker / OrderExecutor / route_order / KIS 주문 0건 | ✓ 정적 grep + 테스트 lock |
| 안전 flag default 변경 0건 | ✓ `.env.example` 미변경 |

## 8. 3-04 (Walk-forward) 진입 조건

본 3-03 PR 머지 후 별도 PR 에서:

1. `PAPER_CANDIDATE` 또는 경계선 후보를 walk-forward train/validation 으로
   재검증.
2. train 만 좋고 validation 부진하면 `OVERFIT_RISK` 라벨 → 후보 자격 박탈.
3. 본 PR 의 `paper_candidate_config.json` 은 walk-forward 통과 *후* 에만
   Paper Auto Loop 입력 권장.

## 9. CLAUDE.md 절대 원칙

- broker / OrderExecutor / route_order import 0건 (정적 grep + 테스트 lock).
- KIS 주문 API 호출 0건. yfinance 는 read-only 시세 fetch 만.
- 실제 매수 / 매도 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- 안전 flag default 변경 0건: `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false` /
  `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- `PAPER_CANDIDATE` 라벨은 *분석 라벨* — paper 운용 / 실거래 활성화 의미 X.
- paper_candidate_config.json 의 모든 객체 (최상위 + 각 candidate) 가
  `is_order_signal=false` / `auto_apply_allowed=false` / `is_live_authorization=false`
  invariant.
