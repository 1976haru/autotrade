# Step 3-07 -- Paper 후보 설정 생성

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> Paper 후보 export 는 *운영자 검토 자료* — 자동 paper trader 시작 / 자동
> 실거래 활성화 / 자동 promotion 변경 의미 X.

## 1. 목적

3-02 (real data backtest) / 3-03 (parameter optimization) / 3-04
(walk-forward) / 3-05 (stress test) 의 모든 산출물을 *종합* 해 (strategy,
symbol, params) 조합이 *모든 4 단계* 를 통과했는지 추적. 통과한 후보 상위
1~2개를 단일 `paper_candidate_config.json` 으로 export.

핵심 원칙:
- **후보가 없으면 억지로 만들지 않음** — `candidates: []` + `reasons_no_candidate`
  채워서 *반드시* 파일 생성.
- **모든 단계 통과 필수** — 한 단계라도 누락 / 실패 → 자격 박탈.
- **단계별 통과 라벨 carry** — `pipeline_stages` 필드 보존, 운영자가
  누락 단계 즉시 확인.

## 2. 통과 기준

| 단계 | 입력 파일 | 통과 verdict |
|---|---|---|
| 3-02 | `reports/backtest_real/real_data_backtest_summary.json` | `BACKTEST_PASS` |
| 3-03 | `reports/parameter_optimization/parameter_optimization_summary.json` | `PAPER_CANDIDATE` |
| 3-04 | `reports/walk_forward/walk_forward_summary.json` | `HEALTHY` |
| 3-05 | `reports/stress_test/stress_test_summary.json` | **모든 시나리오 PASS** |

3-05 는 10 시나리오를 가지므로 *모두 PASS* 일 때만 stage 통과. 하나라도 FAIL
이면 stage verdict 가 FAIL (worst-case carry).

## 3. CLI

```bash
# 1) 4 단계 모두 입력 (권장).
python scripts/run_paper_candidate_aggregator.py \
    --from-backtest      reports/backtest_real/real_data_backtest_summary.json \
    --from-optimization  reports/parameter_optimization/parameter_optimization_summary.json \
    --from-walk-forward  reports/walk_forward/walk_forward_summary.json \
    --from-stress-test   reports/stress_test/stress_test_summary.json

# 2) dry-run — stdout 요약, 파일 작성 X.
python scripts/run_paper_candidate_aggregator.py --dry-run \
    --from-backtest reports/backtest_real/real_data_backtest_summary.json \
    --from-optimization reports/parameter_optimization/parameter_optimization_summary.json \
    --from-walk-forward reports/walk_forward/walk_forward_summary.json \
    --from-stress-test reports/stress_test/stress_test_summary.json

# 3) top-k / required-stages override.
python scripts/run_paper_candidate_aggregator.py \
    --top-k 1 --required-stages 3-02 3-03 3-04 3-05 \
    --from-backtest ... [생략]
```

## 4. 산출물

**경로**: `reports/strategy_optimization/paper_candidate_config.json`
(`reports/*` gitignore — git 미커밋. 테스트는 `tmp_path` 에서만 생성 확인).

### 4.1. 스키마

```jsonc
{
  "generated_at":           "2026-05-17T...",
  "is_order_signal":        false,
  "auto_apply_allowed":     false,
  "is_live_authorization":  false,
  "candidate_count":        0 | 1 | 2,
  "candidates": [
    {
      "strategy":          "sma_crossover",
      "symbol":            "005930",
      "params":            { "short": 5, "long": 20 },
      "score":             0.0567,
      "pipeline_stages": [
        { "name": "3-02", "verdict": "BACKTEST_PASS", "extra": {...} },
        { "name": "3-03", "verdict": "PAPER_CANDIDATE", "extra": {...} },
        { "name": "3-04", "verdict": "HEALTHY", "extra": {...} },
        { "name": "3-05", "verdict": "PASS", "extra": {...} }
      ],
      "risk_metrics":      { ... 3-06 표준 14 키 ... },
      "passed_stages":     ["3-02", "3-03", "3-04", "3-05"],
      "all_stages_passed": true,
      "is_order_signal":      false,
      "auto_apply_allowed":   false
    }
  ],
  "reasons_no_candidate": [
    "3-04_missing_for_2_candidate(s)",
    "3-05_did_not_pass_for_1_candidate(s)",
    "no_candidate_passed_all_required_stages_['3-02', '3-03', '3-04', '3-05']"
  ],
  "metadata": {
    "pipeline":         "step3-07-paper-candidate-aggregator",
    "required_stages":  ["3-02", "3-03", "3-04", "3-05"],
    "input_paths":      { "backtest": ..., "optimization": ..., ... },
    "total_aggregated": 30
  }
}
```

