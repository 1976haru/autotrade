# Paper 운용 성향 비교 리포트 (Risk Profile Paper Comparison)

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 리포트는 *advisory* — Paper 가상 체결 metric 비교만, 실거래 주문 0건.

## 1. 목적

같은 기간 + 같은 explanation/가격 입력에 대해 CONSERVATIVE / BALANCED /
AGGRESSIVE 3 프리셋을 *각각* Paper bridge 로 실행해 결과를 비교한다. 운영자가
"어떤 성향이 본 데이터에서 어떤 동작을 만드는지" 를 한눈에 볼 수 있도록 한다.

**핵심 정책**:
- 같은 데이터를 사용 — 프리셋의 *임계값 차이만* 비교 대상.
- 기본 추천은 **항상 BALANCED** — 손실 방어 우선. AGGRESSIVE 가 metric 상
  우위라도 *자동* 추천하지 않는다.
- 본 리포트는 *자동 프리셋 변경 0건* — 운영자 명시 검토 + 별도 옵트인 PR 후에만
  다른 프리셋으로 전환 가능.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/analytics/risk_profile_comparison.py` | `ProfileResult` / `ComparisonReport` + `compare_profiles()` + `render_markdown()` / `render_ranking_csv()` / `write_reports()` |
| `backend/tests/test_risk_profile_comparison.py` | 35 cases — 3 프리셋 비교 / BALANCED 기본 / AGGRESSIVE 안전장치 / INSUFFICIENT_DATA / 리포트 파일 / 정적 가드 |
| `reports/paper_profile_compare/` | 생성 결과물 (gitignore — git 커밋 0건) |
| `docs/risk_profile_paper_comparison.md` | 본 정책 |

## 3. 비교 지표

| 지표 | 의미 |
|---|---|
| `signal_count` | explanation 의 entry 수 (recommended + watchlist + excluded) |
| `paper_decision_count` | bridge 가 만든 `PaperDecision` 수 |
| `buy_count / sell_count / hold_count / exit_count / no_op_count` | action 별 분포 |
| `buy_ratio / hold_ratio / ...` | 위 카운트 / `paper_decision_count` |
| `win_rate` | filled trade 중 pnl > 0 비율 (`None` if no fills) |
| `expectancy` | mean pnl per filled trade |
| `profit_factor` | `sum(positive) / |sum(negative)|` |
| `max_drawdown` | 누적 pnl 의 최저 trough |
| `loss_streak` | 연속 losing trade 최댓값 |
| `risk_veto_count` | veto BLOCK 으로 차단된 decision 수 |
| `stale_data_violation_count` | `STALE_DATA` flag 매칭 수 |
| `duplicate_signal_count` | `DUPLICATE_SIGNAL` flag 매칭 수 |
| `position_size_avg` | sized BUY/SELL/EXIT 의 평균 quantity |
| `paper_pnl_estimate` | 누적 pnl 추정 (caller 가 `pnl_lookup` 으로 per-share 손익 전달) |

## 4. 결과 파일 (`write_reports`)

`reports/paper_profile_compare/` 디렉토리에 3개 파일 생성:

| 파일 | 형식 | 용도 |
|---|---|---|
| `risk_profile_comparison_summary.json` | JSON | 기계 판독용 — schema_version / results / recommended_profile / invariants |
| `risk_profile_comparison_report.md` | Markdown | 운영자 친화 한글 리포트 — 매트릭스 + 추천 사유 + 안전 invariant 섹션 |
| `risk_profile_comparison_ranking.csv` | CSV | expectancy 내림차순 랭킹 — 프리셋 / expectancy / win_rate / profit_factor / MDD / loss_streak / 등 |

본 디렉토리는 `.gitignore` 의 `reports/*` 규칙으로 git 커밋 0건 (테스트에서
`tmp_path` 를 사용해 생성만 확인).

## 5. 추천 정책

**기본 추천 = `BALANCED`** (영구 invariant — `ComparisonReport.recommended_profile`).

본 모듈은:
- AGGRESSIVE 가 expectancy / win_rate / profit_factor 상 우위라도 **자동
  추천하지 않는다**.
- AGGRESSIVE 도 `is_live_authorization=False` 영구 — Paper 한정.
- INSUFFICIENT_DATA (`entry == 0`) 인 경우에도 `recommended_profile="BALANCED"`
  유지.

운영자가 다른 프리셋을 선택하려면:
1. 본 리포트 + 별도 Paper Gate (#72) / Live Manual Gate (#73) 통과
2. 명시 사용자 승인
3. 별도 옵트인 PR

## 6. 절대 invariant (테스트로 lock)

| 항목 | 검증 위치 |
|---|---|
| `ProfileResult.is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False` | `__post_init__` ValueError |
| `ComparisonReport.is_order_signal=False` 외 2종 동일 | 위 |
| `ComparisonReport.recommended_profile` 가 `RiskProfile` 멤버값 외 거부 | `__post_init__` ValueError |
| 3 프리셋 모두 결과에 등장 | `TestThreeProfilesCompared.test_all_three_profiles_in_results` |
| BALANCED 기본 추천 | `TestBalancedDefault.test_recommended_profile_is_balanced` |
| AGGRESSIVE pnl 우위 시에도 BALANCED 추천 | `test_balanced_default_even_when_aggressive_has_better_pnl` |
| AGGRESSIVE 결과 invariants False | `TestAggressiveSafetyGuard.test_aggressive_profile_result_invariants` |
| `is_live_authorization=False` 영구 | 위 |
| KisBrokerAdapter.place_order / cancel_order 호출 0건 | `test_no_broker_calls_under_any_profile` |
| INSUFFICIENT_DATA on empty explanation | `TestInsufficientData.test_empty_explanation_returns_insufficient_data` |
| position_size: CONS < BAL < AGG | `test_position_size_ordering_cons_lt_bal_lt_agg` |
| 1 stale_data flag: CONS BLOCK / BAL+AGG PASS | `test_risk_flag_blocks_only_conservative_when_one_flag` |
| JSON / MD / CSV 3 파일 생성 | `TestReportFileGeneration` (4 cases) |
| `reports/` git ignore | `test_reports_dir_is_gitignored` |
| schema 에 secret 필드 0건 | `TestSchemaSanity` |
| broker / OrderExecutor / route_order import 0건 | `TestStaticGuards` |
| Anthropic / OpenAI / httpx / requests import 0건 | 위 |
| `settings.enable_*` mutation 0건 | 위 |

## 7. API 사용 예시

```python
from app.analytics.risk_profile_comparison import (
    compare_profiles, write_reports,
)

report = compare_profiles(
    explanation=paper_start_explanation,
    loop_state="RUNNING",
    positions=[],
    price_lookup={("sma_crossover", "005930"): 70_000.0},
    account_equity=10_000_000.0,
    confidence_lookup={("sma_crossover", "005930"): 0.9},
    pnl_lookup={("sma_crossover", "005930"): 250.0},
    period_label="2026-05-01 ~ 2026-05-19",
)

# 보고서 파일 3종 생성.
paths = write_reports(report, "reports/paper_profile_compare")
print(paths["summary_json"], paths["report_md"], paths["ranking_csv"])

# 추천 프리셋.
assert report.recommended_profile == "BALANCED"
```

## 8. CLAUDE.md 절대 원칙 준수

- ✅ broker / OrderExecutor / route_order import 0건 (정적 AST 가드)
- ✅ KIS / Anthropic / OpenAI / 외부 HTTP / `httpx` / `requests` import 0건
- ✅ DB write 0건 — `compare_profiles` 가 bridge 를 `record=False` 로 호출하므로
  ledger / decision_log 영구 기록도 일어나지 않음
- ✅ 안전 flag default 변경 0건
- ✅ secret 필드 0건 (`api_key` / `account_number` 등 검사)
- ✅ `is_live_authorization=False` 영구 — 모든 프리셋, 모든 결과
- ✅ AGGRESSIVE 라벨이 실거래 활성화 허가가 아님 (영구 lock)
- ✅ 기본 추천은 항상 BALANCED — 자동 변경 0건

## 9. 후속 PR 권고

- **API endpoint** — `POST /api/analytics/risk-profile-comparison` (read-only
  + on-demand 리포트 반환).
- **AutoPaperLoopCard / Frontend 통합** — `AgentRiskProfileSelector` 옆에
  "이 데이터로 3 성향 비교" 버튼 — 리포트 미리보기 노출.
- **실제 운용 데이터 통합** — 본 PR 은 동기 입력 데이터 한 cycle. 후속 PR 에서
  AgentDecisionLog (4-10) 의 *과거 N일* 데이터를 재생해 동일 결과 비교 가능.
- **추천 정책 강화** — paper_pnl / max_drawdown / loss_streak 기반 *경고
  라벨* 추가 (예: AGGRESSIVE 가 MDD 가 너무 크면 "주의" 라벨). 단 추천 자체는
  여전히 BALANCED.
