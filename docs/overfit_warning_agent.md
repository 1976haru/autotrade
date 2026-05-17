# Step 4-03 — Overfit Warning Agent

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 경고는 *advisory* 입니다 — 전략 자동 비활성 / 자동 paper trader 시작 /
> 자동 실거래 활성화를 수행하지 **않습니다**.

## 1. 목적

Walk-forward (3-04) 결과를 *읽고* 과최적화 의심 전략을 식별해 4-02 의
`StrategyCombinationRecommendation` 의 `recommended_combo` 에서 *demote* 한다.

핵심 의도:
- `walk_forward_verdict == OVERFIT_RISK` 전략 → **추천 제외** (default) 또는
  *운영자 watchlist* (`demote_to_watchlist=True`).
- `walk_forward_verdict == HEALTHY` 이지만 train/validation 성과 차이가 *큰*
  전략 → **SUSPECT** 분류, **HOLD**.
- 모든 후보가 OVERFIT_RISK → overall `ALL_HOLD` 로 surface.
- 운영자에게 **"실제 Paper 운용 전 재검증 필요"** 메시지 carry.

## 2. 구현 위치

| 파일 | 의미 |
|---|---|
| `backend/app/agents/overfit_warning_agent.py` | 모듈 + Agent (신규 모듈) |
| `backend/tests/test_overfit_warning_agent.py` | 단위 / 통합 / invariant 테스트 |
| `docs/overfit_warning_agent.md` | 본 정책 (현재 문서) |

**왜 신규 모듈인가** — 4-01 `strategy_optimizer_agent.py` 는 *데이터 스키마*
계층 (read-only contract). 과최적화 *추천 필터링 로직* 은 별도 책임이므로
단일 책임 원칙(SRP) 에 따라 신규 모듈로 분리. 4-02 `strategy_combination_recommender.py`
와도 분리 — 4-02 는 *조합 선정*, 본 모듈은 *과최적화 필터*. 두 모듈을 합치면
gap 계산 / suspect 임계 / watchlist demote 같은 본 모듈의 옵션이 4-02 에
누적되어 책임이 흐려진다.

## 3. OverfitVerdict 4단계

| Verdict | 조건 | Action |
|---|---|---|
| `OVERFIT_RISK` | `walk_forward_verdict == "OVERFIT_RISK"` | `EXCLUDE` (default) / `WATCHLIST` |
| `SUSPECT` | `walk_forward_verdict == "HEALTHY"` AND `train_validation_gap >= suspect_threshold` (default 0.5) | `HOLD` |
| `INSUFFICIENT_DATA` | `walk_forward_verdict == "INSUFFICIENT_DATA"` 또는 없음 | `HOLD` (보수적) |
| `HEALTHY` | 그 외 | `KEEP` (상위 추천 유지) |

## 4. train_validation_gap 정규화

```python
gap = (train_expectancy_avg - val_expectancy_avg) / max(|train_expectancy_avg|, 1.0)
```

- 양수: train 우세 → overfit 의심 (>0).
- 음수: val 우세 → 안전 (overfit 아님).
- 데이터 부족: `None`.

예시:
- train=800, val=100 → gap=0.875 (강한 의심)
- train=600, val=580 → gap=0.033 (의심 없음)
- train=1000, val=400 → gap=0.6 (SUSPECT 후보)

## 5. OverfitAction 4 액션 — *주문 방향 0개*

| Action | 의미 (한국어) |
|---|---|
| `KEEP` | 상위 추천 유지 |
| `HOLD` | 보류 (SUSPECT / INSUFFICIENT_DATA) |
| `WATCHLIST` | 운영자 관찰 대상 (demote_to_watchlist=True 시) |
| `EXCLUDE` | 추천 제외 (OVERFIT_RISK default) |

**BUY/SELL/PLACE_ORDER 값 0개** — 본 enum 은 advisory 분류만 표현 (테스트로 lock).

## 6. 출력 6 필수 필드 (`OverfitWarning`)

`backend/tests/test_overfit_warning_agent.py::TestRequired6Fields` 가 lock.

| # | 필드 | 타입 | 의미 |
|---|---|---|---|
| 1 | `overfit_flag` | `bool` | `True` ↔ verdict == OVERFIT_RISK |
| 2 | `overfit_reason` | `str \| None` | 한국어 사유 ("훈련구간에서만 좋고 검증구간에서 성과 저하" + gap 수치) |
| 3 | `train_validation_gap` | `float \| None` | 정규화 gap 또는 None (데이터 부족) |
| 4 | `walk_forward_verdict` | `str \| None` | 3-04 verdict (HEALTHY/OVERFIT_RISK/INSUFFICIENT_DATA) |
| 5 | `recommendation_action` | `OverfitAction` | KEEP/HOLD/WATCHLIST/EXCLUDE |
| 6 | `operator_note` | `str \| None` | 운영자 안내 ("실제 Paper 운용 전 재검증 필요" 등) |

추가 식별 + invariant 필드:
- `strategy` / `symbol` / `params` / `overfit_verdict` (Verdict enum)
- `is_order_signal=False` / `auto_apply_allowed=False` /
  `is_live_authorization=False` / `auto_disable=False`

## 7. `apply_overfit_filter()` — 4-02 위에 필터 적용

