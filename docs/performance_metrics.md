# Step 3-06 -- 성과 지표 표준화

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**

## 1. 목적

백테스트 (3-02), parameter optimization (3-03), walk-forward (3-04), stress
test (3-05), paper 운용 검증 단계까지 *모두 동일한 14개 지표 키* 를 사용하도록
표준화. metric drift (각 단계에서 정의가 미묘하게 다른 문제) 를 방지한다.

**단일 진실**: `backend/app/analytics/metrics.py::compute_performance_metrics()`.

## 2. 14 필수 지표 (`PERFORMANCE_METRIC_KEYS`)

| Key | Type | 설명 |
|---|---|---|
| `total_return` | float | raw 누적 수익률 (소수, 0.123 = 12.3%) |
| `annualized_return` | float | 거래기간 환산 연환산 (252 일 기준) |
| `win_rate` | float (0-1) | 승률 (win count / trade count) |
| `trade_count` | int | 거래 횟수 |
| `profit_factor` | None \| float | 총이익 / 절대값(총손실). 손실 0 + 이익 >0 → None (JSON 안전) |
| `expectancy` | float | 평균 거래 PnL (KRW per trade) |
| `max_drawdown` | float (0-1) | 최대 낙폭 (절대값) |
| `avg_trade_pnl` | float | expectancy 와 동일 (호환성 별칭) |
| `avg_win` | float | 평균 승리 PnL |
| `avg_loss` | float | 평균 손실 PnL (절대값) |
| `loss_streak` | int | 최대 연속 손실 횟수 |
| `risk_adjusted_score` | float | (expectancy / initial_cash) / max_drawdown |
| `sharpe_like_score` | float | pseudo-Sharpe (mean(pnl) / std(pnl) × √N) |
| `fee_adjusted_return` | float | total_return - (fees + taxes) / initial_cash |
| `slippage_adjusted_return` | float | fee_adjusted_return - slippage / initial_cash |

> 사용자 spec 의 14번째 지표 "sharpe_like_score 또는 risk_adjusted_score" —
> **둘 다 포함**. 운영자가 선택해 사용. 출력 dict 은 15 키 (14 + alias).

## 3. 처리 정책

### 3.1. 빈 거래 (`trade_count == 0`)
모든 키 안전 기본값 (0 / None) — 예외 raise 0건. `safe_empty_metrics()` helper
제공.

### 3.2. 손실 없는 경우 (`profit_factor` 분모 0)
- `total_win == 0` AND `total_loss == 0` → `profit_factor = 0.0`
- `total_win > 0` AND `total_loss == 0` → `profit_factor = None` (JSON 안전,
  *무한* 표현 차단)

### 3.3. `max_drawdown` 우선순위
1. 명시 `max_drawdown` 인자 (사전 계산 값)
2. `equity_curve` (account balance series)
3. 누적 PnL 곡선 기반 estimate

모든 결과는 0-1 으로 clamp.

### 3.4. `expectancy` 계산
`sum(pnls) / trade_count` (산술 평균).
- KRW 단위 — 정규화는 caller 가 결정.

### 3.5. `loss_streak` 계산
음수 PnL 연속 카운트의 최대값. `0` PnL 은 streak 를 끊지만 streak count 에는
포함 안 됨.

### 3.6. 수수료 / 슬리피지 분리
3개 별도 키 — 운영자가 비용 영향 비교 가능.
- `total_return` — raw (비용 미반영)
- `fee_adjusted_return` — 수수료 + 세금 반영
- `slippage_adjusted_return` — 수수료 + 세금 + 슬리피지 모두 반영

### 3.7. JSON 직렬화
모든 값은 `int | float | None` — `NaN` / `inf` 발견 시 자동 클램프
(`safe_float` 함수). `json.dumps(metrics)` 출력에 `Infinity` / `NaN` 문자열
0건 보장 (테스트로 lock).

## 4. 사용 예

