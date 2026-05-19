# 전략 조합 중복 / 상관 / 쏠림 위험 검증 (Combo Correlation Risk)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 리포트는 *advisory* — Paper 후보 자동 적용 0건, BLOCK 라벨도 자동 제외
> 가 아니며 caller (promotion gate) 책임.

## 1. 목적

3-12 / 3-13 의 조합 백테스트가 *수익률 지표* 를 다루는 반면, 본 모듈은
**조합의 구조적 위험** 을 검증한다:

- 같은 종목에 여러 전략이 *동시에 진입* 하는 중복 신호
- 같은 방향 LONG / SHORT 신호가 *과도하게 몰리는* 현상
- 전략 간 상관관계가 *높은 조합* (proxy)
- 특정 *종목 / 전략군* 노출 집중
- 같은 손실을 반복할 위험 (BLOCK 사유)

AI Agent 가 조합을 추천할 때 본 결과를 *참고* 해 BLOCK / HIGH_RISK 라벨이
달린 조합을 *자동 제외 후보* 로 분류할 수 있다. 단 *적용은 운영자 승인 후*.

## 2. 측정 지표

### Signal-level

| 지표 | 정의 |
|---|---|
| `overlap_count` / `overlap_ratio` | 같은 (day, symbol) 에 2+ signal — 횟수 / signal_count 비율 |
| `same_direction_count` / `same_direction_ratio` | 같은 (day, symbol, direction) 에 2+ 서로 다른 tactic group — 횟수 / unique (day, symbol) 비율 |
| `conflict_count` / `conflict_ratio` | 같은 (day, symbol) 에 BUY + SELL — 횟수 / signal_count 비율 |
| `correlation_score` | proxy 0~1. (same_direction_ratio × 0.7 + overlap_ratio × 0.3) × diversity_penalty |
| `concentration_score` | max(`max_single_strategy_weight`, `max_single_symbol_weight`) — 0~1 |
| `max_single_strategy_weight` | 단일 strategy_id 차지 signal 비율 |
| `max_single_symbol_weight` | 단일 symbol 차지 signal 비율 |

### Notes

- 실제 *수익률 시계열* 이 없으므로 correlation_score 는 **proxy** —
  same-direction frequency + tactic diversity 로 추정. 후속 PR 에서 PnL
  Pearson 상관계수 계산이 가능해지면 본 함수를 교체.
- `diversity_penalty = 1 - min(0.5, (tactic_count - 1) × 0.15)` — tactic
  다양성이 높을수록 correlation score 감점.

## 3. Verdict 매트릭스

| Verdict | 조건 |
|---|---|
| `PASS` | overlap ≤ pass_overlap AND same_dir ≤ pass_same_dir AND conflict ≤ pass_conflict AND concentration ≤ pass_conc |
| `WATCH` | overlap / same_dir / concentration / conflict 가 pass_ 임계는 초과했지만 block 임계 미만 |
| `HIGH_RISK` | same_dir > watch_same_dir OR concentration > watch_conc |
| `BLOCK` | concentration ≥ block_conc OR same_dir ≥ block_same_dir OR conflict ≥ block_conflict |
| `INSUFFICIENT_DATA` | signal_count < min_signals |

### 기본 임계 (`RiskCriteria`)

| 임계 | 값 |
|---|---|
| `min_signals` | 5 |
| `pass_overlap_ratio` | 0.20 |
| `pass_same_dir_ratio` | 0.40 |
| `pass_conflict_ratio` | 0.10 |
| `pass_concentration` | 0.50 |
| `watch_overlap_ratio` | 0.40 |
| `watch_same_dir_ratio` | 0.60 |
| `watch_concentration` | 0.65 |
| `block_same_dir_ratio` | 0.85 |
| `block_conflict_ratio` | 0.40 |
| `block_concentration` | 0.85 |

## 4. 결과 파일

`reports/strategy_combo/` (3-12 와 같은 디렉토리):

| 파일 | 형식 | 용도 |
|---|---|---|
| `combo_correlation_risk_summary.json` | JSON | 15 result + criteria + invariants |
| `combo_correlation_risk_report.md` | Markdown | 운영자 친화 위험 매트릭스 + 안전 invariant |
| `combo_correlation_risk_ranking.csv` | CSV | risk_verdict 정렬 (BLOCK > HIGH_RISK > WATCH > INSUFFICIENT > PASS) |

`reports/` 는 `.gitignore` 의 `reports/*` 규칙으로 git 커밋 0건.

## 5. AI Agent context 연결

각 `ComboRiskResult.to_dict()`:

