# Step 4-04 — Market Regime → 장세별 전략 선택

> 본 문서는 *연구 / 검증* 파이프라인의 정책 정의입니다. **투자 조언이 아닙니다.**
> 본 장세 분류 + 필터는 *advisory* 입니다 — 자동 paper trader 시작 / 자동
> 실거래 활성화를 수행하지 **않습니다**.

## 1. 목적

현재 장세를 7개 라벨 (TREND_UP / TREND_DOWN / SIDEWAYS / HIGH_VOLATILITY /
LOW_LIQUIDITY / CHOPPY / UNKNOWN) 로 *advisory* 분류하고, 4-02 의
`StrategyCombinationRecommendation` 위에 *장세별 전략 정책* 을 적용해 부적합
전략을 `recommended_combo` 에서 demote 한다.

핵심 의도:
- **장세 인식형 추천** — 횡보에서 breakout 전략, 변동성 폭증에서 stop-loss 민감
  전략, 거래대금 부족에서 모든 신규 진입을 *자동으로 보류*.
- **결정론적 휴리스틱** — LLM 호출 / 외부 API 의존 0건.
- **과최적화 우선** — 4-03 OVERFIT_RISK 차단은 본 필터로 *원복되지 않는다*.

## 2. 기존 `app.agents.market_regime` 과 별개 모듈

