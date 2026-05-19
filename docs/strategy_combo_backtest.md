# 전략 조합 백테스트 (Strategy Combo Backtest)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 리포트는 *advisory* — 전략 조합 분석만, 실거래 주문 0건.

## 1. 목적

단독 전략 6개에 더해 **4 매매기법군의 모든 조합 (15개)** 을 backtest 해, AI
Agent + 운영자가 *조합 기반* 으로 paper / 검증을 검토할 수 있는 advisory 성과표
를 생성한다.

**핵심 정책**: 본 분석은 *advisory* — PASS 라벨도 실거래 / Paper 후보 자동
적용 허가가 아니며, `recommended_for_paper=False` 가 영구. Paper 후보 확정 +
실거래 진입은 별도 운영자 검토 + 옵트인 PR 후에만 가능.

## 2. 4 매매기법군

| Tactic Group | 한글 | 후보 전략 |
|---|---|---|
| `MOMENTUM` | 추세추종 / Momentum | `sma_crossover`, `volume_breakout` |
| `REVERSION` | 평균회귀 / Reversion | `rsi_reversion` |
| `VWAP` | VWAP / 장중 기준가 | `vwap_strategy` |
| `ORB_PULLBACK` | 장초반 돌파 / Pullback-Rebreak | `orb_vwap`, `pullback_rebreak` |

## 3. 15 조합 카탈로그

| 크기 | 개수 | 예시 |
|---:|---:|---|
| 1 (단일) | 4 | `MOMENTUM`, `REVERSION`, `VWAP`, `ORB_PULLBACK` |
| 2 (쌍) | 6 | `MOMENTUM+REVERSION`, `MOMENTUM+VWAP`, `MOMENTUM+ORB_PULLBACK`, `REVERSION+VWAP`, `REVERSION+ORB_PULLBACK`, `VWAP+ORB_PULLBACK` |
| 3 | 4 | `MOMENTUM+REVERSION+VWAP` 등 |
| 4 (전체) | 1 | `MOMENTUM+REVERSION+VWAP+ORB_PULLBACK` |

총 15 = C(4,1) + C(4,2) + C(4,3) + C(4,4).

## 4. Signal-level scoring

caller 가 `StrategySignal` list 를 입력하면 각 조합에 대해 다음을 산출:

| 지표 | 정의 |
|---|---|
| `signal_count` | 조합에 포함된 strategy 의 signal 수 |
| `trade_count` | direction ∈ {BUY, SELL, EXIT} 인 signal 수 |
| `overlap_count` | 같은 (day, symbol) 에 2개 이상 signal 이 동시 발생한 시점 수 |
| `conflict_count` | 같은 (day, symbol) 에 BUY 와 SELL 이 함께 등장한 시점 수 |
| `confirmation_score` | 같은 (day, symbol, direction) 에 *서로 다른 tactic group* signal 의 수 합 |
| `conflict_ratio` | `conflict_count / max(signal_count, 1)` (0~1) |

PnL 지표 (caller 가 `realized_pnl` 전달 시):
- `total_return`, `expectancy`, `profit_factor`, `max_drawdown`, `win_rate`,
  `loss_streak`, `risk_adjusted_score` (= `total / max_drawdown`),
  `fee_adjusted_return` (수수료 차감), `slippage_adjusted_return`.

## 5. Verdict 매트릭스

| Verdict | 조건 |
|---|---|
| `PASS` | trade_count ≥ min_trades AND expectancy > 0 AND profit_factor ≥ pass_PF AND \|max_drawdown\| ≤ pass_MDD AND conflict_ratio ≤ pass_conflict |
| `WARN` | 일부 PASS 임계 boundary 근접 (profit_factor / drawdown / conflict / overlap) |
| `FAIL` | expectancy ≤ 0 OR profit_factor < fail_PF OR \|max_drawdown\| > fail_MDD |
| `INSUFFICIENT_DATA` | signal_count == 0 OR trade_count < min_trades OR metric 계산 불가 |

기본 임계 (`ComboCriteria`):
- `min_trades=10`
- `pass_profit_factor=1.2`, `fail_profit_factor=1.0`
- `pass_max_drawdown_abs=0.20`, `fail_max_drawdown_abs=0.30`
- `pass_conflict_ratio=0.30`
- `fee_rate=0.001` (0.1% per side advisory)
- `slippage_rate=0.0005`

## 6. 결과 파일

`reports/strategy_combo/` 디렉토리에 3개 파일 생성:

