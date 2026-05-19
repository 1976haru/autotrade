# 최종 Paper 조합 후보 선정 (3-15)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 리포트는 *advisory* — Paper Auto Loop 자동 연결 0건. 선정된 후보도
> `requires_operator_approval=True` 영구이며, 운영자 명시 승인 + 별도 PR
> 후에만 실제 입력으로 사용 가능.

## 1. 목적

3-02 ~ 3-14 의 advisory 분석 결과를 *종합* 해, AI Paper Auto Loop 에 넣을
**최종 Paper 운용 후보 1~3개** 를 선정한다. 통과 조건이 부족하면 *억지로
후보를 만들지 않고* `NO_CANDIDATE` + 사유 리스트를 반환.

본 모듈은 *후보 선정* 까지이며 *연결 자체* 는 별도 PR / 운영자 흐름. 어떤
선정 결과도 자동으로 Paper Auto Loop 에 들어가지 않는다.

## 2. 입력 (`CandidateInput`)

단일 `(combo or strategy, symbol)` row 가 carry 하는 모든 측정값:

| 카테고리 | 필드 | 출처 |
|---|---|---|
| 식별자 | `name` / `included_tactics` / `included_strategies` / `symbol` / `params` / `primary_regime` | 3-12 |
| 백테스트 metric | `trade_count` / `expectancy` / `profit_factor` / `max_drawdown` / `win_rate` / `loss_streak` / `total_return` | 3-02 / 3-06 |
| paper 후보 status | `paper_candidate_status` (READY_FOR_PAPER / WATCHLIST_ONLY / REJECTED / OVERFIT_RISK / STRESS_FAILED / INSUFFICIENT_DATA) | 3-07 |
| walk-forward verdict | `walk_forward_verdict` (HEALTHY / WATCH / DECAY_WARNING / DISABLE_CANDIDATE / INSUFFICIENT_DATA) | 3-04 |
| stress test verdict | `stress_verdict` (PASS / WARN / FAIL / INSUFFICIENT_DATA) | 3-05 |
| combo backtest verdict | `combo_verdict` (PASS / WARN / FAIL / INSUFFICIENT_DATA) | 3-12 |
| regime combo verdict | `regime_combo_verdict` (PASS / WATCH / FAIL / INSUFFICIENT_DATA / BLOCKED_REGIME) | 3-13 |
| combo risk verdict | `combo_risk_verdict` (PASS / WATCH / HIGH_RISK / BLOCK / INSUFFICIENT_DATA) | 3-14 |
| 보조 | `confirmation_score` / `correlation_score` / `concentration_score` | 3-12 / 3-14 |

## 3. 후보 선정 7 조건 (전부 통과 필수)

| 조건 | 통과 기준 |
|---|---|
| 1. paper status | `paper_candidate_status ∈ {READY_FOR_PAPER, WATCHLIST_ONLY}` |
| 2. backtest metric | `trade_count ≥ min_trades` AND `expectancy > 0` AND `profit_factor ≥ min_pf` AND `\|max_drawdown\| ≤ max_mdd` |
| 3. walk-forward | `walk_forward_verdict ∈ {HEALTHY, WATCH}` |
| 4. stress test | `stress_verdict ∈ {PASS, WARN}` |
| 5. combo backtest | `combo_verdict ∈ {PASS, WARN}` |
| 6. regime combo | `regime_combo_verdict ∈ {PASS, WATCH}` AND `primary_regime ∉ {LOW_LIQUIDITY, UNKNOWN}` |
| 7. combo risk | `combo_risk_verdict ∈ {PASS, WATCH}` (HIGH_RISK / BLOCK 자동 제외) |

자동 제외 라벨:
- `paper_candidate_status` 가 `REJECTED` / `OVERFIT_RISK` / `STRESS_FAILED` / `INSUFFICIENT_DATA` → 무조건 제외
- `primary_regime` 이 `LOW_LIQUIDITY` 또는 `UNKNOWN` → 무조건 제외
- `regime_combo_verdict == "BLOCKED_REGIME"` → 무조건 제외
- `combo_risk_verdict == "BLOCK"` 또는 `"HIGH_RISK"` → 무조건 제외

## 4. 선정 정책

```
qualified = [c for c in inputs if _qualify(c)]
qualified.sort(key=composite_score, reverse=True)
top_n = qualified[:max_candidates]  # default max_candidates=3

if len(top_n) == 0:    status = NO_CANDIDATE
elif len(top_n) == 1:  status = MIN_CANDIDATES
else:                   status = OK
```

`composite_score = 0.4 × profit_factor_norm + 0.3 × expectancy_norm +
0.2 × drawdown_score + 0.1 × confirmation_bonus`

- `profit_factor_norm`: `(pf - min_pf) / (3.0 - min_pf)` clamp [0,1]
- `expectancy_norm`: `expectancy / 1000.0` clamp [0,1]
- `drawdown_score`: `1 - mdd / max_mdd` (mdd=0 → 1, mdd=max_mdd → 0)
- `confirmation_bonus`: `confirmation_score / 10` clamp [0,1]

