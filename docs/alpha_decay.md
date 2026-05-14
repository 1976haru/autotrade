# Signal Alpha Decay (#94)

> 본 문서는 *신호 단위* 알파 감쇠 분석 정책을 정의합니다. **#77 `Alpha Decay
> Monitor`** (전략 단위) 와는 별개 개념이므로 [§2 두 분석의 구분](#2-두-분석의-구분)
> 를 먼저 읽으세요.

## 1. 한 줄 요약

진입 신호가 *생성된 시점 t=0* 에서 1분 / 3분 / 5분 / 10분 / 30분 / 60분 후
기대수익이 얼마나 빠르게 감소하는지 측정 → `decay_score` (0~100) + verdict
(FRESH / DECAYING / STALE / EXPIRED / UNKNOWN) 산출. **EXPIRED verdict 인
신호는 신규 진입 근거로 사용 금지** — AI Agent 와 Strategy 가 코드 단에서
이를 확인하도록 강제.

## 2. 두 분석의 구분

| 항목 | #77 governance/alpha_decay | #94 본 문서 (analytics/signal_alpha_decay) |
|---|---|---|
| **모듈** | `app/governance/alpha_decay.py` | `app/analytics/signal_alpha_decay.py` |
| **단위** | 전략 (strategy-level) | 개별 신호 (signal-level) |
| **시간 척도** | 일 / 주 (baseline vs recent) | 분 / 시간 (1m ~ 60m bucket) |
| **비교 대상** | 검증 단계 통과 시점 vs 최근 운용 6 metric | 신호 t=0 vs 시간 경과 후 기대수익 |
| **목적** | "이 전략이 *여전히* 동작하는가?" | "이 *신호* 가 *지금* 진입 근거로 유효한가?" |
| **verdict** | HEALTHY / WATCH / DECAY_WARNING / DISABLE_CANDIDATE | FRESH / DECAYING / STALE / EXPIRED / UNKNOWN |
| **UI 카드** | `AlphaDecayCard.jsx` (Strategy 탭) | `SignalAlphaDecayCard.jsx` (Analytics 탭) |
| **API** | `/api/governance/alpha-decay/evaluate` | `/api/analytics/alpha-decay/{evaluate,freshness}` |
| **호출 빈도** | 일 1회 (장 마감 후 분석) | 신호 생성 / 평가 시점마다 |
| **데이터 출처** | `BacktestRun` + `OrderAuditLog` 누적 | 과거 신호 sample (collector 단계에서 누적) |

→ 두 분석은 *상호 보완*. 운영자는 두 카드 모두 확인.

## 3. 왜 필요한가?

단타 / 인트라데이 전략의 신호는 *생성 직후 분 단위로* 기대수익이 빠르게 감쇠한다:

- VWAP 돌파 신호: 30초~5분 내 진입 시 평균 +20bps, 30분 이상 경과 시 +2bps
- 거래량 폭증 신호: 1분 내 진입 시 +30bps, 60분 이상 경과 시 음수
- ORB 신호: 9:00~9:10 진입 시 강한 알파, 10시 이후 점진 소멸

**문제 시나리오** (#94 가 막으려는 것):
1. AI Agent 가 *오늘 09:35 생성된 ORB 신호* 를 보고 *오후 1:00 에 진입* 결정
2. RiskManager 는 *현재 가격 / 잔고 / 한도* 만 검사 → 통과
3. PermissionGate 는 *AI 권한* 만 검사 → 통과
4. → 신호가 5시간 묵힌 *유효하지 않은 알파* 인데도 주문 발행

#94 가 추가하는 *advisory* 신선도 검사:
- 본 모듈은 *신호 age* 를 별도 검증
- AI Agent 의 prompt context 에 `signal_age=5h, verdict=EXPIRED` 정보 carry
- Strategy 가 신호 생성 시 `signal_generated_at` 을 OrderRequest 에 첨부
- 본 카드 UI 가 운영자에게 *시각적으로* EXPIRED 신호 사용 자제 안내

**본 모듈은 RiskManager / OrderGuard 를 우회하지 않으며**, 단지 *판단 보조*
정보만 제공한다. 실제 차단은 별도 RiskRule 추가 (후속 PR) 에서 처리.

## 4. 신호 만료 기준 (default thresholds)

`SignalAlphaDecayThresholds` (운영자 override 가능):

| 항목 | default | 의미 |
|---|---|---|
| `max_actionable_age_minutes` | 30 | 진입 가능한 최대 신호 age |
| `decay_warn_pct` | 70.0 | t=0 대비 70% 미만 → WARN |
| `decay_fail_pct` | 30.0 | t=0 대비 30% 미만 → FAIL |
| `min_sample_count` | 10 | 통계 신뢰성 최소 표본 |
| `fresh_max_minutes` | 1 | FRESH verdict 최대 age |
| `decaying_max_minutes` | 30 | DECAYING verdict 최대 age |
| `stale_max_minutes` | 60 | STALE verdict 최대 age (60분 초과 → EXPIRED) |
| `min_decay_score_for_actionable` | 30.0 | decay_score 이 미만이면 verdict 가 EXPIRED 로 격하 |

### verdict 매트릭스

| age (분) | 표준 verdict | 동작 |
|---|---|---|
| 0~1 | FRESH | 진입 근거로 유효 |
| 1~30 | DECAYING | 진입 가능, 보수적 사이즈 권장 |
| 30~60 | STALE | 진입 *권장하지 않음* |
| > 60 | EXPIRED | **진입 금지** |

### bucket severity (`SignalDecayBucket.severity`)

| relative_to_t0_pct | severity |
|---|---|
| ≥ 70.0 | PASS |
| 30.0 ~ 69.9 | WARN |
| < 30.0 | FAIL |

표본 수가 `min_sample_count` 미만이면 PASS → WARN 격하.

## 5. AI Agent 가 EXPIRED 신호를 쓰면 안 되는 이유

| 이유 | 설명 |
|---|---|
| **알파 소멸** | 평균 기대수익이 0 또는 음수 — 진입 시 *통계적 기댓값 음수* |
| **시장 컨텍스트 변화** | 신호 생성 시점의 시장 상태 (변동성, 거래량, 추세) 가 더 이상 동일하지 않음 |
| **뉴스 / 테마 페이드** | 진입 트리거였던 뉴스 / 테마가 이미 시장에 반영 |
| **stop-loss 위치 기준 무효화** | 신호 생성 시점의 entry / stop / target 이 현 가격 대비 비현실적 |
| **fill quality 악화** | 30분 후 호가 / 거래량이 변해 slippage 가 신호 분석 시점 가정과 다름 |

**AI Agent prompt 가이드** (LIVE_AI_ASSIST 단계 후속 PR 에서 적용):

```
You are reviewing a signal candidate for entry.
- Signal generated_at: {signal_generated_at}
- Current time: {now}
- Signal age: {age_minutes} minutes
- Alpha decay verdict: {verdict}  (FRESH / DECAYING / STALE / EXPIRED / UNKNOWN)
- Decay score: {decay_score} / 100