```jsonc
{
  "combo_name":                  "MOMENTUM+REVERSION",
  "included_tactics":            ["MOMENTUM", "REVERSION"],
  "included_strategies":         ["sma_crossover", "volume_breakout", "rsi_reversion"],
  "overlap_count":               6,
  "overlap_ratio":               0.30,
  "same_direction_count":        4,
  "same_direction_ratio":        0.40,
  "conflict_count":              0,
  "conflict_ratio":              0.0,
  "correlation_score":           0.32,
  "concentration_score":         0.55,
  "max_single_strategy_weight":  0.30,
  "max_single_symbol_weight":    0.55,
  "risk_verdict":                "WATCH",
  "risk_flags":                  ["boundary_concentration"],
  "exclusion_reasons":           ["concentration_score=0.55 > pass=0.50"],
  "recommendation":              "WATCH — 일부 boundary 초과, Paper 관찰 가능",
  "operator_note":               "운영자 관찰 + size 축소 검토 권고.",
  "agent_context_ready":         true,
  "recommended_for_paper":       false,    // 영구 — BLOCK / PASS 모두 자동 적용 X
  "is_order_signal":             false,
  "auto_apply_allowed":          false,
  "is_live_authorization":       false
}
```

**`recommended_for_paper=False` 영구** — BLOCK 라벨도 PASS 라벨도 자동
Paper 후보 적용 0건. Paper 후보 확정은 별도 promotion gate (3-15) 또는
운영자 명시 승인 후에만.

## 6. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `ComboRiskResult.is_order_signal / auto_apply_allowed / is_live_authorization` 영구 False | `__post_init__` ValueError |
| `recommended_for_paper=False` 영구 (BLOCK / PASS 모두) | `test_block_verdict_still_does_not_recommend_paper` |
| ratio 필드 0~1 범위 | `__post_init__` ValueError |
| BLOCK verdict trigger 3종 (concentration / same_direction / conflict) | `TestVerdictMatrix` (3 cases) |
| INSUFFICIENT_DATA on `signal_count < min_signals` | `test_insufficient_data_when_signals_below_min` |
| concentration=1.0 detect (단일 strategy / symbol) | `TestConcentration` (3 cases) |
| same_direction count 정확 (tactic 다양성 필요) | `TestSameDirection` (2 cases) |
| conflict count 정확 (BUY+SELL) | `TestConflict` (2 cases) |
| overlap count 정확 (같은 day+symbol 2+) | `TestOverlap` (3 cases) |
| correlation_score: same-direction 높음 → 높음 / 분산 → 낮음 | `TestCorrelationScore` (2 cases) |
| 15 combo 카탈로그 + only_sizes filter | `TestReport` |
| Report files JSON/MD/CSV + `.gitignore` 가드 | `TestRenderAndWrite` (5 cases) |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |
| schema 에 secret 필드 0건 | `test_no_secret_fields` |

## 7. CLI

```bash
python scripts/run_combo_correlation_risk.py \
    --signals-file path/to/signals.json \
    --symbol 005930 \
    --output-dir reports/strategy_combo \
    --min-signals 5 \
    --pass-overlap 0.20 --watch-overlap 0.40 \
    --pass-same-dir 0.40 --watch-same-dir 0.60 --block-same-dir 0.85 \
    --pass-concentration 0.50 --watch-concentration 0.65 --block-concentration 0.85 \
    --pass-conflict 0.10 --block-conflict 0.40
```

signals JSON 형식은 3-12 (`StrategySignal`) 와 동일.

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — 순수 분석 + 파일 출력
- ✅ `is_live_authorization=False` 영구
- ✅ `recommended_for_paper=False` 영구 — BLOCK / PASS 모두 자동 적용 0건
- ✅ 안전 flag default 변경 0건
- ✅ secret 필드 0건

## 9. 후속 PR 권고

- **3-15 Paper 후보 확정 gate** — 본 risk 분석 + 3-12 / 3-13 의 PASS 결과를
  운영자 명시 검토 후 paper 후보로 promote 하는 단일 gate.
- **PnL Pearson 상관계수** — 실제 수익률 시계열 도입 후 `correlation_score`
  를 *proxy* 대신 직접 계산.
- **Sector / theme concentration** — 본 PR 은 symbol 단위 집중도 만. 후속
  PR 에서 sector / theme 차원 집중도 추가.
- **Cross-regime aggregation** — 본 PR 은 단일 시점 (모든 signal 평가).
  3-13 의 regime 라벨과 결합해 *장세별* 위험 매트릭스 확장 가능.
