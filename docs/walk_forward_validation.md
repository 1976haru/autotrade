# Step 3-04 -- Walk-forward 검증

> 본 문서는 *연구 / 검증* 파이프라인 정의입니다. **투자 조언이 아닙니다.**
> `HEALTHY` / `OVERFIT_RISK` 라벨은 *분석 라벨* — 자동 promotion 변경 / 자동
> 비활성 의미 X. 운영자 검토 후 후보 자격 조정.

## 1. 목적

3-03 의 parameter optimization 결과 (`paper_candidate_config.json`) 또는 임의의
(strategy, symbol, params) 조합을 *학습 (train) / 검증 (validation)* 구간으로
분리해 **과최적화 (overfit)** 를 탐지한다.

핵심 가설: 백테스트 단일 구간에서 좋은 성과는 *우연 / fit* 일 가능성. 여러
fold 에서 train 만 좋고 validation 부진하면 **OVERFIT_RISK** → 후보 자격
재검토.

## 2. WalkForwardConfig

```python
@dataclass(frozen=True)
class WalkForwardConfig:
    mode:             WalkForwardMode = ROLLING   # rolling | expanding
    train_days:       int             = 60
    validation_days:  int             = 20
    holdout_days:     int             = 0        # 최근 N 일 제외
    step_days:        int             = 20       # rolling 윈도우 스텝
    min_folds:        int             = 3        # 미만이면 INSUFFICIENT_DATA
    overfit_ratio:    float           = 0.5      # val/train ratio 미달이면 OVERFIT_RISK
```

### 2.1. ROLLING vs EXPANDING

| 모드 | train 윈도우 | 장점 | 단점 |
|---|---|---|---|
| `ROLLING` (default) | step_days 씩 슬라이드 (고정 길이) | 시장 regime 변화 반영 | 학습 데이터 양 제한 |
| `EXPANDING` | train_start=0 고정, train_end 확장 | 학습 데이터 풍부 | 초기 시점 의존성 |

### 2.2. holdout_days

가장 최근 N 일 데이터는 walk-forward 평가에서 *제외*. final validation /
out-of-sample 검증용으로 별도 보관. default 0 (disable).

## 3. 4단계 verdict

`backend/app/analytics/walk_forward.py::WalkForwardVerdict`:

| Verdict | 조건 | 의미 |
|---|---|---|
| `HEALTHY`           | train_avg > 0 AND val_avg > 0 AND ratio ≥ `overfit_ratio` | 검증 가능 — 다음 단계 진입 권장 |
| `OVERFIT_RISK`      | train_avg > 0 BUT val_avg ≤ 0 OR ratio < `overfit_ratio` | 과최적화 의심 — 후보 자격 검토 박탈 |
| `UNDERFIT`          | train_avg ≤ 0 AND val_avg ≤ 0 | 전략 / 파라미터 부적합 — grid 재검토 |
| `INSUFFICIENT_DATA` | fold 수 < `min_folds` | 데이터 / 윈도우 조정 필요 |

ratio 계산: `val_avg / train_avg` (둘 다 expectancy, KRW per trade).
`train_avg ≤ 0` 일 때 ratio=0.0 으로 clamp (분기 안전).

## 4. 과최적화 탐지 방식

1. **fold 분할** (`generate_splits`)
   - ROLLING / EXPANDING / holdout 기반 N folds 생성.
   - fold 수 < `min_folds` → 즉시 `INSUFFICIENT_DATA`.
2. **per-fold 백테스트**
   - 각 fold 의 train 구간 + validation 구간 *별개* 백테스트.
   - 동일한 strategy + params + 비용 모델 (commission/tax/slippage).
   - 결과는 `expectancy` (KRW per trade) 중심.
3. **집계**
   - `train_avg = mean(train_expectancy_i)`
   - `val_avg   = mean(val_expectancy_i)`
4. **verdict 분류** — 위 4단계.

## 5. CLI 사용

```bash
# 1) 대표 10종 × 6 전략 default 파라미터로 평가.
python scripts/run_walk_forward_validation.py

# 2) 3-03 paper_candidate_config.json 후보를 walk-forward 평가.
python scripts/run_walk_forward_validation.py \
    --from-paper-config reports/parameter_optimization/paper_candidate_config.json

# 3) 단일 (strategy, symbol) 평가.
python scripts/run_walk_forward_validation.py \
    --strategy sma_crossover --symbol 005930

# 4) 모드 / 윈도우 / 임계값 override.
python scripts/run_walk_forward_validation.py \
    --mode expanding --train-days 90 --validation-days 30 \
    --step-days 30 --min-folds 4 --overfit-ratio 0.6 --holdout-days 20

# 5) dry-run.
python scripts/run_walk_forward_validation.py --dry-run

# 6) yfinance 옵트인.
python scripts/run_walk_forward_validation.py --enable-yfinance
```

## 6. 산출물 (`reports/walk_forward/`, gitignore)

- `walk_forward_summary.json` — per_candidate 결과 + verdict + folds 상세.
- `walk_forward_ranking.csv`  — verdict 정렬 (HEALTHY 위, OVERFIT_RISK 아래).
- `walk_forward_report.md`    — markdown 요약.

JSON 최상위 + 각 결과 객체 invariant (테스트로 lock):

- `is_order_signal:        false`
- `auto_apply_allowed:     false`
- `is_live_authorization:  false`

## 7. 3-04 완료 기준

| 항목 | 본 PR 상태 |
|---|---|
| `WalkForwardConfig` (mode / train_days / validation_days / holdout_days / step_days / min_folds / overfit_ratio) | ✓ |
| ROLLING + EXPANDING 모드 | ✓ `generate_splits` |
| 4단계 verdict (HEALTHY / OVERFIT_RISK / UNDERFIT / INSUFFICIENT_DATA) | ✓ `WalkForwardVerdict` |
| 3-03 paper_candidate_config.json 입력 adapter | ✓ `read_candidates_from_paper_config` |
| 단일 (strategy, symbol, params) 평가 | ✓ `evaluate_walk_forward` |
| CLI + 산출물 (JSON / CSV / markdown) | ✓ `scripts/run_walk_forward_validation.py` |
| broker / OrderExecutor / route_order / KIS 주문 0건 | ✓ 정적 grep + 테스트 lock |
| 안전 flag default 변경 0건 | ✓ `.env.example` 미변경 |
| 모든 verdict 결과 객체 invariant (`is_order_signal=false` 등) | ✓ |

## 8. 3-05 (Stress test) 진입 조건

본 3-04 PR 머지 후 별도 PR 에서:

1. `HEALTHY` verdict 후보만 stress test 진입 권장.
2. `OVERFIT_RISK` 후보는 *별도 PR* 로 grid 재정의 또는 후보 박탈.
3. `INSUFFICIENT_DATA` 후보는 데이터 추가 수집 / holdout / step 조정.
4. 본 PR 의 `walk_forward_summary.json` 은 stress test CLI 의 input adapter
   로 carry 가능 (후속 PR).

## 9. CLAUDE.md 절대 원칙

- broker / OrderExecutor / route_order import 0건 (정적 grep + 테스트 lock).
- KIS 주문 API 호출 0건. read-only 시세 fetch (yfinance) 만.
- 실제 매수 / 매도 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- 안전 flag default 변경 0건: `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false` /
  `ENABLE_AI_EXECUTION=false` / `ENABLE_FUTURES_LIVE_TRADING=false`.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- `HEALTHY` / `OVERFIT_RISK` 라벨은 *분석 라벨* — paper 운용 / 실거래 활성화
  / 자동 promotion 변경 의미 X.
- `reports/*` gitignore 유지.