Rules:
- If verdict is EXPIRED, you MUST NOT recommend entry on this signal.
  Respond with action=NO_ENTRY, reason="signal expired (age=X min)".
- If verdict is STALE, recommend conservative position size only.
- If verdict is DECAYING, normal review.
- If verdict is FRESH, normal review.
```

## 6. API

### 6-1. POST `/api/analytics/alpha-decay/evaluate`

```jsonc
// 입력
{
  "strategy_name": "sma_crossover",
  "samples": [
    {"age_minutes": 0,  "mean_return_bps": 20.0, "sample_count": 100},
    {"age_minutes": 1,  "mean_return_bps": 19.0, "sample_count": 100},
    {"age_minutes": 5,  "mean_return_bps": 18.0, "sample_count": 100},
    {"age_minutes": 10, "mean_return_bps": 15.0, "sample_count": 100},
    {"age_minutes": 30, "mean_return_bps": 8.0,  "sample_count": 100},
    {"age_minutes": 60, "mean_return_bps": 3.0,  "sample_count": 100}
  ],
  "strict": false
}
```

응답:
- `strategy_name`, `buckets[]`, `decay_score`, `max_actionable_age_minutes`
- `verdict_overall`: FRESH / DECAYING / STALE / EXPIRED / UNKNOWN
- `warnings[]`, `advice[]`, `insufficient_data`
- **invariants**: `is_order_signal=false`, `auto_apply_allowed=false`,
  `is_live_authorization=false`

### 6-2. GET `/api/analytics/alpha-decay/freshness?age_minutes=N`

빠른 시간 기반 freshness 판정 — 실시간 운영 환경에서 신호 도착 직후 사용.

```jsonc
// 응답
{
  "age_minutes": 35,
  "verdict": "STALE",
  "actionable": true,           // default 모드 — 단지 경고
  "actionable_strict": false,   // strict 모드 — STALE 차단
  "is_order_signal": false
}
```

## 7. Frontend UI (`SignalAlphaDecayCard.jsx`)

| 노출 | testid |
|---|---|
| verdict 헤드라인 | `signal-alpha-decay-headline` |
| EXPIRED 차단 배너 | `signal-alpha-decay-expired-banner` |
| 4 invariant 영구 배지 | `signal-alpha-decay-invariant-{no-order,no-auto,no-live,advisory}` |
| disclaimer (영구) | `signal-alpha-decay-disclaimer` |
| 대상 전략 표시 | `signal-alpha-decay-target` |
| 실시간 freshness (currentAgeMinutes prop) | `signal-alpha-decay-realtime-freshness` |
| 경고 / 권고 리스트 | `signal-alpha-decay-warnings`, `signal-alpha-decay-advice` |
| bucket 상세 표 (toggle) | `signal-alpha-decay-buckets` |

### 금지 라벨 button 0개 (테스트로 lock)

`지금 매수` / `지금 매도` / `매수 실행` / `매도 실행` / `Place Order` /
`BUY signal` / `SELL signal` / `HOLD signal` / `실거래 시작` / `실거래 활성화` /
`ENABLE_LIVE_TRADING 토글` / `AI 자동 실행 활성화` / `전략 비활성화` /
`Apply Parameters` — 모두 0개.

### secret 입력 form 0개

`input` / `textarea` 0개 — secret 은 backend `.env` 에서만 관리.

## 8. 절대 원칙 invariant (코드 단 + 정적 grep 가드)

| invariant | 강제 위치 |
|---|---|
| `is_order_signal=False` 항상 | `SignalAlphaDecayResult.__post_init__` ValueError |
| `auto_apply_allowed=False` 항상 | 동일 |
| `is_live_authorization=False` 항상 | 동일 |
| broker / OrderExecutor / route_order / paper_trader import 0건 | 정적 grep 가드 (`test_signal_alpha_decay.py::test_module_does_not_import_broker_or_executor`) |
| 외부 HTTP / AI SDK import 0건 | 정적 grep 가드 |
| `app.core.config.get_settings` 호출 0건 | 정적 grep 가드 (입력은 payload 로) |
| DB write 0건 | 정적 grep 가드 |
| 안전 flag mutate 0건 | 정적 grep 가드 |

## 9. 안전 flag default 유지 (#94 시점)

- `KIS_IS_PAPER=true` ✅
- `ENABLE_LIVE_TRADING=false` ✅
- `ENABLE_AI_EXECUTION=false` ✅
- `ENABLE_FUTURES_LIVE_TRADING=false` ✅

본 PR 은 안전 flag default 를 변경하지 않으며, 실거래 실행 기능을 추가하지
않는다. AI Agent / Strategy 가 본 verdict 를 무시하고 EXPIRED 신호로 진입을
시도하더라도 RiskManager / OrderGuard 가 별도로 막을 책임 — 본 카드는 *판단
보조*만 제공한다.

## 10. 후속 backlog

- **collector 자동화**: 과거 OrderAuditLog 의 (entry_price, t+1m_price,
  t+3m_price, ...) 를 자동 추출해 `SignalAlphaDecayInput.samples` 채우는
  collector.
- **RiskRule 통합**: `EXPIRED` verdict 인 신호의 주문을 RiskManager 가
  자동 REJECT 하는 신규 rule (후속 PR + 운영자 명시 옵트인).
- **AI prompt 통합**: LIVE_AI_ASSIST 단계에서 AI 가 받는 prompt 에 자동으로
  decay verdict carry — AI 의 제안이 EXPIRED 신호 기반이면 사람 승인 단계
  에서 즉시 reject 안내.
- **CLI**: `scripts/evaluate_signal_alpha_decay.py --strategy X --output report.md`.
- **history**: 시계열로 decay_score 변화 추적 (`SignalAlphaDecayLog` 테이블).
- **시각화 강화**: bucket bar chart, decay curve 시각화.

## 11. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/alpha_decay_monitor.md`](alpha_decay_monitor.md) — **#77 전략 단위 알파 감쇠** (본 문서와 별개)
- [`docs/loss_tagging_policy.md`](loss_tagging_policy.md) — #79 손실 원인 태그
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager 단일 진입점
- [`docs/order_guard_policy.md`](order_guard_policy.md) — #38 OrderGuard (실 차단 책임)
- [`backend/app/analytics/signal_alpha_decay.py`](../backend/app/analytics/signal_alpha_decay.py) — evaluator 구현
- [`backend/tests/test_signal_alpha_decay.py`](../backend/tests/test_signal_alpha_decay.py) — 43 신규 테스트
