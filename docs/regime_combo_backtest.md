# 장세별 전략 조합 백테스트 (Regime Combo Backtest)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 리포트는 *advisory* — 장세 × 조합 분석만, 실거래 주문 0건. UNKNOWN 장세
> 에서는 어떤 조합도 추천하지 않습니다.

## 1. 목적

3-12 `strategy_combo_backtest` 의 15 조합을 **7 장세별로 분리**해 평가한다.
같은 조합이라도 TREND_UP / SIDEWAYS / HIGH_VOLATILITY 등 장세에 따라 성과가
크게 다르므로, AI Agent + 운영자가 *장세 + 조합* 매트릭스를 한눈에 볼 수 있도록.

총 출력: **7 regime × 15 combo = 105 row** 의 `RegimeComboResult`.

## 2. 7 장세 (MarketRegimeAgent #4-04 기준)

| Regime | 의미 |
|---|---|
| `TREND_UP` | 강한 상승 추세 — momentum / breakout 우선 |
| `TREND_DOWN` | 강한 하락 추세 — 신규 진입 축소, 손실 제한 우선 |
| `SIDEWAYS` | 횡보 — mean-reversion 우선, breakout 보류 |
| `HIGH_VOLATILITY` | 변동성 급증 — 신규 진입 축소, size 축소 권고 |
| `LOW_LIQUIDITY` | 거래대금 부족 — 슬리피지 위험 |
| `CHOPPY` | 무방향 + 잦은 반전 |
| `UNKNOWN` | 분류 불가 — Paper 자동 시작 금지, WATCH_ONLY |

## 3. 15 조합 (3-12 카탈로그 재사용)

`MOMENTUM` / `REVERSION` / `VWAP` / `ORB_PULLBACK` 4 매매기법군의
C(4,1)+C(4,2)+C(4,3)+C(4,4) = 15 조합.

## 4. Verdict 매트릭스

| Verdict | 조건 |
|---|---|
| `PASS` | trade_count ≥ min_trades AND expectancy > 0 AND profit_factor ≥ pass_PF AND \|MDD\| ≤ pass_MDD AND conflict_ratio ≤ pass_conflict + regime 차단 0건 |
| `WATCH` | 일부 PASS 임계 boundary 근접 OR HIGH_VOLATILITY / CHOPPY 라벨 |
| `FAIL` | expectancy ≤ 0 OR profit_factor < fail_PF OR \|MDD\| > fail_MDD |
| `INSUFFICIENT_DATA` | signal_count == 0 OR trade_count < min_trades |
| `BLOCKED_REGIME` | LOW_LIQUIDITY / UNKNOWN 또는 조합 내 strategy 가 regime 정책상 `blocked` |

### BLOCKED_REGIME 매트릭스

| Regime | 정책 |
|---|---|
| `LOW_LIQUIDITY` | **모든 조합 BLOCKED** — 거래대금 부족 정책 |
| `UNKNOWN` | **모든 조합 BLOCKED** + 추천 영구 차단 (`recommended_by_regime[UNKNOWN]==[]`) |
| `TREND_DOWN` | `volume_breakout` / `orb_vwap` 포함 시 BLOCKED |
| `SIDEWAYS` | `volume_breakout` / `orb_vwap` 포함 시 BLOCKED |
| `HIGH_VOLATILITY` | `orb_vwap` / `volume_breakout` 포함 시 BLOCKED |
| `CHOPPY` | `sma_crossover` / `pullback_rebreak` / `orb_vwap` / `volume_breakout` 포함 시 BLOCKED |
| `TREND_UP` | `blocked` 정책 0건 — metric 기반 PASS/WATCH/FAIL |

`MarketRegimeAgent.REGIME_STRATEGY_POLICY` (4-04) 와 1:1 일관성.

## 5. 결과 파일

`reports/regime_combo/` 디렉토리:

| 파일 | 형식 | 용도 |
|---|---|---|
| `regime_combo_summary.json` | JSON | 105 result + recommended_by_regime + blocked_by_regime + criteria |
| `regime_combo_report.md` | Markdown | 장세별 추천/차단 표 + 안전 invariant |
| `regime_combo_ranking.csv` | CSV | regime × combo 정렬 + `recommended_for_paper=false` 영구 |

