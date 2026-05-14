# Portfolio Correlation Guard (#95)

> 본 문서는 *포트폴리오 내 종목 간 수익률 상관관계* 기반 advisory 가드 정책을
> 정의합니다. **#78 `correlation_guard_policy.md`** (sector/theme 노출 cap) 와
> 는 별개 개념이므로 [§2 두 가드의 구분](#2-두-가드의-구분) 를 먼저 읽으세요.

## 1. 한 줄 요약

현재 보유 포지션 + 신규 진입 후보 종목 간의 *Pearson 상관계수 매트릭스*를
계산해, 포트폴리오 전체가 *동일 시장 리스크에 과노출*되었는지 검사한다.
verdict 5단계 (`HEALTHY` / `WATCH` / `WARN` / `BLOCK` / `INSUFFICIENT_DATA`)
+ 신규 진입 허용 여부 (`new_entry_allowed`) advisory. **BLOCK 도 권고
수준** — 실제 차단은 별도 RiskRule (후속 PR + 운영자 명시 옵트인).

## 2. 두 가드의 구분

| 항목 | #78 correlation_guard | #95 본 가드 (portfolio_correlation_guard) |
|---|---|---|
| **모듈** | `app/risk/correlation_guard.py` | `app/risk/portfolio_correlation_guard.py` |
| **분석** | sector / theme 노출 cap (메타 기반) | 종목 간 historical return Pearson correlation |
| **입력** | 포지션 + 신규 후보 + sector/theme 메타 | 위 + 종목별 close 시계열 (또는 returns) |
| **verdict** | PASS / WARN / REJECT / SKIP_NON_BUY | HEALTHY / WATCH / WARN / BLOCK / INSUFFICIENT_DATA |
| **차단 트리거** | sector/theme 비중 임계 초과 | 종목 간 |corr| 임계 초과 |
| **데이터 부족 시** | SKIP (차단 안 함) | INSUFFICIENT_DATA (차단 안 함) |
| **UI 카드** | `CorrelationGuardCard.jsx` | `PortfolioCorrelationGuardCard.jsx` |
| **API** | `POST /api/risk/correlation-guard/...` | `POST /api/risk/portfolio-correlation/evaluate` |
| **호출 빈도** | 매 BUY order pre-trade | 평가 시점마다 (대시보드 / Daily Report) |

→ 두 가드는 *상호 보완*. #78 은 RiskManager pre-trade 단계에서 *즉시 차단*용,
#95 는 *advisory 분석*용으로 운영자 / Agent 가 검토에 사용. 본 모듈은 #78 의
`compute_return_correlation` / `returns_from_closes` helper 를 *재사용*.

## 3. 왜 필요한가?

여러 전략 / 종목 / 포지션이 *같은 방향으로 과도하게 몰리는 위험*을 감지하는
것이 본 가드의 목적. 다음 시나리오에서 흔히 발생:

### 시나리오 (국내주식 예시)

- **동일 sector 다중 보유**: 반도체 4개 종목 보유 시 (삼성전자 / SK하이닉스 /
  ...) — 미국 반도체 인덱스 하락 시 *동시에* 손실
- **KOSPI beta 노출**: 대형주 다수 보유 시 시장 전체 sell-off 에 종합적 손실
- **테마 페이드**: 동일 테마 (예: AI 반도체) 다중 보유 시 테마 모멘텀 소진 시
  동시 매도 압력
- **전략 클러스터링**: 모멘텀 / VWAP 돌파 / 변동성 돌파 등 *서로 다른* 전략의
  신호가 *동일 종목군* 에 동시 발생 — 의도치 않은 노출 집중

### crypto 시장 (illustrative, 본 프로젝트 미적용)

사용자 요청에서 BTC/ETH/알트코인 동조화 사례가 언급되었으나, 본 프로젝트는
**국내주식 단타** 플랫폼이므로 crypto 직접 지원 없음. 다만 *상관관계 분석
로직 자체*는 asset-class agnostic — 수익률 시계열만 있으면 어떤 자산에도
적용 가능 (후속 PR 에서 crypto 채택 시 동일 모듈 재사용 가능).

### 본 가드가 막으려는 *문제 시나리오*

1. AI Agent 또는 Strategy 가 *독립적으로* 4개 종목 진입 결정
2. RiskManager 는 *각 주문* 의 1회 한도 / sector 한도 (#78) 만 검사 → 모두 통과
3. *그러나* 4개 종목이 KOSPI beta ~0.95 로 *실질적으로 같은 포지션*
4. KOSPI -3% 충격 시 *4개 모두 동시 손실* → daily_loss_limit 초과

#95 가 추가하는 *advisory* 검사:
- 본 가드는 4개 종목 *간의* return correlation 매트릭스를 계산
- max |corr| ≥ 0.85 면 BLOCK verdict + `new_entry_allowed=False`
- 운영자 / Agent 가 prompt context 에서 verdict 확인 → 진입 자제 또는 사이즈
  감소

## 4. verdict / 임계 매트릭스

`PortfolioCorrelationThresholds` (운영자 override 가능):

| 항목 | default | 의미 |
|---|---|---|
| `warn_threshold` | 0.50 | 0.5 ≤ |corr| < 0.7 → WATCH |
| `caution_threshold` | 0.70 | 0.7 ≤ |corr| < 0.85 → WARN |
| `block_threshold` | 0.85 | |corr| ≥ 0.85 → BLOCK |
| `min_bars` | 20 | Pearson 계산 최소 표본 |
| `max_block_pairs_for_healthy` | 0 | > 0 쌍 임계 초과 시 verdict 격상 |

| verdict | 의미 | new_entry_allowed |
|---|---|---|
| `HEALTHY` | max |corr| < 0.50 | True |
| `WATCH` | 0.50 ≤ max |corr| < 0.70 | True (informational) |
| `WARN` | 0.70 ≤ max |corr| < 0.85 | True (sizing 감소 권장) |
| `BLOCK` | max |corr| ≥ 0.85 | **False** |
| `INSUFFICIENT_DATA` | 표본 부족 | True (본 가드 미적용) |

### pair severity

| |corr| | severity |
|---|---|
| < 0.50 | LOW |
| 0.50 ~ 0.69 | MEDIUM |
| 0.70 ~ 0.84 | HIGH |
| ≥ 0.85 | EXTREME |

### strict 모드

`strict=True` 입력 시 `WARN` 도 `new_entry_allowed=False` 로 격하. 운영자가
*매우 보수적인 운용*을 원할 때 사용.

### 음의 상관관계 (-0.85 등) 처리

본 가드는 **|corr| 절댓값** 기준 — 음의 상관관계도 *반대 방향의 강한 결합*
이므로 동일하게 advisory 발생. 예) AAA(LONG) + BBB(LONG) 이 corr=-0.94 면
서로 헤지하지만 *결합 변동성이 매우 큼* (작은 시장 충격에도 큰 PnL 변동) —
운영자가 의도한 헤지인지 확인 필요.

## 5. API

### 5-1. POST `/api/risk/portfolio-correlation/evaluate`

```jsonc
// 입력
{
  "positions": [
    {"symbol": "005930", "notional_krw": 5000000, "direction": "LONG"},
    {"symbol": "000660", "notional_krw": 3000000, "direction": "LONG"}
  ],
  "candidate": {"symbol": "035420", "notional_krw": 2000000, "direction": "LONG"},
  "return_series_by_symbol": {
    "005930": [0.012, -0.005, 0.008, ...],
    "000660": [0.011, -0.006, 0.009, ...],
    "035420": [0.003, 0.001, -0.002, ...]
  },
  "strict": false
}
```

대안 입력 (시계열을 종가로 전달):
```jsonc
{
  "positions": [...],
  "close_series_by_symbol": {
    "005930": [70000, 70500, 70100, ...]
  }
}
```

응답 (요약):
- `verdict`: HEALTHY / WATCH / WARN / BLOCK / INSUFFICIENT_DATA
- `pairs[]`: 모든 종목 쌍의 (correlation, severity, sample_size)
- `portfolio_correlation_score`: 0~100 (mean |corr| × 100)
- `max_pairwise_correlation`, `mean_pairwise_correlation`,
  `high_correlation_pair_count`
- `candidate_max_correlation`: 후보 종목과 기존 포지션 최대 |corr|
- `new_entry_allowed`: bool
- **invariants**: `is_order_signal=false`, `auto_apply_allowed=false`,
  `is_live_authorization=false`

## 6. Frontend UI (`PortfolioCorrelationGuardCard.jsx`)

| 노출 | testid |
|---|---|
| verdict 헤드라인 | `portfolio-corr-headline` |
| BLOCK 차단 배너 | `portfolio-corr-block-banner` |
| 4 invariant 영구 배지 | `portfolio-corr-invariant-{no-order,no-auto,no-live,advisory}` |
| disclaimer (영구) | `portfolio-corr-disclaimer` |
| 후보 정보 (있을 때) | `portfolio-corr-candidate` |
| 통계 라인 | `portfolio-corr-stats` |
| 경고 / 권고 리스트 | `portfolio-corr-warnings`, `portfolio-corr-advice` |
| 쌍 상세 표 (toggle) | `portfolio-corr-pairs` |

### 금지 라벨 button 0개 (테스트로 lock)

`지금 매수` / `지금 매도` / `매수 실행` / `매도 실행` / `Place Order` /
`BUY signal` / `SELL signal` / `HOLD signal` / `실거래 시작` / `실거래 활성화` /
`ENABLE_LIVE_TRADING 토글` / `AI 자동 실행 활성화` / `전략 비활성화` /
`Apply Parameters` — 모두 0개.

### secret 입력 form 0개

`input` / `textarea` 0개 — secret 은 backend `.env` 에서만 관리.

## 7. 절대 원칙 invariant (코드 단 + 정적 grep 가드)

| invariant | 강제 위치 |
|---|---|
| `is_order_signal=False` 항상 | `PortfolioCorrelationResult.__post_init__` ValueError |
| `auto_apply_allowed=False` 항상 | 동일 |
| `is_live_authorization=False` 항상 | 동일 |
| broker / OrderExecutor / route_order / paper_trader import 0건 | 정적 grep 가드 |
| 외부 HTTP / AI SDK import 0건 | 정적 grep 가드 |
| `app.core.config.get_settings` 호출 0건 | 정적 grep 가드 (입력은 payload 로) |
| DB write 0건 | 정적 grep 가드 |
| 안전 flag mutate 0건 | 정적 grep 가드 |

## 8. 안전 flag default 유지 (#95 시점)

- `KIS_IS_PAPER=true` ✅
- `ENABLE_LIVE_TRADING=false` ✅
- `ENABLE_AI_EXECUTION=false` ✅
- `ENABLE_FUTURES_LIVE_TRADING=false` ✅

본 PR 은 안전 flag default 를 변경하지 않으며, **실거래 실행 기능을 추가하지
않는다**. AI Agent / Strategy 가 BLOCK verdict 를 무시하고 진입을 시도하더라도
RiskManager / OrderGuard 가 별도로 막을 책임 — 본 가드는 *판단 보조*만 제공.

## 9. 후속 backlog

- **collector 자동화**: `MarketBar` 또는 yfinance 데이터에서 자동으로
  return_series 추출하는 collector — 현재는 호출자가 명시 입력.
- **RiskRule 통합**: BLOCK verdict 인 신규 주문을 RiskManager 가 자동
  REJECT (별도 PR + 운영자 명시 옵트인).
- **AI prompt 통합**: LIVE_AI_ASSIST 단계에서 AI 가 받는 prompt context 에
  자동으로 verdict + max_corr carry — AI 의 제안이 BLOCK 상태 기반이면 사람
  승인 단계에서 reject 안내.
- **CLI**: `scripts/evaluate_portfolio_correlation.py --positions ... --output report.md`.
- **history**: 시계열로 portfolio_correlation_score 변화 추적.
- **시각화 강화**: heatmap, dendrogram (clustering 시각화).
- **crypto 지원**: 본 모듈은 asset-class agnostic — 후속 PR 에서 crypto
  포지션 / 거래소 시계열 collector 만 추가하면 즉시 동작.

## 10. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/correlation_guard_policy.md`](correlation_guard_policy.md) — **#78 sector/theme 노출 cap** (본 문서와 별개)
- [`docs/alpha_decay.md`](alpha_decay.md) — #94 신호 단위 알파 감쇠
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 RiskManager 단일 진입점
- [`docs/order_guard_policy.md`](order_guard_policy.md) — #38 OrderGuard (실 차단 책임)
- [`backend/app/risk/portfolio_correlation_guard.py`](../backend/app/risk/portfolio_correlation_guard.py) — evaluator 구현
- [`backend/tests/test_portfolio_correlation_guard.py`](../backend/tests/test_portfolio_correlation_guard.py) — 35 신규 테스트