| 항목 | 기존 `market_regime.py` (#225) | 본 PR `market_regime_agent.py` (4-04) |
|---|---|---|
| 용도 | 실시간 단일 주문 결정 filter | 일일 advisory 전략 조합 추천 |
| 사용 시점 | route_order / RiskManager 호출 시점 | 운영자 / Agent 의 일일 검토 |
| Regime 수 | 10 (GAP_DAY / NEWS_DRIVEN / RISK_OFF / OPENING_CHAOS / LATE_DAY_FADE 포함) | 7 (user spec — SIDEWAYS / UNKNOWN 포함) |
| AgentBase 호환 | ❌ | ✅ |
| Import 의존 | 본 모듈은 import 안 함 | 기존 모듈을 import 안 함 |

두 모듈은 *독립* — 운영자 혼선 방지 + 책임 분리.

## 3. 7 장세 (`MarketRegime` enum)

| Regime | 운영자 한 줄 (`_REGIME_LABEL_KO`) |
|---|---|
| `TREND_UP` | "상승 추세 — momentum / breakout 우선" |
| `TREND_DOWN` | "하락 추세 — 신규 진입 축소, 손실 제한 우선" |
| `SIDEWAYS` | "횡보 — mean-reversion 우선, breakout 보류" |
| `HIGH_VOLATILITY` | "변동성 급증 — 신규 진입 축소, size 축소 권고" |
| `LOW_LIQUIDITY` | "거래대금 부족 — 대부분 신규 진입 보류, 슬리피지 위험" |
| `CHOPPY` | "무방향 + 잦은 반전 — 추세추종 보류, mean-reversion 만 제한 검토" |
| `UNKNOWN` | "장세 분류 불가 — Paper 자동 시작 금지, WATCH_ONLY" |

**BUY/SELL/PLACE_ORDER 값 0개** — 본 enum 은 advisory 분류만 (테스트 lock).

## 4. 장세별 전략 정책 (`REGIME_STRATEGY_POLICY`)

| Regime | Preferred (점수 우대) | Watchlist (HOLD 권고) | Blocked (EXCLUDE 권고) |
|---|---|---|---|
| TREND_UP | sma_crossover, volume_breakout, orb_vwap, pullback_rebreak | (없음) | (없음) |
| TREND_DOWN | (없음) | rsi_reversion | volume_breakout, orb_vwap |
| SIDEWAYS | rsi_reversion, vwap_strategy | sma_crossover, pullback_rebreak | volume_breakout, orb_vwap |
| HIGH_VOLATILITY | (없음) | rsi_reversion, vwap_strategy, sma_crossover, pullback_rebreak | orb_vwap, volume_breakout |
| LOW_LIQUIDITY | (없음) | rsi_reversion, vwap_strategy | orb_vwap, volume_breakout, sma_crossover, pullback_rebreak |
| CHOPPY | (없음) | rsi_reversion, vwap_strategy | sma_crossover, pullback_rebreak, orb_vwap, volume_breakout |
| UNKNOWN | (없음) | 모든 등록 전략 | (없음 — 보수적 보류) |

본 정책 매트릭스는 `REGIME_STRATEGY_POLICY` (dict) 로 노출 — 운영자가 별도
PR 로 조정 가능 (테스트로 lock).

## 5. 분류기 (`classify_market_regime`)

```python
from app.agents.market_regime_agent import (
    MarketStateInput, classify_market_regime,
)

report = classify_market_regime(MarketStateInput(
    trend_direction="UP",     # "UP"/"DOWN"/"SIDEWAYS"/None
    volatility_pct=0.025,     # daily ATR / close
    liquidity_score=0.7,      # 0~1
    momentum_score=0.4,       # -1..1
    choppiness_index=0.45,    # 0~1
))
print(report.regime)  # MarketRegime.TREND_UP
print(report.allowed_strategies)
print(report.blocked_strategies)
print(report.watchlist_strategies)
print(report.operator_note)
```

**분류 순서 (가장 보수적부터)** — 다음 순서로 평가, 첫 매칭에서 즉시 반환:
1. `liquidity_score < 0.30` → `LOW_LIQUIDITY`
2. `volatility_pct > 0.04` → `HIGH_VOLATILITY`
3. `choppiness_index > 0.60` → `CHOPPY`
4. `trend_direction == "UP"` → `TREND_UP`
5. `trend_direction == "DOWN"` → `TREND_DOWN`
6. `trend_direction == "SIDEWAYS"` → `SIDEWAYS`
7. 그 외 → `UNKNOWN`

임계는 `MarketStateInput.{high_volatility_threshold, low_liquidity_threshold,
choppiness_threshold}` 로 runtime override 가능.

## 6. `apply_regime_filter()` — 4-02 위에 필터 적용

```python
from app.agents.strategy_combination_recommender import build_combination_recommendation
from app.agents.overfit_warning_agent import (
    build_overfit_warning_report, apply_overfit_filter,
)
from app.agents.market_regime_agent import (
    classify_market_regime, MarketStateInput, apply_regime_filter,
)

# 1) 4-02 추천 빌드.
combo = build_combination_recommendation(operator_report=operator_report)

# 2) 4-03 OVERFIT 필터 (먼저 — 우선순위 높음).
warnings = build_overfit_warning_report(operator_report=operator_report)
after_overfit = apply_overfit_filter(combo, warnings)

# 3) 4-04 장세 필터.
regime = classify_market_regime(MarketStateInput(trend_direction="UP",
                                                  volatility_pct=0.02))
final = apply_regime_filter(after_overfit, regime)
# final.regime_context 에 장세 정보 carry.
# final.recommended_combo 에 OVERFIT + regime-blocked 전략 0건.
```

`apply_regime_filter` 동작:
- `regime.blocked` 전략 → `recommended_combo` 에서 제거 → `excluded` 로 이동.
- `regime.watchlist` 전략 → `recommended_combo` 에서 제거 → `held` 로 이동.
- `regime.preferred` 전략 → 유지 (본 PR 시점 점수 가산 미적용).
- `UNKNOWN` 장세: *모든* recommended → `held` 로 이동 → `ALL_HOLD` (WATCH_ONLY).

## 7. 추천 결과 추가 필드 (`regime_context`)

`StrategyCombinationRecommendation.regime_context: dict | None`:

```jsonc
{
  "market_regime":               "TREND_UP",
  "regime_confidence":           0.75,
  "regime_reasons":              ["trend_direction=UP"],
  "regime_risk_flags":           [],
  "regime_allowed_strategies":   ["sma_crossover", "volume_breakout", ...],
  "regime_blocked_strategies":   [],
  "regime_watchlist_strategies": [],
  "regime_operator_note":        "상승 추세 — momentum / breakout 계열 우선 검토."
}
```

본 필드는 **default None** — 4-04 필터를 적용하지 않은 caller 는 영향 없음
(backwards compat 테스트 lock).

## 8. 과최적화 우선순위 (4-03 → 4-04 순서)

**과최적화 차단이 *항상* 우선**:
- `apply_overfit_filter` 가 OVERFIT_RISK 전략을 `recommended_combo` 에서 이미
  제거 → `excluded` 로 이동.
- `apply_regime_filter` 는 *현재 `recommended_combo`* 만 처리 → OVERFIT_RISK 가
  TREND_UP 의 preferred 라도 *원복하지 않음*.
- 테스트 lock: `test_overfit_overrides_trend_up_recommendation` 가 강제.

권장 호출 순서: **4-02 (build) → 4-03 (overfit) → 4-04 (regime)** — 정책이 다른
가드보다 우선시되는 순서.

## 9. 필수 동작 (테스트로 lock)

| 동작 | 테스트 |
|---|---|
| LOW_LIQUIDITY → recommended 0개 또는 watchlist | `test_low_liquidity_blocks_most_strategies` |
| HIGH_VOLATILITY → risk_summary 에 변동성 경고 | `test_high_volatility_carries_warning` |
| SIDEWAYS → breakout 전략 점수 감점 (EXCLUDE) | `test_sideways_blocks_breakout_strategies` |
| TREND_UP → momentum/breakout 우선 | `test_trend_up_keeps_momentum_breakout` |
| UNKNOWN → ALL_HOLD (Paper 자동 시작 금지) | `test_unknown_demotes_all_to_held` |
| OVERFIT_RISK → regime 좋아도 추천 제외 유지 | `test_overfit_overrides_trend_up_recommendation` |
| Pre-market / Paper 후보 조건 우회 0건 | upstream filter 흐름 (4-02 + 4-03) 유지 — 본 PR 미수정 |

## 10. 절대 invariant (테스트로 lock)

| 항목 | 강제 |
|---|---|
| `MarketRegimeReport.is_order_signal=False` | `__post_init__` ValueError |
| `MarketRegimeReport.auto_apply_allowed=False` | `__post_init__` ValueError |
| `MarketRegimeReport.is_live_authorization=False` | `__post_init__` ValueError |
| `MarketRegimeReport.auto_start_paper_trader=False` | `__post_init__` ValueError |
| `MarketRegime` enum 에 BUY/SELL/PLACE_ORDER/EXECUTE 값 0개 | `test_regime_enum_has_no_order_direction` |
| 7 장세 정확히 존재 (TREND_UP/TREND_DOWN/SIDEWAYS/HIGH_VOLATILITY/LOW_LIQUIDITY/CHOPPY/UNKNOWN) | `test_seven_regimes_present` |
| `REGIME_STRATEGY_POLICY` 가 등록된 6 전략만 참조 | `test_policy_strategies_are_registered_only` |
| broker / OrderExecutor / route_order import 0건 | 정적 grep |
| 외부 HTTP / AI SDK import 0건 | 정적 grep |
| `app.core.config.get_settings` import 0건 | 정적 grep |
| `settings.enable_*_trading =` mutate 0건 | 정적 grep |
| schema 에 API key / Secret / 계좌번호 필드 0건 | `test_schema_has_no_secret_fields` |
| Agent output JSON 에 "BUY"/"SELL"/"Place Order"/"실거래 시작" 0건 | 통합 테스트 |
| `apply_regime_filter` 가 OVERFIT_RISK 를 원복하지 않음 | `test_overfit_overrides_trend_up_recommendation` |

## 11. Agent 호환성 (#51 Agent Architecture)

`MarketRegimeAgent` 는 `AgentBase` 호환:
- `metadata.role = AgentRole.OBSERVER`
- `metadata.can_execute_order = False` (영구)
- `run(context) → AgentOutput(decision=OBSERVE, summary, reasons, risk_flags,
  metadata=...)`.
- `AgentOutput.is_order_intent=False` / `can_execute_order=False` (AgentBase 가드 상속).

`context.extra["market_state"]` 에 `MarketStateInput` 주입 → 분류 수행.
`context.extra["recommendation"]` 에 `StrategyCombinationRecommendation` 주입 시
→ 필터까지 자동 적용 → `metadata["filtered_recommendation"]` 에 결과 carry.

## 12. CLAUDE.md 절대 원칙 준수

- ✅ `RiskManager → PermissionGate → OrderExecutor` 흐름 *변경 0건*.
- ✅ 본 모듈은 *read-only* — broker / DB / 외부 API 호출 0건.
- ✅ `is_order_signal=False` 영구 — 본 결과로 직접 주문 생성 *불가능*.
- ✅ Paper trader 자동 시작 *불가능* — `auto_start_paper_trader=False` 영구.
- ✅ 운영 모드 / 안전 flag default 변경 0건.
- ✅ LLM 호출 0건 — 결정론적 휴리스틱.

## 13. 테스트

```bash
python -m pytest backend/tests/test_market_regime_agent.py -q
python -m pytest backend/tests/test_strategy_combination_recommender.py -q  # 회귀 확인
python -m pytest backend/tests/test_repository_hygiene.py -q
python scripts/security_scan.py
```

## 14. Schema 진화 정책

- `MarketRegime` enum 에 새 장세 추가 시 본 문서 + `REGIME_STRATEGY_POLICY` +
  `test_seven_regimes_present` (또는 신규 lock 테스트) 동시 갱신 필요.
- 정책 매트릭스 (`REGIME_STRATEGY_POLICY`) 변경 시 별도 옵트인 PR + 백테스트
  근거 필요.
- invariant 필드 (`is_order_signal` / `auto_apply_allowed` /
  `is_live_authorization` / `auto_start_paper_trader`) *추가 / 변경 / 삭제 영구 금지*.
- `regime_context` 필드는 *항상* `dict | None` 으로 backwards compat 유지.