```python
from app.agents.strategy_combination_recommender import build_combination_recommendation
from app.agents.overfit_warning_agent import (
    build_overfit_warning_report, apply_overfit_filter,
)

# 1) 4-02 추천 (이미 OVERFIT_RISK status 는 EXCLUDE 처리).
combo = build_combination_recommendation(operator_report=report)

# 2) 4-03 경고 — train/val gap 기반 SUSPECT 도 식별.
warnings = build_overfit_warning_report(operator_report=report)

# 3) 필터 적용 — recommended_combo 에서 OVERFIT_RISK/SUSPECT demote.
filtered = apply_overfit_filter(combo, warnings)
# filtered.recommended_combo 에 OVERFIT_RISK 0건 (테스트로 lock).
# filtered.metadata["overfit_filter_applied"] = True.
```

`demote_to_watchlist=True`:
- OVERFIT_RISK → `held` (watchlist 의미) 로 이동.
- `demote_to_watchlist=False` (default): → `excluded` 로 이동.

원본 `recommendation` 객체 *변경 0건* — 새 `StrategyCombinationRecommendation` 반환.

## 8. Overall 상태

| 상태 | 조건 |
|---|---|
| `HAS_RECOMMENDATIONS` | healthy_count >= 1 (필터 후 recommended 1건 이상) |
| `ALL_HOLD` | healthy_count == 0 AND (overfit + suspect + insufficient) > 0 |
| `NO_CANDIDATES_TODAY` | warnings 0건 |

## 9. 절대 invariant (테스트로 lock)

| 항목 | 강제 방식 |
|---|---|
| `OverfitWarning.is_order_signal=False` | `__post_init__` ValueError |
| `OverfitWarning.auto_apply_allowed=False` | `__post_init__` ValueError |
| `OverfitWarning.is_live_authorization=False` | `__post_init__` ValueError |
| `OverfitWarning.auto_disable=False` | `__post_init__` ValueError |
| `OverfitWarningReport` 동일 4 invariant | `__post_init__` ValueError |
| `OverfitAction` 에 `BUY`/`SELL`/`PLACE_ORDER`/`EXECUTE` 값 0개 | `test_overfit_action_has_no_order_direction` |
| broker / OrderExecutor / route_order import 0건 | 정적 grep |
| 외부 HTTP / AI SDK import 0건 | 정적 grep |
| `app.core.config.get_settings` import 0건 | 정적 grep |
| `settings.enable_*_trading =` mutate 0건 | 정적 grep |
| **전략 자동 비활성** 패턴 0건 (`strategy.enabled=False` / `.disable_strategy(` / ...) | `test_no_strategy_auto_disable` |
| schema 에 API key / Secret / 계좌번호 필드 0건 | `test_schema_has_no_secret_fields` |
| Agent output JSON 에 "BUY"/"SELL"/"Place Order"/"지금 매수"/"전략 자동 비활성" 0건 | 통합 테스트 |

## 10. Agent 호환성 (#51 Agent Architecture)

`OverfitWarningAgent` 는 `AgentBase` 호환:
- `metadata.role = AgentRole.RISK_AUDITOR` (위험 감사 역할 — #54 risk_auditor 와
  동일 카테고리이지만 *과최적화 특화*).
- `metadata.can_execute_order = False` (영구).
- `run(context) → AgentOutput(decision=WARN if overfit_count>0 else REPORT, ...)`.
- `AgentOutput.is_order_intent=False` / `can_execute_order=False` (AgentBase 가드 상속).

## 11. CLAUDE.md 절대 원칙 준수

- ✅ `RiskManager → PermissionGate → OrderExecutor` 흐름 *변경 0건*.
- ✅ 본 모듈은 *read-only* — broker / DB / 외부 API 호출 0건.
- ✅ `is_order_signal=False` 영구 — 본 경고 결과로 직접 주문 생성 *불가능*.
- ✅ 전략 자동 비활성 *불가능* — `auto_disable=False` 영구.
- ✅ 운영 모드 / 안전 flag default 변경 0건.
- ✅ LLM 호출 0건 — 본 모듈은 *결정론적 휴리스틱*.

## 12. 운영자 후속 행동

1. 본 모듈 출력 (`OverfitWarningReport`) 검토.
2. `overfit_count > 0` 이면:
   - Strategy Researcher Agent (#55) 리포트로 추가 분석.
   - 별도 PR 로 파라미터 단순화 또는 백테스트 기간 확장.
   - 재 walk-forward → SUSPECT 도 해소되면 추천 풀에 재진입.
3. 본 모듈은 자동으로 전략을 비활성 / 삭제하지 *않는다* — 운영자 수동 PR.

## 13. 테스트

```bash
python -m pytest backend/tests/test_overfit_warning_agent.py -q
python -m pytest backend/tests/test_repository_hygiene.py -q
python scripts/security_scan.py
```

## 14. Schema 진화 정책

- `schema_version` 은 *명시 옵트인 PR* 로만 bump.
- `OverfitAction` enum 에 새 값 추가 시 본 문서 + `TestEnums` 동시 갱신 필요.
- `OverfitVerdict` 에 *주문 방향* 값 (BUY/SELL/HOLD) 영구 금지.
- invariant 필드 (`is_order_signal` / `auto_apply_allowed` /
  `is_live_authorization` / `auto_disable`) *추가 / 변경 / 삭제 영구 금지*.