| 파일 | 형식 | 용도 |
|---|---|---|
| `strategy_combo_summary.json` | JSON | 기계 판독용 — schema_version / 15 results / criteria / invariants |
| `strategy_combo_report.md` | Markdown | 운영자 한글 리포트 — 매매기법군 카탈로그 + 결과 매트릭스 + 안전 invariant |
| `strategy_combo_ranking.csv` | CSV | expectancy 내림차순 + `recommended_for_paper=false` 영구 column |

`.gitignore` 의 `reports/*` 규칙으로 git 커밋 0건 — 테스트는 `tmp_path` 사용.

## 7. AI Agent context 연결

각 `ComboResult.to_dict()` 응답에 다음 필드 carry — Agent 가 *참고용* 으로
읽을 수 있게 통일:

```jsonc
{
  "combo_name":              "MOMENTUM+VWAP",
  "included_tactics":        ["MOMENTUM", "VWAP"],
  "included_strategies":     ["sma_crossover", "volume_breakout", "vwap_strategy"],
  "combo_verdict":           "PASS",
  "reasons":                 ["모든 PASS 기준 통과 — advisory"],
  "risk_flags":              [],
  "agent_context_ready":     true,
  "recommended_for_paper":   false,    // 영구 — 자동 적용 X
  "is_order_signal":         false,
  "auto_apply_allowed":      false,
  "is_live_authorization":   false
}
```

**`recommended_for_paper=False` 영구** — PASS verdict 라도 자동으로 Paper
후보로 들어가지 *않는다*. Paper 후보 확정은 운영자 검토 + 별도 PR (예: 3-13
또는 `strategy_promotion_gate`) 이후에만.

## 8. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `ComboResult.is_order_signal=False` 외 2종 | `__post_init__` ValueError |
| `recommended_for_paper=False` (영구 — PASS 라벨에도) | `test_pass_result_still_does_not_recommend_paper` |
| 4 tactic groups 모두 카탈로그 등재 | `test_four_tactic_groups_exist` |
| 6 전략이 정확히 4 그룹에 매핑 | `test_six_strategies_assigned_to_tactics` |
| 총 조합 15개 (4+6+4+1) | `test_total_combinations_is_15` |
| 전체 combo 의 strategy 수 = 6 | `test_full_combo_strategy_count_is_6` |
| overlap / conflict / confirmation 계산 | `TestSignalMetrics` (5 cases) |
| PASS/WARN/FAIL/INSUFFICIENT_DATA verdict 매트릭스 | `TestVerdictMatrix` (6 cases) |
| JSON/MD/CSV 3 파일 생성 + invariant carry | `TestRenderAndWrite` (6 cases) |
| `reports/` git ignore | `test_reports_dir_is_gitignored` |
| `only_sizes` 필터 | `TestSizeFilter` (3 cases) |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| schema 에 secret 필드 0건 | `test_no_secret_fields_in_dataclass` |

## 9. CLI

```bash
python scripts/run_strategy_combo_backtest.py \
    --signals-file path/to/signals.json \
    --symbol 005930 \
    --output-dir reports/strategy_combo \
    --min-trades 10 \
    --pass-pf 1.2 --fail-pf 1.0 \
    --pass-mdd 0.20 --fail-mdd 0.30
```

signals JSON 형식: `StrategySignal` 의 list (strategy_id / symbol / day_key /
direction / score / realized_pnl).

## 10. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — 순수 분석 + 파일 출력
- ✅ `is_live_authorization=False` 영구 — PASS 라벨도 실거래 허가 아님
- ✅ `recommended_for_paper=False` 영구 — Paper 후보 자동 적용 0건
- ✅ 안전 flag default 변경 0건 (`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 그대로)
- ✅ secret 필드 0건 (`api_key` / `account_number` 등 검사)
- ✅ `reports/` 결과물 git tracked 0건

## 11. 후속 PR 권고

- **장세별 조합 검증** — `MarketRegimeAgent` (4-04) 결과와 결합해 regime 별
  조합 verdict 산출 (예: TREND_UP 에서 MOMENTUM-heavy 조합 가산, SIDEWAYS
  에서 REVERSION+VWAP 가산).
- **3-13 Paper 후보 확정 게이트** — 본 combo backtest 의 PASS 결과 중
  *몇 개* 를 운영자 명시 검토 후 paper 후보로 promote 하는 별도 옵트인 흐름.
- **walk-forward + combo** — 본 PR 은 in-sample backtest. walk-forward
  validation (#25) 위에서 combo 안정성 재확인.
- **Live engine 통합** — combo PASS 가 Live 흐름에 반영되려면 Paper Gate
  (#72) / Live Manual Gate (#73) / AI Execution Activation Gate (#75) 통과
  필요. 본 combo 분석은 *입력 데이터* 일 뿐 자동 진입 trigger 0건.
