# Step 4-02 — Strategy Combination Recommendation

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 추천은 *오늘 모의투자(Paper) 검토용 advisory* 입니다.
> **주문 신호가 아닙니다.** **Paper trader 자동 시작 / 실거래 활성화를 수행하지
> 않습니다.**

## 1. 목적

4-01 `StrategyAgentInput` 또는 3-08 `OperatorReport` 를 입력으로 받아, AI Agent
가 *오늘* Paper 모의운용에서 검토할 전략 *조합* 을 결정론적 휴리스틱으로
*추천* 합니다.

핵심 의도:
- 전략별 **3 액션** (`RECOMMEND` / `HOLD` / `EXCLUDE`) 으로 *비개발자가 즉시
  판단* 가능.
- 후보가 있으면 *상위 N* 조합 (default 2) 을 *다양성 + score 우선* 으로 선정.
- 후보가 없으면 **"오늘은 자동 운용 후보 없음"** 으로 명확히 표시.
- 본 결과는 *advisory only* — 운영자가 BotControl / Paper Auto Loop 흐름에서
  *명시 시작* 해야 합니다.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/agents/strategy_combination_recommender.py` | 모듈 + Agent |
| `backend/tests/test_strategy_combination_recommender.py` | 단위 / 통합 테스트 |
| `docs/strategy_combination_recommendation.md` | 본 정책 (현재 문서) |

## 3. 액션 분류 (`StrategyAction`)

| Action | 조건 | 의미 (한국어) |
|---|---|---|
| `RECOMMEND` | `paper_candidate_status=READY_FOR_PAPER` AND `len(risk_flags) < threshold` | "추천 (오늘 Paper 모의투자 검토 가능)" |
| `HOLD` | `READY_FOR_PAPER` AND `len(risk_flags) >= threshold` | "보류 (위험 신호 다수 — 추가 관찰)" |
| `EXCLUDE` | 위 외 모든 status (`NEED_MORE_DATA` / `OVERFIT_RISK` / `STRESS_FAILED` / `REJECTED_BY_RISK` / `NO_CANDIDATE`) | "제외 (오늘 사용 안 함)" |

기본 `hold_risk_flag_threshold = 2` — risk_signals 2개 이상이면 보류.
운영자가 `--hold-risk-flag-threshold` 로 조정 가능 (별도 PR 시).

**HOLD 는 *보류* 의미이며 *주문 신호* 가 아니다** — 본 enum 에 `BUY` / `SELL`
같은 주문 방향 값은 *영구 금지* (테스트로 lock).

## 4. 조합 선정 휴리스틱

1. **score 내림차순 정렬** — 후보 정렬 기준.
2. **첫 후보 무조건 포함** — 점수 1위.
3. **다음 후보 선정**:
   - 우선순위 ① — 이미 선정된 것과 *다른 strategy* 인 후보.
   - 우선순위 ② — 그 안에서 *다른 symbol* 인 후보.
   - 점수 동률이면 score 높은 쪽.
4. **max_combo_size 도달 또는 다양성 0 시 종료**.
5. **나머지 후보는 `HOLD` 로 demote** + `reasons` 에 `demoted_from_recommend` carry.
6. **다양성 부족 경고**:
   - 모두 같은 strategy → `operator_notes` 에 "운영자 검토 권고" 추가.
   - 모두 같은 symbol → "분산 효과 제한" 추가.

## 5. Overall 상태 (`OverallRecommendation`)

| 상태 | 조건 | 운영자 한 줄 |
|---|---|---|
| `HAS_RECOMMENDATIONS` | `recommended_combo` 1개 이상 | "오늘 모의투자(Paper) 검토 가능한 추천 조합 있음" |
| `ALL_HOLD` | 모든 후보가 `HOLD` (RECOMMEND 0개) | "후보는 있으나 위험 신호 다수 — 모두 보류" |
| `NO_CANDIDATES_TODAY` | 모두 `EXCLUDE` 또는 input 0건 | "오늘은 자동 운용 후보 없음" |
| `NEEDS_OPERATOR_REVIEW` | 추천 후보는 있는데 조합 선정 0 (안전 fallback) | "운영자 판단 필요 — 다양성 부족 또는 자료 결손" |

## 6. 출력 스키마 (`StrategyCombinationRecommendation`)

```jsonc
{
  "generated_at":             "2026-05-17T...",
  "schema_version":           "1.0",
  "overall_recommendation":   "HAS_RECOMMENDATIONS",
  "overall_label_ko":         "오늘 모의투자(Paper) 검토 가능한 추천 조합 있음",
  "recommended_count":        2,
  "held_count":               0,
  "excluded_count":           0,
  "recommended_combo":        [{...StrategyDecision...}],
  "held":                     [],
  "excluded":                 [],
  "decisions":                [...all decisions...],
  "reasons_no_candidate":     [],
  "operator_notes":           ["..."],
  "advisory_disclaimer":      "본 추천은 *오늘 모의투자(Paper) 검토용 advisory*...",
  "metadata":                 {"pipeline": "step4-02-...", ...},
  // 최상위 invariant.
  "is_order_signal":          false,
  "auto_apply_allowed":       false,
  "is_live_authorization":    false,
  "auto_start_paper_trader":  false
}
```

`StrategyDecision` (per-strategy, 11 필수 필드):

```jsonc
{
  "strategy":                "sma_crossover",
  "symbol":                  "005930",
  "params":                  {"short": 5, "long": 20},
  "action":                  "RECOMMEND",
  "action_label_ko":         "추천 (오늘 Paper 모의투자 검토 가능)",
  "paper_candidate_status":  "READY_FOR_PAPER",
  "score":                   0.05,
  "risk_flags":              [],
  "reasons":                 ["paper_candidate=READY_FOR_PAPER, ..."],
  // 데이터 단위 invariant.
  "is_order_signal":         false,
  "auto_apply_allowed":      false,
  "is_live_authorization":   false
}
```

## 7. 절대 invariant (테스트로 lock)

| 항목 | 강제 방식 |
|---|---|
| `StrategyDecision.is_order_signal=False` | `__post_init__` ValueError |
| `StrategyDecision.auto_apply_allowed=False` | `__post_init__` ValueError |
| `StrategyDecision.is_live_authorization=False` | `__post_init__` ValueError |
| `StrategyCombinationRecommendation.is_order_signal=False` | `__post_init__` ValueError |
| `StrategyCombinationRecommendation.auto_apply_allowed=False` | `__post_init__` ValueError |
| `StrategyCombinationRecommendation.is_live_authorization=False` | `__post_init__` ValueError |
| `StrategyCombinationRecommendation.auto_start_paper_trader=False` | `__post_init__` ValueError |
| `StrategyAction` enum 에 `BUY`/`SELL`/`PLACE_ORDER`/`EXECUTE` 값 0개 | `test_action_enum_has_no_buy_sell_hold_order_values` |
| broker / OrderExecutor / route_order import 0건 | 정적 grep |
| 외부 HTTP / AI SDK import 0건 | 정적 grep |
| `app.core.config.get_settings` import 0건 | 정적 grep |
| `settings.enable_*_trading =` mutate 0건 | 정적 grep |
| schema 자체에 API key / Secret / 계좌번호 필드 0건 | `test_schema_has_no_secret_fields` |
| Agent output JSON 에 "BUY"/"SELL"/"Place Order"/"지금 매수"/"지금 매도"/"실거래 시작" 0건 | 통합 테스트 |

## 8. Agent 호환성 (#51 Agent Architecture)

`StrategyCombinationRecommenderAgent` 는 `AgentBase` 호환:
- `metadata.role = AgentRole.STRATEGY_RESEARCHER`
- `metadata.can_execute_order = False` (영구)
- `run(context) -> AgentOutput(decision=RECOMMEND, summary, reasons, risk_flags, metadata=...)`
- `AgentOutput.is_order_intent=False` / `can_execute_order=False` (AgentBase 가드 상속)

`AgentDecision.RECOMMEND` 는 #51 표준 decision label 중 *advisory recommendation*
의미 — 주문 신호 *아님*.

## 9. 입력 우선순위

`build_combination_recommendation` 인자 우선순위:
1. `agent_input` (`StrategyAgentInput` from 4-01)
2. `operator_report` (`OperatorReport` from 3-08) — 빌더 호출
3. `inputs` (`ReportInputs` — raw 5 단계 경로)
4. 모두 None — 빈 입력 → `NO_CANDIDATES_TODAY`

Agent.run() 의 context.extra 우선순위:
1. `combination_recommendation` (이미 빌드된 객체 passthrough)
2. `strategy_agent_input`
3. `operator_report`
4. fallback — 빈 입력

## 10. CLAUDE.md 절대 원칙 준수

- ✅ `RiskManager → PermissionGate → OrderExecutor` 흐름 *변경 0건*.
- ✅ 본 모듈은 *read-only* — broker / DB / 외부 API 호출 0건.
- ✅ `is_order_signal=False` 영구 — 본 추천 결과로 직접 주문 생성 *불가능*.
- ✅ Paper trader 자동 시작 *불가능* — `auto_start_paper_trader=False` 영구.
- ✅ 운영 모드 / 안전 flag default 변경 0건.
- ✅ LLM 호출 *0건* — 본 모듈은 결정론적 휴리스틱.

## 11. 운영자 검토 흐름

1. 본 모듈 출력 (`recommended_combo`) 검토.
2. `risk_flags` / `operator_notes` 확인 — 다양성 부족 / 위험 신호 있으면 보완.
3. 모의투자(Paper) 에 *수동* 입력 — Paper Auto Loop (#2-01 ~ #2-08).
4. 본 모듈은 paper trader 를 *시작하지 않는다* — 운영자가 BotControl 에서 시작.

## 12. 테스트

```bash
python -m pytest backend/tests/test_strategy_combination_recommender.py -q
python -m pytest backend/tests/test_repository_hygiene.py -q
python scripts/security_scan.py
```

## 13. Schema 진화 정책

- `schema_version` 은 *명시 옵트인 PR* 로만 bump.
- 새 액션 (4 번째 Action enum 값) 추가 절대 금지 → 별도 PR + 본 문서 갱신 필요.
- invariant 필드 (`is_order_signal` / `auto_apply_allowed` /
  `is_live_authorization` / `auto_start_paper_trader`) *추가 / 변경 / 삭제 영구 금지*.
- API key / Secret / 계좌번호 필드 *영구 금지*.

---

## 14. v2 API — `PaperStrategyCombination` (병행 제공, #4-02 v2)

**v1** (`build_combination_recommendation` + `StrategyCombinationRecommendation`)
은 4-03/4-04 (`apply_overfit_filter` / `apply_regime_filter`) 의 dependency 로
유지. v2 는 *사용자 spec 의 더 fine-grained state 매트릭스* 를 제공하는 **별도
API** (additive — 같은 모듈 안 병행 export).

### 14.1. v2 5-state matrix (`PaperCombinationStatus`)

v1 의 4 state (HAS_RECOMMENDATIONS / ALL_HOLD / NO_CANDIDATES_TODAY /
NEEDS_OPERATOR_REVIEW) 와 별개로, v2 는 *위험 / 데이터 부족 사유를 분리* :

| Status | 의미 |
|---|---|
| `RECOMMEND_PAPER` | 1개 이상 paper 추천 가능 |
| `WATCH_ONLY` | 후보 있으나 보류 (위험 신호 / 검증 부족 혼합) |
| `NO_CANDIDATE` | 분석 가능한 후보 0건 (파이프라인 결과 부재) |
| `REJECTED_BY_RISK` | 모든 후보가 위험 한도 위반 / 검증 미통과로 차단 |
| `NEED_MORE_DATA` | 모든 후보가 NEED_MORE_DATA — 데이터 부족 |

`BUY` / `SELL` / `EXECUTE` / `PLACE_ORDER` 같은 *주문 방향* 값 0개 — 테스트 lock.

### 14.2. v2 7 출력 필드 (`PaperStrategyCombination.to_dict()`)

사용자 spec 의 7 필드 정확히 매핑:

| 필드 | 의미 |
|---|---|
| `recommended_strategies` | `PaperStrategyEntry` list — 추천 후보 (default 최대 2) |
| `excluded_strategies` | 제외 후보 (OVERFIT_RISK / STRESS_FAILED / REJECTED_BY_RISK) |
| `watchlist_strategies` | 보류 후보 (READY+risk_flags>=2 / NEED_MORE_DATA / 조합 상한 demote) |
| `no_candidate_reason` | `str \| None` — 후보 0건 / 모두 차단 시 한국어 사유 |
| `risk_summary` | `list[str]` — 위험 신호 합집합 + 다양성 경고 |
| `agent_rationale` | `str` — 운영자 한 줄 요약 |
| `operator_next_action` | `list[str]` — 다음 행동 권고 |

invariant (`is_order_signal=False` / `auto_apply_allowed=False` /
`is_live_authorization=False`) 양 레벨 (top + per-entry) 강제.

### 14.3. v2 분류 매트릭스

| paper_candidate_status | risk_flags | bucket | overall 영향 |
|---|---|---|---|
| `READY_FOR_PAPER` | < threshold (default 2) | **recommended** | `RECOMMEND_PAPER` |
| `READY_FOR_PAPER` | ≥ threshold | **watchlist** | (count + 분포에 따라) `WATCH_ONLY` |
| `NEED_MORE_DATA` | any | **watchlist** | 모두 NEED_MORE_DATA → `NEED_MORE_DATA` |
| `OVERFIT_RISK` | any | **excluded** | 모두 차단 → `REJECTED_BY_RISK` |
| `STRESS_FAILED` | any | **excluded** | 위 |
| `REJECTED_BY_RISK` | any | **excluded** | 위 |
| `NO_CANDIDATE` | any | **excluded** | 위 |
| (입력 0건) | — | — | `NO_CANDIDATE` |

`max_recommended` (default 2) 초과 추천 후보는 *watchlist* 로 demote (`rationale`
에 "조합 상한 초과로 demote" carry).

### 14.4. 동일 종목/전략 쏠림 경고

`recommended_strategies` 가 2개 이상이고:
- 모두 같은 strategy → `risk_summary` 에 "전략 다양성 부족" carry
- 모두 같은 symbol → `risk_summary` 에 "분산 효과 제한" carry

### 14.5. v1 ↔ v2 cross-reference

본 모듈은 *같은 파일* (`backend/app/agents/strategy_combination_recommender.py`)
에서 v1 + v2 *둘 다* export. caller 가 필요에 따라 선택:

- **v1 (`build_combination_recommendation`)** — 4-03 `apply_overfit_filter` /
  4-04 `apply_regime_filter` 의 입력. *기존 dependency 가 사용*.
- **v2 (`build_paper_combination_recommendation`)** — 사용자 spec 의 새 state
  매트릭스 + 7 출력 필드 필요한 새 caller.

향후 정책 통합 시 별도 옵트인 PR 로 v1 → v2 migration 검토.
