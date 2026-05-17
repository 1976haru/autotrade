# Strategy Portfolio — 6 전략 후보 → 4 매매기법군 정합성 (#0-02)

> **본 문서는 전략 포트폴리오 *정의* 문서이며, 실전매매 *허가* 문서가
> 아닙니다.** 실전(Live) 자동매매는 [`docs/live_readiness_policy.md`](
> live_readiness_policy.md) (#0-01) 의 Live Gate / Canary 통과 전까지
> **차단** 됩니다.

---

## 0. 목적

코드에 등록된 **6개 전략 모듈** (검증 후보 풀) 을 운영자 / AI Agent 가 다루는
**4개 매매기법군** (운용 단위) 으로 매핑한다. 이를 통해:

- 운영자가 "오늘 어느 *기법군*을 쓸지" 결정 — 6개 ID 를 다 외울 필요 없음.
- AI Agent 가 장세별 (4-04) 로 *기법군 단위* 추천 — 다양성 + 해석 가능성 향상.
- 백테스트 / Walk-forward / Stress Test / Paper 후보 통합 (3-02 ~ 3-08) 결과
  검토 시 *기법군 분포* 로 위험 집중도 확인 가능.

---

## 1. 현재 코드의 6개 전략 후보 (검증 후보 풀)

`backend/app/strategies/registry_metadata.py` 의 `STRATEGY_METADATA_REGISTRY` 가
**단일 진실** :

| # | `strategy_id` | 한글 표시명 (`display_name`) | risk_level | 본 PR 시점 백테스트 가용성 |
|---|---|---|---|---|
| 1 | `sma_crossover` | 단기/장기 이동평균 교차 | MEDIUM | ✅ Paper 권장 |
| 2 | `rsi_reversion` | RSI 과매도/과매수 회복 | MEDIUM | ✅ Paper 권장 |
| 3 | `vwap_strategy` | VWAP 평균 회귀 | MEDIUM | ✅ Paper 권장 |
| 4 | `orb_vwap` | ORB + VWAP 돌파 | HIGH | ✅ Paper 권장 |
| 5 | `volume_breakout` | 거래량 급증 돌파 | HIGH | ✅ Paper 권장 |
| 6 | `pullback_rebreak` | 눌림목 재돌파 | MEDIUM | ✅ Paper 권장 |

**가짜 전략명 추가 영구 금지** (`골든브릿지` / `100% 승률` / `magic strategy` /
`guaranteed` 등) — `test_strategy_registry_metadata.py` 가 정적 grep 으로 차단
(#81~#83).

`live_trading_available=False` — 6개 *모두* 본 PR 시점 KIS LIVE 미구현 (영구
False).

---

## 2. 최종 4개 매매기법군 (운용 단위)

본 프로젝트의 *최종 운용 단위* — Paper / Live 단계에서 운영자 / AI Agent 가
참조하는 4개 카테고리.

| # | 매매기법군 | 포함 전략 모듈 | 핵심 컨셉 |
|---|---|---|---|
| **A** | **추세추종 / Momentum** | `sma_crossover` + `volume_breakout` | 추세 방향으로 진입, 신호 강도 / 거래량으로 확인 |
| **B** | **평균회귀 / Reversion** | `rsi_reversion` | 과매도/과매수 후 평균으로 회귀 — 횡보 장에 강점 |
| **C** | **VWAP / 장중 기준가** | `vwap_strategy` | 거래량 가중 평균에서 이격 후 회귀 — 장중 평균을 기준 |
| **D** | **장초반 돌파 / Pullback-Rebreak** | `orb_vwap` + `pullback_rebreak` | 장초반 range 돌파 또는 1차 돌파 후 눌림에서 재돌파 |

각 매매기법군은 **백테스트 / Walk-forward / Stress Test / Paper 후보 통합** (3-02
~ 3-08) 의 *동일한 평가 체계* 를 거치며, 통과한 (strategy, symbol, params) 조합만
Paper 운용 후보가 됩니다.

---

## 3. 운용 정책 (매매기법군 사용 규칙)

| 정책 | 내용 |
|---|---|
| **검증 풀 → 운용 풀 압축** | 6개 전략 모듈 모두 *검증 후보* — 실제 Paper 운용에 들어가는 것은 1~4개 *조합* |
| **AI Agent 추천 단위** | 4개 매매기법군 *조합 단위* — 4-02 `StrategyCombinationRecommender` + 4-04 장세별 필터 적용 |
| **다양성 권장** | `recommended_combo` 선정 시 *서로 다른 기법군* 우선 (4-02 다양성 휴리스틱) — 한 기법군 집중 회피 |
| **장세 매칭** | 4-04 `apply_regime_filter` 가 TREND_UP → A / D, SIDEWAYS → B / C, HIGH_VOLATILITY → 보류 등 정책 매트릭스 적용 |
| **과최적화 우선 차단** | 4-03 `apply_overfit_filter` 가 OVERFIT_RISK 전략을 우선 제외 — 기법군이 좋아도 원복 없음 |
| **Paper 운용 가능** | 4개 군 *모두* `paper_trading_available=True` — `KIS_IS_PAPER=true` + PaperTrader (#42) 강제 |
| **Live 운용 가능 여부** | 4개 군 *모두* 본 PR 시점 **불가** — `live_trading_available=False` 영구. Live Gate / Canary 통과 + `ENABLE_LIVE_TRADING=true` 별도 PR 필요 ([`live_readiness_policy.md`](live_readiness_policy.md)) |

---

## 4. 매매기법군 매트릭스 (10 컬럼 lock)

본 표는 4 매매기법군의 *단일 진실* — 변경은 본 문서 동시 갱신 PR 필요.

### A. 추세추종 / Momentum

| 항목 | 값 |
|---|---|
| 최종 매매기법명 | 추세추종 / Momentum (Trend-Following / Momentum) |
| 포함 전략 모듈 | `sma_crossover`, `volume_breakout` |
| 적용 장세 | **TREND_UP** (강) / TREND_DOWN (방어적, 신규 진입 축소) |
| 주요 신호 | 단기 MA > 장기 MA crossover + 거래량 급증 확인 |
| 주요 위험 | 횡보장 whipsaw, 추세 반전 시 손실 streak, 변동성 급증 시 stop-loss 빈발 |
| 사용 조건 | TREND_UP 장세 + Walk-forward HEALTHY + Stress test PASS + 거래량 > 임계 |
| 제외 조건 | OVERFIT_RISK / 거래량 부족 (LOW_LIQUIDITY) / CHOPPY / SIDEWAYS 에서 차단 |
| AI Agent 판단 기준 | 4-02 RECOMMEND + 4-04 TREND_UP preferred — `regime_context.market_regime=="TREND_UP"` 시 우선 |
| Paper 운용 가능 | ✅ (`paper_trading_available=True`) |
| Live 운용 가능 | ❌ — Live Gate / Canary 미통과 시 영구 차단 (`live_trading_available=False`) |

### B. 평균회귀 / Reversion

| 항목 | 값 |
|---|---|
| 최종 매매기법명 | 평균회귀 / Reversion (Mean Reversion) |
| 포함 전략 모듈 | `rsi_reversion` |
| 적용 장세 | **SIDEWAYS** (강) / CHOPPY (제한 검토) / HIGH_VOLATILITY (보류) |
| 주요 신호 | RSI 과매도/과매수 이탈 후 평균 복귀 — 횡보 장에서 진입 |
| 주요 위험 | TREND 장에서 잘못된 진입 → 손실 확대, 변동성 급증 시 추세 전환 미감지 |
| 사용 조건 | SIDEWAYS 장세 + RSI 임계 위반 + Walk-forward HEALTHY + Stress test PASS |
| 제외 조건 | TREND_UP / TREND_DOWN 에서 watchlist (HOLD) — 4-04 정책 |
| AI Agent 판단 기준 | 4-02 RECOMMEND + 4-04 SIDEWAYS preferred — `regime_context.market_regime=="SIDEWAYS"` 시 우선 |
| Paper 운용 가능 | ✅ |
| Live 운용 가능 | ❌ — Live Gate 미통과 |

### C. VWAP / 장중 기준가

| 항목 | 값 |
|---|---|
| 최종 매매기법명 | VWAP / 장중 기준가 (VWAP Mean Reversion) |
| 포함 전략 모듈 | `vwap_strategy` |
| 적용 장세 | **SIDEWAYS** (강) / CHOPPY (제한) — 평균 회귀 흐름 |
| 주요 신호 | 가격이 VWAP 에서 이격 후 회귀 — 거래량 가중 평균 기준 |
| 주요 위험 | 추세 장에서 VWAP 이격 지속, 거래량 부족 시 VWAP 신뢰성 저하 |
| 사용 조건 | SIDEWAYS 장세 + 정상 거래량 + VWAP 이격 임계 + Walk-forward HEALTHY |
| 제외 조건 | LOW_LIQUIDITY (거래량 부족 → VWAP 부정확) / TREND_UP / TREND_DOWN |
| AI Agent 판단 기준 | 4-02 RECOMMEND + 4-04 SIDEWAYS preferred — Reversion 군과 다양성 보완 (서로 다른 종목 권장) |
| Paper 운용 가능 | ✅ |
| Live 운용 가능 | ❌ — Live Gate 미통과 |

### D. 장초반 돌파 / Pullback-Rebreak

| 항목 | 값 |
|---|---|
| 최종 매매기법명 | 장초반 돌파 / Pullback-Rebreak (Opening Range Breakout / Pullback Continuation) |
| 포함 전략 모듈 | `orb_vwap`, `pullback_rebreak` |
| 적용 장세 | **TREND_UP** (강) — 추세와 결합 시 최강. SIDEWAYS / CHOPPY 에서 차단 |
| 주요 신호 | 장초반 range 돌파 (orb) 또는 1차 돌파 후 눌림에서 재돌파 (pullback) — VWAP 동행 확인 |
| 주요 위험 | False breakout, 변동성 급증 시 stop-loss 민감, 거래량 부족 시 slippage 큼 |
| 사용 조건 | TREND_UP + 정상 거래량 + 장초반 / 1차 돌파 후 timing + Walk-forward HEALTHY |
| 제외 조건 | SIDEWAYS / CHOPPY / HIGH_VOLATILITY / LOW_LIQUIDITY 에서 차단 — 4-04 정책 |
| AI Agent 판단 기준 | 4-02 RECOMMEND + 4-04 TREND_UP preferred — 추세추종(A) 군과 다양성 보완 |
| Paper 운용 가능 | ✅ (risk_level=HIGH — `orb_vwap` / `volume_breakout` 은 sizing 신중) |
| Live 운용 가능 | ❌ — Live Gate 미통과. HIGH_VOLATILITY 에서 *영구 차단* (stop-loss 민감) |

---

## 5. AI Agent 가 매매기법군을 다루는 방식

본 PR 시점 운영 흐름 (4-01 ~ 4-04 통합):

1. **3-02 ~ 3-08**: 6개 전략 후보 풀 → 백테스트 / Walk-forward / Stress Test
   / Paper 후보 통합 → `StrategyAgentInput` (4-01) 표준 입력 생성.
2. **4-02 `StrategyCombinationRecommender`**: `RECOMMEND` / `HOLD` /
   `EXCLUDE` 분류 + 다양성 (strategy + symbol) 우선 조합 선정.
3. **4-03 `apply_overfit_filter`**: OVERFIT_RISK 전략 제외 — 기법군이 좋아도
   *원복 없음*.
4. **4-04 `apply_regime_filter`**: 장세별 정책 적용 — TREND_UP 에서 A/D 우대,
   SIDEWAYS 에서 B/C 우대, LOW_LIQUIDITY / UNKNOWN 에서 보수적 보류.
5. **AI Paper 자동매매**: AI Agent 가 매수 / 매도 / 보류 / 청산 *판단* 수행 —
   하지만 broker 는 `MockBroker` 또는 `KIS_IS_PAPER=true` 만 호출
   (`PaperTrader.assert_paper_broker` 강제).

**AI Live 실전은 Live Gate / Canary 통과 전까지 *금지*** —
[`docs/live_readiness_policy.md`](live_readiness_policy.md) §4 의 14개 조건 +
4개 게이트 + 운영자 명시 옵트인 모두 PASS 필수.

---

## 6. Paper / Live 단계 구분 표

| 단계 | 4 매매기법군 사용 가능 | broker | 안전 flag |
|---|---|---|---|
| **AI Paper Auto Trading** (단계 1, 현재) | ✅ 모두 (A/B/C/D) | MockBroker / KIS Paper (`KIS_IS_PAPER=true`) | `ENABLE_LIVE_TRADING=false` 영구 |
| **AI Live Manual Approval** (단계 2, 미진입) | ⏳ Live Gate 통과 후 운영자 *수동 승인* 후만 | KIS LIVE (수동 승인) | `ENABLE_LIVE_TRADING=true` 운영자 PR 후 |
| **AI Live Canary** (단계 3, 미진입) | ⏳ 초소액 자동 — 1회 ≤ 3만원 / 일일 손실 ≤ 5천원 | KIS LIVE (초소액) | 위 + AIExecutionGate PASS |
| **AI Live Auto Execution** (단계 4, 미진입) | ⏳ 정상 한도 — Canary 검증 통과 후 | KIS LIVE (한도 내) | `ENABLE_AI_EXECUTION=true` 별도 PR |

본 PR 시점 운영 모드: **단계 1 (AI Paper Auto Trading)** 만 가능. 단계 2~4 는
*아직 진입 불가*.

---

## 7. 안전 문구 (필수 — 본 PR 핵심)

> 본 문서를 읽는 모든 사람이 *반드시* 이해해야 할 4가지:

1. **본 문서는 전략 포트폴리오 *정의* 문서이며, 실전매매 *허가* 문서가
   아닙니다.** 4 매매기법군이 정의됐다고 해서 실거래가 자동으로 가능한
   것은 *아닙니다*.

2. **실전(Live) 자동매매는 [`docs/live_readiness_policy.md`](
   live_readiness_policy.md) 의 Live Gate / Canary 통과 후에만 가능합니다.**
   본 문서 단독으로는 실전 진입 *불가*.

3. **`ENABLE_LIVE_TRADING=false` 기본 유지** — 본 PR 은 `.env` 를 *수정하지
   않으며*, 안전 flag 4종 (`KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false`
   / `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`)
   default 가 *영구 유지* 됩니다.

4. **AI 가 broker API 를 *직접* 호출하지 않습니다** — CLAUDE.md 절대 원칙 1.
   모든 주문은 `RiskManager → PermissionGate → OrderExecutor` 순서를 거치며,
   `OrderExecutor.execute` *만이* broker 호출의 유일한 진입점입니다 (#40).
   AI Agent 의 매수/매도 판단은 *advisory* — 실주문은 본 게이트 흐름에서만
   생성됩니다.

---

## 8. 관련 문서 (cross-reference)

| 문서 | 의미 |
|---|---|
| [`docs/live_readiness_policy.md`](live_readiness_policy.md) | **AI Paper / AI Live 단계 분리 최상위 경계** (#0-01) |
| [`docs/strategy_registry.md`](strategy_registry.md) | 6개 전략 모듈 beginner-friendly 메타데이터 (#81~#83) |
| [`docs/promotion_policy.md`](promotion_policy.md) | 운용 모드 7단계 승격 |
| [`docs/paper_candidate_aggregator.md`](paper_candidate_aggregator.md) | 3-07 Paper 후보 통합 |
| [`docs/strategy_optimization_report.md`](strategy_optimization_report.md) | 3-08 운영자용 최적화 리포트 |
| [`docs/agent_strategy_input_schema.md`](agent_strategy_input_schema.md) | 4-01 AI Agent 표준 입력 |
| [`docs/strategy_combination_recommendation.md`](strategy_combination_recommendation.md) | 4-02 전략 조합 추천 |
| [`docs/overfit_warning_agent.md`](overfit_warning_agent.md) | 4-03 과최적화 경고 |
| [`docs/market_regime_strategy_selection.md`](market_regime_strategy_selection.md) | 4-04 장세별 전략 선택 |
| [`docs/paper_trading_policy.md`](paper_trading_policy.md) | PaperTrader (#42) — paper broker 강제 |

---

## 9. 변경 정책

본 문서의 *4 매매기법군 매트릭스* (§4) 변경은 다음 조건을 모두 충족하는 PR
에서만 허용:

1. **6 → N 전략 변경** — `STRATEGY_REGISTRY` 의 신규 strategy_id 추가 / 삭제와
   *동시* 갱신.
2. **매매기법군 추가 / 삭제** — 5번째 군 추가 시 4-02 다양성 휴리스틱 +
   4-04 `REGIME_STRATEGY_POLICY` 매트릭스 *동시* 갱신.
3. **Live 운용 가능 ❌ → ✅ 변경** — 영구 금지. Live 활성화는 [`docs/
   live_readiness_policy.md`](live_readiness_policy.md) §4 의 14개 조건 + 운영자
   명시 옵트인 PR 외에서는 *절대* 변경 불가. 본 문서의 "Live 운용 가능" 셀은
   *전략 단위* 가용성 표시 — 운용 모드 활성화와는 별개 채널로 결정.
4. **가짜 전략명 추가** — *영구 금지*. `골든브릿지` / `100% 승률` /
   `magic strategy` / `guaranteed` 등은 `test_strategy_registry_metadata.py` /
   `test_system_audit_invariants.py` 가 정적 grep 으로 차단.

---

## 10. CLAUDE.md 절대 원칙 상속

- ✅ 6개 전략 모듈 외 *어떤 전략도 존재하지 않음* — #87 system audit + #81
  registry metadata + 본 문서가 모두 6개 ID 로 lock.
- ✅ AI 가 broker 주문 API 를 *직접* 호출하지 않음 — 4 매매기법군의 모든 진입은
  `OrderExecutor.execute` 단일 진입점 통과.
- ✅ 모든 주문은 `RiskManager → PermissionGate → OrderExecutor` 순서.
- ✅ 기본 운용모드 SIMULATION / PAPER, `LIVE_AI_EXECUTION` 기본 비활성.
- ✅ 선물 기능은 본 문서 *범위 외* — 별도 `FuturesBrokerAdapter` + 영구
  BLOCKED 정책 (#46 / #76).
