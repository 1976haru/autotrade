# 전략 파라미터 최적화

> ⚠ **본 문서와 산출물은 *투자 조언이 아니라* 자동매매 시스템 운영·검증·개선
> 자료입니다.** 결과는 결정론적 합성 OHLCV (`MockMarketData`) 기반이며 *실
> 시장 성과 아님*. Paper 운용 진입은 별도 운영자 승인 + 실 데이터
> walk-forward + `paper_gate` (#72) 통과 후에만.

## 1. 목적

6개 단타 전략 (`sma_crossover` / `rsi_reversion` / `vwap_strategy` /
`orb_vwap` / `volume_breakout` / `pullback_rebreak`) 각각의 *핵심 파라미터*
를 작은 grid 로 탐색하고, 수수료·슬리피지 반영 후 *기대값 양수* 인 후보만
추려 1~2 전략을 *Paper 운용 후보* 로 추천한다.

본 절차의 산출물은 **advisory**. 어떤 파라미터도 자동으로 코드 / `.env` /
DB / 운영 정책에 반영되지 않는다. 추천 후보 채택은 별도 PR + 별도 검증
(`paper_gate` / `live_manual_gate` 등) 절차로만 가능.

## 2. 데이터 / 비용 가정

- 데이터: `MockMarketData` 결정론적 합성 OHLCV (재현 가능 — 같은 입력 → 같은
  결과). 실 데이터 재실행은 별도 PR.
- 체결 모델: `next_open` + `execution_delay_bars=1`.
- 비용: commission `15 bps` (0.15%) + tax `23 bps` (0.23% SELL) +
  slippage `5 bps` (0.05%). 본 값은 *보수적 기본* — 실 비용은 증권사 / 계좌에
  따라 다르며, 운영자가 CLI 인자로 override.

## 3. 파라미터 grid (small)

각 전략의 *대표 차원* 만 변동 (조합 폭발 방지). 모든 grid 의 default 부근에서
약간의 perturbation. 본 grid 는 1차 후보 식별용 — 운영자가 실 데이터 재실행
시 확장 권장.

| Strategy | 변동 차원 | 조합 수 |
|----------|-----------|---------|
| `sma_crossover` | `short`, `long` (short < long 강제) | 16 |
| `rsi_reversion` | `period`, `oversold`, `overbought` | 36 |
| `orb_vwap` | `orb_bars` | 5 |
| `vwap_strategy` | `rolling_vwap_window`, `max_deviation_pct_for_entry`, `take_profit_pct` | 36 |
| `volume_breakout` | `volume_lookback_bars`, `volume_multiplier`, `breakout_lookback_bars` | 36 |
| `pullback_rebreak` | `impulse_lookback_bars`, `pullback_lookback_bars`, `min_impulse_pct` | 48 |

총 177 조합. `--max-trials-per-strategy` 로 cap 가능 (default 200).

## 4. 점수화 (0~100)

| Sub-score | 가중치 | 정규화 |
|-----------|--------|--------|
| `expectancy_score` | 30 | `expectancy / avg_trade_notional` → 0~10% saturate |
| `profit_factor_score` | 25 | `(pf - 1.0) / 2.0` → 1.0~3.0 → 0~1 |
| `win_rate_score` | 15 | `win_rate` → 0~1 |
| `mdd_score` | 20 | `1 - MDD/initial_cash * (1/0.3)` → 30% MDD = 0점 |
| `trade_count_score` | 10 | `(trade_count - 10) / 40` → 10~50 거래 |

음수 expectancy → `expectancy_score = 0`. PF < 1 → `profit_factor_score = 0`.

## 5. 카테고리 분류

| Category | 조건 |
|----------|------|
| `INSUFFICIENT_DATA` | `trade_count < 10` (통계 신뢰 부족) |
| `NEGATIVE_EXPECTANCY` | 비용 반영 expectancy ≤ 0 |
| `LOW_QUALITY` | `PF < 1.10` 또는 `win_rate < 0.40` 또는 `MDD > 15% of initial_cash` |
| `PASS` | 위 모두 통과 — Paper 후보 candidate pool |

## 6. Paper 후보 선정

전체 PASS rows 중:
- 전략별 *최고 점수 1개* 추출
- `total_score ≥ 40.0` 임계 통과
- 최대 2개 (`PAPER_MAX_RECOMMEND=2`)

**선정된 후보는 자동 적용되지 않으며**, 운영자가 별도 PR 로 검토 → 실 데이터
재실행 → walk-forward → paper_gate 평가 → Paper 운용 진입 절차를 거친다.

## 7. Walk-forward 검증 준비

각 paper 후보에 대해 `WalkForwardConfig` 를 자동 생성 (`docs/strategy_optimization.md`
plan 섹션):

- mode: `rolling`
- train: 60d, validation: 20d, step: 20d
- holdout: 30d (학습 미사용)
- min_fold_count: 3, min_positive_fold_ratio: 0.6

기본은 *plan 만 carry*. `--run-walk-forward` 플래그를 명시하면 실제 fold N
회를 추가 실행해 over-fit 여부 평가에 사용.

## 8. 실행 방법

```bash
# 기본 실행 (reports/strategy_optimization/ 에 산출)
python scripts/run_strategy_optimization.py

# 옵션 — 기간 / 비용 / 부분 실행 / walk-forward 실 실행
python scripts/run_strategy_optimization.py \
    --symbol 005930 \
    --start 2026-01-01 --end 2027-12-31 \
    --commission-bps 15 --tax-bps 23 --slippage-bps 5 \
    --output-dir reports/strategy_optimization \
    --run-walk-forward

python scripts/run_strategy_optimization.py \
    --strategies sma_crossover rsi_reversion --dry-run

# 테스트
python -m pytest backend/tests/test_strategy_optimization.py -q
python scripts/security_scan.py
```

산출 파일 3종 (`reports/strategy_optimization/` — `.gitignore` 로 커밋 차단):
- `optimization_summary.json` — 전체 grid + 카테고리 + paper_candidates + walk-forward plan
- `optimization_ranking.csv` — total_score 내림차순
- `paper_candidates.md` — 운영자 검토용 markdown

## 9. 본 PR 시점 결과 (MockMarketData 한계 명시)

`MockMarketData` 의 합성 봉은 *매끄러운* OHLCV 라 4 전략(`orb_vwap` /
`volume_breakout` / `pullback_rebreak` / `vwap_strategy`)의 진입 조건이 거의
트리거되지 않는다. 결과적으로 본 PR 시점 grid 의 대부분이 `INSUFFICIENT_DATA`
로 분류되며, 추천 후보가 0개로 보고될 수 있다.

이는 *코드 결함이 아니라* 합성 데이터 한계. 다음 단계는:
1. 실 데이터 (`yfinance` / KIS market_data adapter) 로 같은 grid 재실행
2. 후보가 잡히면 walk-forward 실 실행으로 over-fit 검증
3. paper_gate 평가 → 실 KIS 모의 자금 Paper 운용

## 10. 절대 원칙 (CLAUDE.md 매핑)

| 원칙 | 강제 방식 |
|------|-----------|
| 실거래 활성화 금지 | `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건 (env mutate grep 가드) |
| `KIS_IS_PAPER=true` 유지 | env mutate 0건 |
| broker / OrderExecutor / route_order 호출 0건 | AST 호출 검사 + import grep |
| 외부 API (KIS LIVE / Anthropic / OpenAI / Telegram) 호출 0건 | import grep |
| Secret 노출 0건 | 산출 파일 secret-shape 패턴 검사 |
| `.env` 작성·수정 0건 | grep 검사 |
| 파라미터 자동 적용 0건 | `STRATEGY_REGISTRY[...] =`, `.save_params(`, `.apply_params(`, `strategy.enabled =` 호출 0건 (grep 가드) |
| advisory only | summary JSON `disclaimer` + markdown disclaimer 텍스트로 명시 |

## 11. 다음 단계 (참고)

- 다. **실 데이터 백테스트**: yfinance 또는 KIS adapter 로 실 OHLCV 가져와 같은 grid 재실행
- 라. **Walk-forward 실 실행**: paper 후보가 잡히면 `--run-walk-forward` 플래그로 fold 결과 / holdout 결과 검토 → over-fit 판단
- 마. **paper_gate (#72) 평가**: 4주 paper 운용 결과를 promotion_policy 기준으로 평가
- 바. **Live Manual Approval (#73)**: paper_gate PASS 후 운영자 명시 opt-in
