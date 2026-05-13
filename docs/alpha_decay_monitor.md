# Alpha Decay Monitor — 체크리스트 #77

> 전략별 알파 감쇠를 추적하는 *read-only* 분석 게이트.
> **자동 비활성 / 삭제 / promotion 변경 *절대 금지***. 결과는 운영자 /
> Strategy Researcher Agent(#55) / Promotion Gate(#27) 참고용.

---

## 1. 목적

- 한때 잘 되던 단타 전략이 *계속 통하지 않을 수* 있다.
- baseline (검증 단계 통과 시점) 대비 최근 운용 성과 변화를 *정량 점수화*.
- **자동 비활성 후보** 표시만 — 실제 비활성/삭제는 운영자 수동 승인 + 별도 PR.
- 단기 부진과 구조적 성능저하를 *분류*해 운영자가 의사 결정 가능하게 한다.

---

## 2. Alpha Decay 정의

> *알파 감쇠 (Alpha Decay)* = 검증 시점에 통하던 전략 신호가 최근 운용 구간에서
> 약해지는 현상.

원인 가능성 (advisory):
- 시장 regime 변화 (trend → range_bound 등).
- 본 전략이 의존하던 시장 microstructure 변화.
- 데이터 품질 저하 / freshness drift.
- 과최적화로 인한 백테스트-실거래 괴리.
- 단기 부진 (운수)만으로도 발생 가능 — 구조적 감쇠와 *반드시 구분*.

---

## 3. 지표

본 게이트가 평가하는 6개 핵심 지표:

| 지표 | baseline → recent 비교 | 가중치 (default) |
|---|---|---|
| `expectancy` 하락 | drop ratio | 25 |
| `expectancy` 양수→음수 flip | flip 발생 시 | **+25** (추가) |
| `profit_factor` 하락 | drop ratio | 20 |
| `profit_factor < 1.2` | 임계 미만 | **+15** (추가) |
| `win_rate` 하락 | drop ratio | 10 |
| `max_drawdown` 악화 (baseline 1.5배 이상) | 임계 통과 | 15 |
| `max_consecutive_losses` 2배 이상 | 임계 통과 | 10 |
| `data_quality_score` 낮음 (<75) | 임계 통과 | 15 |
| market regime 변경 | bool | 5 |

score = 위 가중치 누적 (0~100 clamp).

`compute_alpha_decay_score(baseline, recent)` 가 `(score, signals)` 반환.
`signals` 는 각 가중치를 트리거한 advisory tag 리스트 (예: `expectancy_drop` /
`pf_below_min` / `mdd_worsen` / `consec_losses_increase` / `winrate_drop` /
`data_quality_issue` / `regime_change`).

---

## 4. 단기 부진 vs 구조적 성능저하 (`AlphaDecayKind`)

`_classify_kind()` 가 6개 분류 중 하나 반환:

| Kind | 조건 |
|---|---|
| `INSUFFICIENT_DATA` | recent trade_count < min_recent_trades (default 20). score 미산정. |
| `DATA_QUALITY_ISSUE` | `data_quality_score < 60` (block-level). 결과 신뢰도 자체 저하. |
| `REGIME_MISMATCH` | market regime 변경 + 핵심 지표 악화 ≤ 1개. → 단기 부진일 가능성. |
| `STRUCTURAL_DECAY` | 핵심 지표 ≥ **3개** 동시 악화. → 구조적 감쇠 의심. |
| `SHORT_TERM_DRAWDOWN` | 핵심 지표 1~2개만 악화. → 단기 부진. |
| `NONE` | 악화 신호 0건 (HEALTHY). |

핵심 지표는 `regime_change` / `data_quality_issue` 를 *제외*한 나머지 (성과
지표 위주).

---

## 5. Score 해석 (`AlphaDecayStatus`)

| Score | Status | 색상 | 의미 |
|---|---|---|---|
| **−1** | `INSUFFICIENT_DATA` | 회색 | 표본 부족, 측정 불가 |
| 0~24 | `HEALTHY` | 초록 | 정상 |
| 25~49 | `WATCH` | 노랑 | 주의 — 다음 구간 회복 여부 확인 |
| 50~74 | `DECAY_WARNING` | 주황 | 감쇠 경고 — kind에 따라 대응 |
| 75~100 | `DISABLE_CANDIDATE` | 빨강 | *비활성 후보* (자동 비활성 X) |

---

## 6. 운영 정책

### 6.1 절대 금지 (코드 단 invariant)

- **자동 비활성화 금지** — `AlphaDecayResult.auto_disable=False` 불변
  (dataclass `__post_init__` ValueError).
- **자동 적용 금지** — `auto_apply_allowed=False` 불변.
- **주문 신호 생성 금지** — `is_order_signal=False` 불변.
- 본 모듈은 `PromotionGate` / `StrategyBase` / broker / OrderExecutor / `route_order`
  중 어느 것도 호출하지 *않는다* (정적 grep 가드).
- DB write 0건 — read-only evaluator.

### 6.2 운영자 검토 절차

1. UI / API로 strategy별 평가 결과 확인.
2. `DECAY_WARNING` / `DISABLE_CANDIDATE` 인 경우:
   - **Strategy Researcher Agent(#55)** 분석 실행.
   - kind이 `REGIME_MISMATCH` 면 regime 정상화 후 회복 여부 확인.
   - kind이 `STRUCTURAL_DECAY` 면 backtest 재검증 + parameter 재튜닝 검토.
3. 비활성 결정 시:
   - 별도 PR로 전략 코드 / 설정 변경.
   - 운영자 명시 승인 (PR review).
   - Strategy Promotion Gate(#27) 단계 재진입 검토.

### 6.3 Strategy Researcher / Promotion Gate 연결

- 본 게이트의 결과는 [`strategy_researcher_agent.md`](strategy_researcher_agent.md) (#55)
  Agent가 *추가 분석*용으로 참조 가능.
- 비활성 결정 후 재투입은 [`strategy_promotion_gate.md`](strategy_promotion_gate.md) (#27)
  + [`paper_gate_policy.md`](paper_gate_policy.md) (#72) 단계 재통과 필요.

---

## 7. 한계 (운영자 인지 필요)

본 게이트는 *판단 보조 자료*일 뿐 절대적 기준이 아니다. 다음 한계를 인지하고
사용한다:

- **최근 데이터 부족** — 표본이 적으면 INSUFFICIENT_DATA로 정확한 판정 불가.
- **장세 변화** — 시장 regime 변경 시 일시적 부진이 구조적으로 보일 수 있다.
- **데이터 품질 문제** — feed / freshness / 일부 결측이 점수에 잡음 추가.
- **과최적화 (Overfitting)** — 백테스트 baseline 자체가 비현실적이면 *항상*
  decay로 보일 수 있다.
- **거래 빈도 변화** — 같은 전략이 시장 변동성에 따라 trade_count 차이가 크면
  recent 표본 충분성 판단이 달라진다.
- **portfolio 효과 미반영** — 본 게이트는 *전략별* 평가. 포트폴리오 단위
  상관관계 / 분산 효과는 별도 분석 필요.

---

## 8. API

### `POST /api/governance/alpha-decay/evaluate`

read-only — 안전 플래그 / DB write / broker 호출 0건.

응답 invariant:
- `is_order_signal=false`
- `auto_disable=false`
- `auto_apply_allowed=false`
- `live_flag_changed=false`
- `mode_changed=false`

요청 body 예:
```json
{
  "strategy_name": "sma_cross",
  "baseline": {
    "trade_count": 100, "expectancy": 300, "profit_factor": 1.5,
    "win_rate": 0.55, "max_drawdown": 200000, "max_consecutive_losses": 3
  },
  "recent": {
    "trade_count": 50, "expectancy": -100, "profit_factor": 0.9,
    "win_rate": 0.40, "max_drawdown": 600000, "max_consecutive_losses": 8
  },
  "baseline_regime": "trend_up",
  "recent_regime":   "range_bound",
  "recent_data_quality_score": 78.5
}
```

---

## 9. UI

`frontend/src/components/tabs/AlphaDecayCard.jsx`:

- 표시: 전략명 / status badge / "비활성 후보" 보조 배지 (DISABLE_CANDIDATE에만) /
  score / kind / recent trade count / baseline vs recent 메트릭 Δ /
  악화 신호 chip / 권장 조치 / cautions.
- 위험 문구 *항상* 노출: "DISABLE_CANDIDATE는 *비활성 후보* 표시이지 자동
  비활성이 아닙니다. 전략 삭제/중단은 *운영자 수동 승인*이 필요합니다."
- **전략 비활성화 / 삭제 / 파라미터 적용 / promotion 변경 / AI 자동매매 활성화 /
  Place Order / 주문 실행 라벨 버튼 0개** (테스트로 lock).
- BUY/SELL/HOLD/긴급정지 토글 문구 0건.
- 평가 버튼은 "알파 감쇠 평가" 한 개만.

---

## 10. 절대 원칙 — 본 모듈 강제

`tests/test_alpha_decay.py`의 정적 grep 가드:

1. broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
   `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
2. `broker.place_order(` / `route_order(` / `OrderExecutor(` /
   `submit_candidate(` / `AiClient(` 호출 0건.
3. DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건.
4. `settings.enable_*_trading =` mutate 0건.
5. `from app.core.config import` / `get_settings(` 호출 0건.
6. `.save_params(` / `.apply_params(` / `.update_params(` / `strategy.enabled = False` /
   `PromotionGate(` / `evaluate_promotion(` / `.set_emergency_stop(` 호출 0건.
7. `AlphaDecayResult.is_order_signal=True` 생성 불가 (ValueError).
8. `AlphaDecayResult.auto_disable=True` 생성 불가.
9. `AlphaDecayResult.auto_apply_allowed=True` 생성 불가.
10. UI에 "전략 비활성화" / "전략 삭제" / "Disable Strategy" / "Apply Parameters" /
    "파라미터 적용" / "promotion 변경" / "AI 자동매매 활성화" / "ENABLE_AI_EXECUTION" /
    "Place Order" / "주문 실행" 라벨 버튼 0개.
11. UI / 응답에 Secret 패턴 0건.

---

## 11. 후속 backlog

- regime-aware alpha decay — regime별 baseline / recent 분리 비교
- portfolio-level decay — 여러 전략의 상관관계 / 분산 효과
- 자동 collector — BacktestRun / OrderAuditLog 에서 baseline / recent 자동 추출
- decay history 추적 — 시계열 알파 감쇠 추세
- Strategy Researcher Agent(#55) 결과 carry — kind별 권장 조치 자동 매핑
- Notification 연계 — DISABLE_CANDIDATE 진입 시 운영자 알림
- Bayesian 신뢰 구간 — score에 신뢰 구간 부여
- 시장 regime 자동 분류 — operator note → regime 매핑 자동화

---

## 12. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/strategy_researcher_agent.md`](strategy_researcher_agent.md) — #55 Strategy Researcher
- [`docs/strategy_promotion_gate.md`](strategy_promotion_gate.md) — #27 Promotion Gate
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — #72 Paper Gate
- [`docs/ai_assist_gate.md`](ai_assist_gate.md) — #74 AI Assist Gate
- [`docs/ai_execution_gate.md`](ai_execution_gate.md) — #75 AI Execution Activation Gate
- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격
- [`docs/data_quality_report.md`](data_quality_report.md) — #21 data quality (본 모듈이 score carry)
- [`docs/market_regime_filter.md`](market_regime_filter.md) — 시장 regime 분류 (본 모듈이 사용)