## 5. status 매트릭스

| Status | 의미 |
|---|---|
| `OK` | 2~3 후보 통과 (필수: candidates ≥ 2) |
| `MIN_CANDIDATES` | 정확히 1 후보 통과 — 운영자가 단일 후보로 진행할지 결정 |
| `NO_CANDIDATE` | 0 후보 통과 — `reasons_no_candidate` 에 가장 흔한 제외 사유 5개 carry |

## 6. 결과 파일

`reports/final_paper/` 디렉토리:

| 파일 | 형식 | 용도 |
|---|---|---|
| `final_paper_candidates_summary.json` | JSON | 전체 결과 + criteria + invariants |
| `final_paper_candidates_report.md` | Markdown | 후보 detail (rank / 사유 / 위험 flag) + 제외 후보 top 20 + 안전 invariant |
| `final_paper_candidates_ranking.csv` | CSV | rank 순 + 모든 verdict carry + `requires_operator_approval=true` 영구 |

`.gitignore` 의 `reports/*` 로 git 커밋 0건 (테스트로 lock).

## 7. AI Agent 연결

각 `PaperCandidate.to_dict()` 에 영구 carry:

```jsonc
{
  "rank": 1,
  "name": "MOMENTUM+VWAP",
  "included_tactics": ["MOMENTUM", "VWAP"],
  "included_strategies": ["sma_crossover", "volume_breakout", "vwap_strategy"],
  "symbol": "005930",
  "params": { ... },
  "primary_regime": "TREND_UP",
  "composite_score": 0.7234,
  "recommended_for_paper": true,
  "requires_operator_approval": true,    // 영구 — 자동 적용 X
  "is_order_signal": false,
  "auto_apply_allowed": false,
  "is_live_authorization": false,
  ...
}
```

**`requires_operator_approval=True` 영구** — AI Agent / Paper Auto Loop 가
본 리스트를 *읽을 수* 있지만, *자동* 으로 Paper 흐름에 연결하지 못한다.
연결은 별도 PR + 운영자 명시 승인 이후에만.

## 8. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `PaperCandidate.is_order_signal/auto_apply_allowed/is_live_authorization=False` 영구 | `__post_init__` ValueError |
| `PaperCandidate.requires_operator_approval=True` 영구 (False 시도 ValueError) | 위 |
| `FinalCandidateReport.is_order_signal` 외 2종 영구 | 위 |
| status / candidate 개수 일관성 (NO_CANDIDATE=0, MIN=1, OK≥2) | `FinalCandidateReport.__post_init__` |
| 7 조건 각각이 실패 시 exclusion + risk_flag | `TestSelectionConditions` (21 cases) |
| OVERFIT_RISK / STRESS_FAILED / REJECTED 자동 제외 | 위 |
| LOW_LIQUIDITY / UNKNOWN primary_regime 자동 제외 | 위 |
| combo_risk HIGH_RISK / BLOCK 자동 제외 | 위 |
| Walk-forward WATCH / Stress WARN / Combo WARN / regime WATCH / risk WATCH 통과 | 위 |
| 후보 정렬 composite_score desc | `TestTopN` |
| max_candidates 기본 3 / override 가능 | 위 |
| Report 파일 3종 생성 + invariant carry | `TestReportFiles` (6 cases) |
| `reports/` git ignore | `test_reports_dir_gitignored` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| settings mutation 0건 | 위 |
| schema 에 secret 필드 0건 | `test_no_secret_fields` |

## 9. CLI

```bash
python scripts/run_final_paper_candidates.py \
    --inputs-file path/to/candidate_inputs.json \
    --output-dir reports/final_paper \
    --period "2026-05" \
    --min-trades 10 \
    --min-profit-factor 1.2 \
    --max-drawdown-abs 0.20 \
    --max-candidates 3
```

inputs.json 형식은 `CandidateInput` list (위 §2 의 모든 필드).

## 10. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — 순수 분석 + 파일 출력
- ✅ `is_live_authorization=False` 영구 — 모든 후보, 모든 응답
- ✅ `requires_operator_approval=True` 영구 — 자동 Paper Auto Loop 연결 0건
- ✅ 안전 flag default 변경 0건
- ✅ secret 필드 0건
- ✅ 후보가 없으면 NO_CANDIDATE — 억지 생성 0건

## 11. 후속 PR 권고

- **4-PaperConnect** — 본 모듈의 선정 결과를 운영자가 검토한 뒤 *명시 PR*
  로 Paper Auto Loop input adapter 에 wire. 본 PR 은 *분석까지* 만.
- **AgentDecisionLog 통합** — 후보 선정 사유를 chain_id 와 함께 영구 기록.
- **시계열 누적** — 매 운영 cycle 마다 후보 변화 추적 (안정성 검증).
- **운영자 UI** — `FinalPaperCandidatesCard.jsx` 카드 (선정 후보 표 +
  "Paper Loop 입력으로 사용" 버튼 — 클릭 시 *별도 승인 모달* 거쳐 운영자
  명시 승인 시점에만 wire).