### 4.2. 절대 invariant (테스트 lock)

- 최상위 `is_order_signal: false` / `auto_apply_allowed: false` /
  `is_live_authorization: false`.
- 각 candidate 객체 `is_order_signal: false` / `auto_apply_allowed: false`.
- BUY/SELL/HOLD/Place Order/실거래 시작/ENABLE_LIVE_TRADING 단어 0건.
- 후보 0건도 파일 생성 — `candidates: []` + `reasons_no_candidate` 채움.

## 5. 후보 0건 시 사유 ('reasons_no_candidate')

후보가 없으면 다음 정보를 carry:

- `3-02_missing_for_N_candidate(s)` — 3-02 단계 결과가 없는 후보 N개
- `3-04_did_not_pass_for_N_candidate(s)` — 3-04 단계가 HEALTHY 가 아닌 N개
- (마지막) `no_candidate_passed_all_required_stages_['3-02', ..., '3-05']`

운영자는 이 사유를 보고 *어떤 단계에서 실패했는지* 즉시 판단 가능.

## 6. 3-07 완료 기준

| 항목 | 본 PR 상태 |
|---|---|
| 4 단계 (3-02 ~ 3-05) 산출물 종합 | ✓ `aggregate_candidates` |
| (strategy, symbol, params) 식별 + 단계별 verdict carry | ✓ `_candidate_key` + `PipelineStage` |
| 모든 단계 통과 후보만 export | ✓ `all_stages_passed()` filter |
| 상위 1~2 (default top_k=2) | ✓ `build_paper_candidate_config(top_k=...)` |
| 후보 0건도 파일 생성 + 사유 명시 | ✓ `reasons_no_candidate` |
| 산출물 경로 `reports/strategy_optimization/paper_candidate_config.json` | ✓ CLI default |
| 최상위 + candidate 객체 invariant | ✓ `is_order_signal=false` 등 |
| broker / OrderExecutor / route_order 호출 0건 | ✓ 정적 grep + 테스트 lock |
| 안전 flag default 변경 0건 | ✓ |
| `reports/*` gitignore — 산출물 git 미커밋 | ✓ |
| 테스트는 `tmp_path` 에서 생성 확인만 | ✓ |

## 7. 운영자 검토 흐름 (3-07 이후 단계)

1. `paper_candidate_config.json` 검토.
2. `candidates` 가 있으면 → *수동* 으로 Paper Auto Loop (#2-01 ~ #2-08)
   에 입력. **자동 활성화 / 자동 paper trader 시작 절대 금지**.
3. `candidate_count == 0` 이면 `reasons_no_candidate` 확인 후 단계별 grid /
   임계값 조정 (별도 PR).
4. Paper 운용 4 주+ 후 #72 Paper Gate / #73 Live Manual Gate 평가 (별도 단계).

## 8. CLAUDE.md 절대 원칙

- broker / OrderExecutor / route_order import 0건 (정적 grep + 테스트 lock).
- KIS 주문 API / Anthropic / OpenAI / 외부 HTTP import 0건.
- 실거래 / Place Order 0건. 본 모듈은 *분석 read-only*.
- 안전 flag default 변경 0건: `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false`
  / `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- `PAPER_CANDIDATE` 라벨은 *paper 운용 후보 검토* — paper trader 자동 시작 /
  실거래 활성화 / 자동 promotion 변경 의미 X.
- `reports/*` gitignore — 산출물 git 미커밋. 테스트는 `tmp_path` 에서만.
