# Loss Root Cause Tagging (#96)

> 본 문서는 *결정 시점 / 실행 단계* 손실 원인 추정 태깅 정책. **#79
> `loss_tagging_policy.md`** (post-trade 25 tag × 7 cat) 와는 별개 분석
> 레이어이므로 [§2 두 모듈의 구분](#2-두-모듈의-구분) 를 먼저 읽으세요.

## 1. 한 줄 요약

손실 거래에 *"왜 잃었는가"* 의 근본원인을 16개 태그 × 5 카테고리로 *추정*
분류한다. **태그는 추정값이며 확정 원인이 아니다** (`is_estimated=True`
영구). 본 모듈은 *분석 전용이며 주문 기능이 아니다* — AI Agent / Strategy
성능 개선 학습 자료로만 사용.

## 2. 두 모듈의 구분

| 항목 | #79 loss_tagging | #96 본 모듈 (loss_root_cause) |
|---|---|---|
| **모듈** | `app/analytics/loss_tagging.py` | `app/analytics/loss_root_cause.py` |
| **초점** | post-trade 결과 분류 (25 tag × 7 cat) | *결정 시점 / 실행 단계* 약점 태깅 (16 tag × 5 cat) |
| **입력** | 체결된 손실 거래의 전후 metric | *신호 → 진입 → 청산* 의 시점별 결정 데이터 |
| **태그 예** | STOP_LOSS_HIT, MARKET_SELLOFF, AI_LOW_CONFIDENCE | LATE_ENTRY, STALE_SIGNAL, AGENT_OVERRULED, HIGH_CORRELATION |
| **용도** | 손실 패턴 통계 / 추세 추적 | AI Agent prompt 개선 / 신호 품질 보강 |
| **사용 단계** | 일/주 단위 집계 | 거래별 즉시 + 누적 |
| **UI 카드** | `LossReasonCard.jsx` | `LossRootCauseCard.jsx` |
| **API** | `POST /api/analytics/loss-tags/...` | `POST /api/analytics/loss-root-cause/{evaluate,summarize}` |
| **DB 저장** | `LossReasonLog` 테이블 (alembic 0022) | 본 모듈은 *순수 함수* — DB write 0건 |

두 분석은 *상호 보완* — 운영자 / Agent 는 두 카드 모두 검토.

## 3. 왜 필요한가?

단순히 "손실 금액"만 보면 *재현 가능한 패턴* 을 찾기 어렵다. 운영자 / AI
Agent 는 "*왜* 잃었는가"를 알아야 다음에 같은 실수를 피할 수 있다.

### 시나리오 예시 (국내주식 단타)

| 케이스 | #79 만 보면 | #96 root cause |
|---|---|---|
| 1 | "STOP_LOSS_HIT, -50,000원" | "신호 생성 후 45분 지난 stale signal 로 진입 → STALE_SIGNAL + STOP_LOSS_HIT" |
| 2 | "MARKET_SELLOFF, -30,000원" | "포트폴리오 max |corr| = 0.92 → 동일 시장 충격에 다수 종목 동시 손실 → HIGH_CORRELATION" |
| 3 | "HIGH_SLIPPAGE, -15,000원" | "거래량 부족 시점 진입 → LOW_LIQUIDITY → SLIPPAGE" |
| 4 | "UNKNOWN, -20,000원" | "운영자가 AI 추천을 reject 후 직접 진입 → AGENT_OVERRULED" |

→ #79 는 *결과* 를 분류, #96 는 *결정의 약점* 을 분류. 두 정보를 합치면
운영자가 *어디를 고쳐야 같은 손실을 피할 수 있는지* 명확해진다.

## 4. 16개 root cause 태그

| 카테고리 | 태그 | 의미 | 임계 default |
|---|---|---|---|
| `decision` | `LATE_ENTRY` | 신호 생성 후 진입까지 지연 | entry_lag > 30s |
| `decision` | `LATE_EXIT` | 청산 트리거 후 실 청산 지연 | exit_lag > 60s |
| `decision` | `STALE_SIGNAL` | 진입 시점 신호 age 가 너무 큼 | age > 30min (#94 연동) |
| `decision` | `AGENT_OVERRULED` | 운영자가 AI 추천을 reject / override | bool |
| `risk` | `HIGH_CORRELATION` | 포트폴리오 동시 노출 상관계수 과다 | \|corr\| ≥ 0.85 (#95 연동) |
| `risk` | `RISK_GATE_REJECTED` | RiskManager pre-trade 차단 우회 의심 | bool |
| `market` | `HIGH_VOLATILITY` | 일중 변동성 과다 | volatility > 0.04 |
| `market` | `BAD_REGIME` | 시장 regime 이 전략에 부적합 | bool |
| `market` | `NEWS_RISK` | 진입 직전/직후 부정적 뉴스 | bool |
| `execution` | `LOW_LIQUIDITY` | 평균 대비 거래량 부족 | volume_ratio < 0.2 |
| `execution` | `SLIPPAGE` | 큰 slippage 발생 | \|slippage\| > 50 bps |
| `execution` | `SPREAD_TOO_WIDE` | bid-ask 스프레드 과다 | spread > 100 bps |
| `strategy` | `STOP_LOSS_HIT` | 손절가 도달 | bool |
| `strategy` | `TIME_STOP_HIT` | 시간 손절 | bool |
| `strategy` | `KIMP_CONVERGENCE_FAIL` | 김프 페어트레이딩 수렴 실패 (crypto, **본 프로젝트 미적용**) | bool |
| `unknown` | `UNKNOWN` | 입력 metric 부족 — 원인 추정 불가 | (자동) |

### primary tag 선정 우선순위

여러 태그가 동시에 부여될 때, **카테고리 우선순위 → severity** 순으로 primary 결정:

```
risk > decision > market > execution > strategy > unknown
HIGH > MEDIUM > LOW > UNKNOWN
```

예) `STALE_SIGNAL(HIGH, decision)` + `HIGH_CORRELATION(HIGH, risk)` + `STOP_LOSS_HIT(LOW, strategy)` →
primary = **HIGH_CORRELATION** (risk 카테고리가 최우선).

## 5. 김프·역김프 전략에서의 손실 태그 예시 (참고)

본 프로젝트는 국내주식 단타이지만, 향후 crypto 확장 시 동일 모듈로 김프
페어트레이딩 손실 분류 가능:

| 시나리오 | 부여되는 태그 |
|---|---|
| 김프 진입 → 수렴 전 한쪽이 먼저 청산 | KIMP_CONVERGENCE_FAIL + STOP_LOSS_HIT |
| 역김프 진입 → 환율 급변 → 수렴 무산 | KIMP_CONVERGENCE_FAIL + BAD_REGIME + NEWS_RISK |
| 김프 신호 늦게 도착 → 이미 수렴됨 | STALE_SIGNAL + KIMP_CONVERGENCE_FAIL |
| 알트코인 BTC 동조화로 페어 무효화 | HIGH_CORRELATION + KIMP_CONVERGENCE_FAIL |

`KIMP_CONVERGENCE_FAIL` 은 enum 에 포함하되 본 프로젝트 1차 배포에서는
*적용되지 않는다* (모든 입력 default `False`). 향후 crypto collector 추가
시 활성화.

## 6. AI Agent 성능 개선 활용

본 카드는 *AI Agent 의 학습 자료* 로 설계됨. 사용 흐름:

```text
1. 손실 거래 발생
   ↓
2. Collector 가 거래 전후 metric 추출 (entry_lag, signal_age, slippage 등)
   ↓
3. POST /api/analytics/loss-root-cause/evaluate
   ↓
4. 결과 (primary_tag + advice) 가 AI Agent prompt context 에 carry
   ↓
5. 다음 신호 생성 시 AI 가 본 컨텍스트로 학습 — *동일 패턴 회피*
```

**AI prompt 가이드** (LIVE_AI_ASSIST 단계 후속 PR):

```
Previous loss analysis for {symbol}:
- Primary root cause: {primary_tag} ({primary_category})
- All tags: {tags}
- Improvement advice:
  {advice}

Apply these lessons when evaluating new signals:
- If similar metric patterns appear (e.g., signal_age > 30m, portfolio
  correlation > 0.85), prefer NO_ENTRY or smaller size.
```

본 모듈은 *어떤 자동 차단 / 자동 강제 적용도 하지 않는다* — AI Agent 가
*선택적으로* 학습할 수 있는 advisory 정보만 제공.

## 7. API

### 7-1. POST `/api/analytics/loss-root-cause/evaluate`

```jsonc
// 입력 (단일 거래)
{
  "symbol": "005930",
  "is_loss": true,
  "trade_pnl": -50000,
  "strategy": "sma_crossover",
  "signal_age_minutes_at_entry": 45,
  "portfolio_max_correlation": 0.92,
  "realized_slippage_bps": 75.0,
  "hit_stop_loss": true
}
```

응답:
- `tags[]`: 부여된 태그 (다중 가능) + severity + rationale
- `primary_tag` / `primary_category`: 우선순위 적용 결과
- `rationale[]`: 각 태그의 trigger 근거
- `improvement_advice[]`: 개선 제안
- **invariants**: `is_estimated=true`, `is_order_signal=false`,
  `auto_apply_allowed=false`, `is_investment_advice=false`

### 7-2. POST `/api/analytics/loss-root-cause/summarize`

```jsonc
// 입력 (N개 거래)
{
  "losses": [
    {"symbol": "a", "hit_stop_loss": true, "strategy": "sma"},
    {"symbol": "b", "portfolio_max_correlation": 0.92, "strategy": "sma"},
    ...
  ]
}
```

응답 (요약):
- `total_loss_count`, `by_tag[]` (frequency), `by_category`,
  `top_tags`, `high_severity_tags`, `by_strategy`
- **invariants**: `is_estimated=true`, `is_order_signal=false`,
  `auto_apply_allowed=false`

## 8. Frontend UI (`LossRootCauseCard.jsx`)

| 노출 | testid |
|---|---|
| primary tag 배지 | `loss-root-cause-primary-{tag}` |
| 5 invariant 영구 배지 | `loss-root-cause-invariant-{estimated,no-order,no-auto,no-advice,analysis}` |
| disclaimer (영구) | `loss-root-cause-disclaimer` |
| 단일 거래 detail | `loss-root-cause-detail` |
| 근거 (rationale) | `loss-root-cause-rationale` |
| 개선 제안 | `loss-root-cause-advice` |
| 집계 요약 | `loss-root-cause-summary` |
| top tags / high severity | `loss-root-cause-{top-tags,high-severity}` |
| 태그 분포 표 (toggle) | `loss-root-cause-by-tag-table` |
| 전략별 카테고리 분포 | `loss-root-cause-by-strategy` |

### 금지 라벨 button 0개 (테스트로 lock)

`지금 매수` / `지금 매도` / `매수 실행` / `매도 실행` / `Place Order` /
`BUY signal` / `SELL signal` / `HOLD signal` / `실거래 시작` / `실거래 활성화` /
`ENABLE_LIVE_TRADING 토글` / `AI 자동 실행 활성화` / `전략 비활성화` /
`Apply Parameters` — 모두 0개.

### secret 입력 form 0개

`input` / `textarea` 0개 — secret 은 backend `.env` 에서만 관리.

## 9. 절대 원칙 invariant (코드 단 + 정적 grep 가드)

| invariant | 강제 위치 |
|---|---|
| `is_estimated=True` 항상 | `LossRootCauseResult.__post_init__` ValueError |
| `is_order_signal=False` 항상 | 동일 |
| `auto_apply_allowed=False` 항상 | 동일 |
| `is_investment_advice=False` 항상 | 동일 |
| broker / OrderExecutor / route_order / paper_trader import 0건 | 정적 grep 가드 |
| 외부 HTTP / AI SDK import 0건 | 정적 grep 가드 |
| `app.core.config.get_settings` 호출 0건 | 정적 grep 가드 |
| DB write 0건 | 정적 grep 가드 |
| 안전 flag mutate 0건 | 정적 grep 가드 |

## 10. 안전 flag default 유지 (#96 시점)

- `KIS_IS_PAPER=true` ✅
- `ENABLE_LIVE_TRADING=false` ✅
- `ENABLE_AI_EXECUTION=false` ✅
- `ENABLE_FUTURES_LIVE_TRADING=false` ✅

본 PR 은 안전 flag default 를 변경하지 않으며, **실거래 실행 기능을 추가하지
않는다**. 태그는 *advisory* — RiskManager / OrderGuard 자동 차단 트리거로
사용하지 *않는다*.

## 11. 후속 backlog

- **collector 자동화**: OrderAuditLog + MarketBar 에서 entry_lag /
  signal_age / volatility / slippage 등을 자동 추출하는 collector.
- **DB 저장**: `LossRootCauseLog` 테이블 추가 후 시계열 추적 (현재는 순수
  함수 — 호출자가 직접 저장).
- **AI prompt 통합**: LIVE_AI_ASSIST 단계에서 AI 가 받는 prompt context 에
  자동으로 최근 손실의 root cause + advice carry.
- **자동 운영자 알림**: HIGH severity tag (HIGH_CORRELATION /
  RISK_GATE_REJECTED) 발생 시 운영자 알림.
- **crypto 지원**: 본 모듈은 asset-class agnostic — KIMP_CONVERGENCE_FAIL
  등 crypto-specific tag 는 enum 에 이미 포함, 향후 crypto 거래소 collector
  추가 시 즉시 적용 가능.
- **시각화 강화**: tag frequency bar chart, category pie chart, strategy x
  category heatmap.

## 12. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/loss_tagging_policy.md`](loss_tagging_policy.md) — **#79 post-trade loss tagging** (본 문서와 별개)
- [`docs/alpha_decay.md`](alpha_decay.md) — #94 신호 단위 알파 감쇠 (STALE_SIGNAL 연동)
- [`docs/correlation_guard.md`](correlation_guard.md) — #95 포트폴리오 상관관계 (HIGH_CORRELATION 연동)
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager
- [`backend/app/analytics/loss_root_cause.py`](../backend/app/analytics/loss_root_cause.py) — evaluator 구현
- [`backend/tests/test_loss_root_cause.py`](../backend/tests/test_loss_root_cause.py) — 49 신규 테스트