`.gitignore` 의 `reports/*` 로 git 커밋 0건 — 테스트는 `tmp_path` 사용.

## 6. AI Agent context 연결

각 `RegimeComboResult.to_dict()` 에 carry:

```jsonc
{
  "regime":                "TREND_UP",
  "combo_name":            "MOMENTUM",
  "included_tactics":      ["MOMENTUM"],
  "included_strategies":   ["sma_crossover", "volume_breakout"],
  "verdict":               "PASS",
  "regime_combo_score":    2.4567,
  "reasons":               ["모든 PASS 기준 통과 — advisory"],
  "risk_flags":            [],
  "blocked_strategies":    [],
  "watchlist_strategies":  [],
  "agent_context_ready":   true,
  "recommended_for_paper": false,    // 영구 — 자동 적용 X
  "is_order_signal":       false,
  "auto_apply_allowed":    false,
  "is_live_authorization": false
}
```

**`recommended_for_paper=False` 영구** — PASS verdict 라도 자동 Paper 후보
승격 0건. 운영자 검토 + 별도 PR (3-14 또는 promotion gate) 이후에만.

## 7. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `RegimeComboResult.is_order_signal / auto_apply_allowed / is_live_authorization` 영구 False | `__post_init__` ValueError |
| `recommended_for_paper=False` 영구 (PASS 라벨에도) | `test_pass_still_does_not_recommend_paper` |
| `UNKNOWN` 장세 모든 조합 BLOCKED_REGIME | `test_unknown_blocks_every_combo` |
| `recommended_by_regime["UNKNOWN"] == []` 영구 invariant | `__post_init__` ValueError + `test_report_construction_rejects_unknown_recommendation` |
| `LOW_LIQUIDITY` 모든 조합 BLOCKED | `test_low_liquidity_blocks_every_combo` |
| TREND_UP 에서 MOMENTUM / ORB_PULLBACK PASS | `TestTrendUpFavored` |
| TREND_DOWN 에서 momentum 차단 / REVERSION 통과 | `TestTrendDownBlocks` |
| SIDEWAYS 에서 momentum 차단 / REVERSION+VWAP PASS | `TestSidewaysFavored` |
| HIGH_VOLATILITY 항상 WATCH + risk_flag | `TestHighVolatilityWarn` |
| CHOPPY 항상 WATCH + risk_flag | `TestChoppyWatch` |
| 7 × 15 = 105 row | `test_empty_signals_produces_105_rows` |
| `reports/` git ignore | `test_reports_dir_is_gitignored` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| schema 에 secret 필드 0건 | `test_no_secret_fields` |

## 8. CLI

```bash
python scripts/run_regime_combo_backtest.py \
    --signals-file path/to/signals_with_regime.json \
    --symbol 005930 \
    --output-dir reports/regime_combo \
    --min-trades 10 \
    --pass-pf 1.2 --fail-pf 1.0 \
    --pass-mdd 0.20 --fail-mdd 0.30
```

signals JSON 형식: `RegimeStrategySignal` 의 list — `StrategySignal` 의 모든
필드 + `regime` (MarketRegime enum value).

## 9. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — 순수 분석 + 파일 출력
- ✅ `is_live_authorization=False` 영구 — PASS 라벨도 실거래 허가 아님
- ✅ `recommended_for_paper=False` 영구 — Paper 후보 자동 적용 0건
- ✅ `UNKNOWN` 장세 → 추천 0건 (영구 lock)
- ✅ `LOW_LIQUIDITY` 장세 → 모든 조합 BLOCKED (영구 lock)
- ✅ 안전 flag default 변경 0건
- ✅ `MarketRegimeAgent.REGIME_STRATEGY_POLICY` 와 1:1 일관성

## 10. 후속 PR 권고

- **3-14 Paper 후보 확정 게이트** — 본 regime × combo PASS 결과 중 운영자
  명시 검토 후 paper 후보로 promote.
- **Walk-forward + regime** — 장세별 in-sample / out-of-sample 안정성 확인.
- **regime confidence weighting** — `MarketRegimeReport.regime_confidence`
  를 carry 해 advisory 점수에 가중치 적용 (낮은 confidence → WATCH 강제).
- **per-regime backtest 시계열** — 단일 시점 평가가 아닌 *기간별* regime
  변화 시계열 (rolling window) 분석.