```python
from app.analytics.metrics import (
    compute_performance_metrics,
    is_insufficient_data,
    assert_required_keys_present,
)

metrics = compute_performance_metrics(
    trades=[trade1, trade2, ...],
    initial_cash=10_000_000,
    trading_days=60,
    raw_total_return=0.05,
    fees_paid=2000,
    taxes_paid=3000,
    slippage_paid=1000,
    # max_drawdown / equity_curve 둘 다 optional.
)

# 필수 키 누락 검출.
missing = assert_required_keys_present(metrics)
assert missing == [], f"누락 키: {missing}"

# INSUFFICIENT_DATA 판정 (advisory).
if is_insufficient_data(metrics, min_trade_count=10):
    verdict = "INSUFFICIENT_DATA"
```

## 5. 기존 백테스트 / 최적화 / 스트레스 모듈과의 연결

| 단계 | 모듈 | 본 표준 모듈 사용 방법 |
|---|---|---|
| 3-02 baseline | `backend/app/backtest/real_data/verdicts.py::classify_backtest_metrics` | `metrics["trade_count"]` / `profit_factor` / `max_drawdown` 사용 — 본 모듈 키와 호환 (테스트로 lock) |
| 3-03 optimization | `optimization_verdicts.classify_optimization_run` | 동일 키 + `expectancy` — 본 모듈 출력을 그대로 입력 가능 (테스트 lock) |
| 3-04 walk-forward | `app.analytics.walk_forward.evaluate_walk_forward` | per-fold 마다 `expectancy` 추출 — 본 모듈 키 사용 |
| 3-05 stress test | `app.analytics.stress_test._compute_metrics` | 16 필드 중 핵심 (trade_count / expectancy / profit_factor / max_drawdown / win_rate / loss_streak) 동일 |
| 3-07 paper | (별도 PR) | 본 모듈 결과를 `paper_candidate_config.json` 의 `risk_metrics` 필드에 그대로 carry |

기존 모듈들의 inline metrics 계산은 후속 PR (별도) 에서 본 표준 모듈로 단계적
이관 가능. 본 PR 은 *표준 모듈 + 호환 테스트* 만 — 기존 코드 변경 0줄.

## 6. 3-06 완료 기준

| 항목 | 본 PR 상태 |
|---|---|
| 14 표준 지표 키 (`PERFORMANCE_METRIC_KEYS`) 정의 | ✓ |
| `compute_performance_metrics()` — 모든 키 산출 | ✓ |
| 빈 거래 → 안전 기본값 (예외 raise 0건) | ✓ `safe_empty_metrics()` |
| `profit_factor` 손실 0 정책 (None / 0.0 분기) | ✓ `compute_profit_factor()` |
| `max_drawdown` 우선순위 (명시 > equity_curve > PnL) | ✓ |
| 수수료 / 슬리피지 분리 (`total_return` / `fee_adjusted` / `slippage_adjusted`) | ✓ |
| JSON 직렬화 가능 (NaN / inf 클램프) | ✓ |
| 기존 백테스트 / 최적화 / 스트레스 키와 호환 (테스트 lock) | ✓ `TestCompatibilityWithExistingModules` |
| broker / OrderExecutor / route_order import 0건 | ✓ 정적 grep + 테스트 lock |
| 안전 flag default 변경 0건 | ✓ |

## 7. 3-07 (Paper 후보 설정) 진입 조건

본 3-06 PR 머지 후 별도 PR 에서:

1. 3-02 ~ 3-05 모든 산출물의 `risk_metrics` 필드를 본 표준 모듈로 통일.
2. 운영자 검토용 *통합 paper 후보 리스트* 생성 — 각 후보가 모든 단계
   (3-02 / 3-03 / 3-04 / 3-05) 의 PASS / HEALTHY / BACKTEST_PASS 라벨을
   carry 하는지 확인.
3. `paper_candidate_config.json` 최종 export — 운영자 검토 후 Paper Auto
   Loop 수동 입력.

## 8. CLAUDE.md 절대 원칙

- 본 모듈은 *순수 함수 only* — broker / OrderExecutor / route_order / DB /
  외부 HTTP import 0건 (정적 grep + 테스트 lock).
- KIS 주문 API / Anthropic / OpenAI / requests / yfinance / httpx import 0건.
- 안전 flag default 변경 0건: `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false`
  / `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- 본 모듈은 *지표 계산* 만 — verdict 분류 / 후보 자격 결정 / 자동 promotion
  은 다른 모듈 / 운영자 책임.
