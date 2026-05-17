# Step 3 — 실제 데이터 기반 백테스트 파이프라인

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> 기본 모드는 `SIMULATION` / `PAPER` 이고, `ENABLE_LIVE_TRADING` /
> `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 는 default false 입니다.

## 1. 목적

`MockMarketData` 의 결정론적 합성 OHLCV 만으로는 *실 시장 성과* 를 판단할 수
없다. Step 3 는 *실제 / 준실제* OHLCV 데이터로 6개 등록 전략의 수익성 / 위험
지표를 재검증하고, 통과한 (strategy, symbol) 조합을 *paper 운용 후보* 로
export 하는 파이프라인을 정의한다.

> **Mock 의 위치**: 시스템 동작 확인용. 본 파이프라인 산출물 (paper 후보) 의
> 근거 데이터로 사용 금지.

## 2. 항목별 완료 기준 (3-01 ~ 3-07)

| # | 항목 | 본 PR 시점 상태 | 후속 작업 |
|---|---|---|---|
| 3-01 | 전략 후보 6개 유지 | `STRATEGY_REGISTRY` 6개 (`sma_crossover` / `rsi_reversion` / `vwap_strategy` / `orb_vwap` / `volume_breakout` / `pullback_rebreak`) — 본 PR `test_six_strategies_all_registered` 로 lock | — |
| 3-02 | 실제 데이터 백테스트 스크립트 | `scripts/run_real_data_backtest.py` 1회 명령 실행 가능 | 실제 KOSPI / KOSDAQ 추가 종목 CSV 데이터 수집 (별도 PR) |
| 3-03 | 실제 데이터 파라미터 탐색 준비 | `--strategies / --symbol / --commission-bps / --slippage-bps / --tax-bps` CLI 인자로 조절 가능 | grid search loop 추가 (별도 PR) |
| 3-04 | Walk-forward 검증 연결 준비 | `app.backtest.real_data.walk_forward_connector` — `WalkForwardSplit` / `WalkForwardVerdict` (`HEALTHY` / `OVERFIT_RISK` / `UNDERFIT` / `INSUFFICIENT`) + `assess_walk_forward_overfit()` helper | 기존 `walk_forward_runner.run_walk_forward()` 와 결선 (별도 PR) |
| 3-05 | Stress test 연결 준비 | `app.backtest.real_data.stress_test_connector` — 6 시나리오 catalog (`CRASH` / `SURGE` / `SIDEWAYS` / `SLIPPAGE` / `DATA_GAP` / `EXECUTION_REJECT`) + `StressVerdict` (`PASS` / `WARN` / `FAIL`) | 시나리오별 데이터 변형 / 비용 가중 적용 (별도 PR) |
| 3-06 | 성과지표 표준화 | `app.backtest.real_data.metrics::compute_extended_metrics` — 13 필수 키. `REQUIRED_METRIC_KEYS` 로 lock | — |
| 3-07 | `paper_candidate_config.json` 생성 | `app.backtest.real_data.paper_candidate` + CLI 출력 (`reports/backtest_real/paper_candidate_config.json`). 후보 0건도 `reasons_no_candidate` 명시 | — |

## 3. 13개 표준 지표

```text
- total_return                — raw 누적 수익률
- annualized_return           — 거래기간 환산 연환산
- win_rate                    — 승률 (win / total)
- trade_count                 — 거래 횟수
- profit_factor               — 총이익 / |총손실|
- expectancy                  — 평균 거래 PnL (KRW)
- max_drawdown                — 최대 낙폭 (0~1, 절대값)
- avg_trade_pnl               — 평균 거래 PnL
- avg_win                     — 평균 승리 PnL
- avg_loss                    — 평균 손실 PnL (절대값)
- loss_streak                 — 최대 연속 손실 거래 수
- risk_adjusted_score         — (expectancy / initial_cash) / max_drawdown
- fee_adjusted_return         — total_return - (fees + taxes) / initial_cash
- slippage_adjusted_return    — fee_adjusted_return - slippage / initial_cash
```

`REQUIRED_METRIC_KEYS` (tuple) 가 본 13개를 단일 진실로 정의 — 키 누락 시
`assert_required_keys_present()` 가 즉시 검출.

## 4. 5단계 verdict

`app.backtest.real_data.filters::BacktestVerdict`:

| Verdict | 의미 | 트리거 조건 |
|---|---|---|
| `INSUFFICIENT_DATA` | 거래 수 부족 | `trade_count < 10` |
| `NEGATIVE_EXPECTANCY` | 기대값 음수 | `expectancy <= 0` |
| `HIGH_DRAWDOWN` | 위험 한도 초과 | `max_drawdown > 15%` |
| `LOW_QUALITY` | 품질 미달 | `profit_factor < 1.10` 또는 비용 반영 음수 |
| `PAPER_CANDIDATE` | 모든 필터 통과 | 위 4개 모두 통과 |

우선순위 (가장 엄격한 거부 사유가 먼저): `INSUFFICIENT_DATA` > `NEGATIVE_EXPECTANCY` > `HIGH_DRAWDOWN` > `LOW_QUALITY` > `PAPER_CANDIDATE`.

## 5. 대표 종목 10종

`app.backtest.real_data.symbols::REPRESENTATIVE_SYMBOLS` (고정 카탈로그):

| code | 한글명 | 시장 | sector hint |
|---|---|---|---|
| 005930 | 삼성전자 | KOSPI | semiconductor |
| 000660 | SK하이닉스 | KOSPI | semiconductor |
| 035420 | NAVER | KOSPI | internet |
| 035720 | 카카오 | KOSPI | internet |
| 005380 | 현대차 | KOSPI | auto |
| 051910 | LG화학 | KOSPI | chemical |
| 068270 | 셀트리온 | KOSPI | biotech |
| 373220 | LG에너지솔루션 | KOSPI | battery |
| 105560 | KB금융 | KOSPI | finance |
| 055550 | 신한지주 | KOSPI | finance |

코드에서는 항상 6자리 ``symbol`` 로 처리 — `display_ko` 는 리포트용 라벨.

## 6. 데이터 소스 우선순위

1. **로컬 CSV** (`data/ohlcv/{symbol}.csv` 또는
   `backend/tests/fixtures/real_data/{symbol}.csv`).
2. **yfinance fallback** — `--enable-yfinance` 옵트인 시에만. 네트워크 / API
   rate-limit / 파싱 실패 모두 *graceful* (예외 raise 0건).
3. **데이터 없음** — `DISABLED` / `NO_DATA` status + 사유 carry. **mock 으로
   silent swap 0건** — 후보 사유로 명시되어 운영자가 즉시 인지.

## 7. paper_candidate_config.json 스키마

```jsonc
{
  "generated_at":           "2026-05-17T05:00:00+00:00",
  "is_order_signal":        false,
  "auto_apply_allowed":     false,
  "is_live_authorization":  false,
  "candidate_count":        0 | 1 | 2,
  "candidates": [
    {
      "strategy":          "sma_crossover",
      "symbol":            "005930",
      "params":            { ... },
      "score":             0.0123,
      "risk_metrics":      { ...13 keys... },
      "validation_status": "PAPER_CANDIDATE",
      "reasons":           ["all_filters_passed"],
      "extra":             { "data_source": "...", "bar_count": 84 },
      "is_order_signal":      false,
      "auto_apply_allowed":   false
    }
  ],
  "reasons_no_candidate": [
    "INSUFFICIENT_DATA: 6 run(s)",
    "no_strategy_symbol_passed_all_filters"
  ],
  "metadata": {
    "pipeline":     "step3-real-data-backtest",
    "config":       { ... },
    "data_summary": { ... },
    "strategies":   [...],
    "symbols":      [...],
    "stress_scenarios_prepared": [...]
  }
}
```

**절대 invariant** (테스트로 lock):

- 최상위 `is_order_signal=false` / `auto_apply_allowed=false` /
  `is_live_authorization=false`.
- 각 candidate 객체에도 `is_order_signal=false` / `auto_apply_allowed=false`.
- 후보 0건이어도 *파일은 생성* + `reasons_no_candidate` carry.
- BUY/SELL/HOLD/Place Order/실거래 시작/ENABLE_LIVE_TRADING 단어 0건.

## 8. CLI 사용 예

```bash
# 1) repo CSV 만으로 실행 (CI / 자동 테스트 안전).
python scripts/run_real_data_backtest.py

# 2) yfinance 옵트인 — 네트워크 실패해도 graceful.
python scripts/run_real_data_backtest.py --enable-yfinance

# 3) 특정 symbol / strategy 만.
python scripts/run_real_data_backtest.py --symbol 005930 \
    --strategies sma_crossover rsi_reversion

# 4) dry-run — stdout 요약만, 파일 작성 X.
python scripts/run_real_data_backtest.py --dry-run

# 5) 비용 / 자본 / 기간 override.
python scripts/run_real_data_backtest.py \
    --start 2025-01-01 --end 2026-05-01 \
    --initial-cash 10000000 --quantity 10 \
    --commission-bps 15 --tax-bps 23 --slippage-bps 5
```

## 9. 산출물

`reports/backtest_real/` (gitignore — 운영 로그):

- `paper_candidate_config.json` — paper 운용 후보 + 사유.
- `real_data_backtest_summary.json` — 전체 결과 (per_symbol × per_strategy).

## 10. CLAUDE.md 절대 원칙

- broker / OrderExecutor / route_order import 0건 (정적 grep + 테스트).
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` default 변경 0건.
- 실제 매수 / 매도 / Place Order 0건. 본 파이프라인은 *분석 read-only*.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- **paper 후보 export 가 자동 paper trader 시작 / 자동 실거래 활성화를 의미하지 않는다.**
  운영자가 `paper_candidate_config.json` 을 *직접 검토* 후 paper 운용 결정.

## 11. 다음 단계 (3-04 ~ 3-07 본격 실행)

- 3-04 walk-forward 실제 실행 — `walk_forward_runner.run_walk_forward()` 와
  `assess_walk_forward_overfit()` 결선. `OVERFIT_RISK` 라벨된 전략은 paper
  후보 자격 *박탈*.
- 3-05 stress test 실제 실행 — 6 시나리오별 데이터 변형 / 비용 가중 적용.
  `FAIL` 라벨된 전략은 paper 후보 자격 *박탈*.
- 3-06 metrics 표준 — 본 PR 에서 lock 완료. 향후 추가 지표는
  `REQUIRED_METRIC_KEYS` 와 별개 키로 확장 (backward compat 보장).
- 3-07 paper_candidate_config 실 사용 — 운영자가 검토 후 Paper Auto Loop
  (#2-01 ~ #2-08) 에 *수동* 입력. 자동 적용 금지.
