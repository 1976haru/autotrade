# 4전략 + Agent Council Walk-forward 과최적화 방지 검증 (#47 / 6-02)

#46 의 [4전략 + Agent Council 백테스트](strategy_council_backtest.md) 결과가 *특정
기간에만 맞는 착시(overfit)* 인지 검증한다. 과거 OHLCV 를 시간 순서로
train / validation / test 로 분리하고 walk-forward(rolling) 로 반복 검증해, **검증
구간(out-of-sample)에서도 성과가 유지되는지** 본다.

> ⚠️ **본 작업은 과최적화 방지용 *검증* 작업이다.** 실제 주문을 보내지 않으며, KIS
> 주문 경로를 변경하지 않고, 실전 기능을 켜지 않는다. **walk-forward 결과만으로
> 실전 전환(live promotion)을 허가하지 않으며, 과거 성과는 미래 수익을 보장하지
> 않는다.**

> 참고: 본 모듈/스크립트는 **#46 council 백테스트 전용** walk-forward 다.
> 6 전략 registry *파라미터* walk-forward (#24/#25, 3-04)는 별도
> [`docs/walk_forward_validation.md`](walk_forward_validation.md) +
> `scripts/run_walk_forward_validation.py` 를 사용한다 — 목적/입력이 다르다.

관련 코드:
- `backend/app/backtest/walk_forward_validation.py` — split / 평가 / 판정 / 리포트
- `scripts/run_strategy_council_walk_forward.py` — CLI (JSON / Markdown)
- 테스트: `backend/tests/test_walk_forward_validation.py`,
  `backend/tests/test_run_strategy_council_walk_forward_script.py`
- fixture: `backend/tests/fixtures/backtest_ohlcv.py`
  (`walk_forward_stable_records` / `walk_forward_overfit_records` /
  `walk_forward_insufficient_records`)

## 1. 검증 목적

백테스트가 train 구간에만 맞춰진 *과최적화* 인지 확인한다. train 성과가 좋아도
validation / test 에서 무너지면 그 전략 결합은 신뢰할 수 없다. 본 검증은 #46 의
deterministic council 평가를 segment 별로 재실행하므로 결과가 재현 가능하다.

## 2. Walk-forward 정책

1. train / validation / test 를 **시간 순서** 로 분리한다.
2. **미래 데이터를 train 에 섞지 않는다** (날짜를 정렬해 분할).
3. 각 segment 는 독립적으로 #46 `run_strategy_council_backtest` 로 평가한다.
4. train 성과가 좋아도 validation / test 가 붕괴하면 `overfit_suspected` 로 표시한다.
5. validation / test 성과가 유지돼야 안정적(`WALK_FORWARD_STABLE`)이라고 본다.
6. 결과는 실전 승인이나 주문 권한이 아니다 — 분석 리포트로만 저장한다.

**분할 단위는 날짜(KST date)** — 한 거래일을 segment 로 쪼개지 않는다(#46 forward
return 이 당일 close 로 clamp 되므로 day 경계 분할이 안전).

### 지원 방식

- **A. THREE_WAY** — 단순 60/20/20 날짜 분할 (split 1개). `--train-pct` / `--validation-pct`.
- **B. ROLLING** — rolling walk-forward. 날짜 window(`--train-window-days` /
  `--validation-window-days` / `--test-window-days`)를 `--step-days` 만큼 슬라이딩,
  여러 split 생성.

### reason_code

| reason_code | 의미 |
|---|---|
| `WALK_FORWARD_INSUFFICIENT_DATA` | split 을 만들 데이터 부족 |
| `WALK_FORWARD_SPLIT_CREATED` | split 정상 생성(개별 split) |
| `WALK_FORWARD_VALIDATION_DEGRADATION` | validation 유지율 < 임계 |
| `WALK_FORWARD_TEST_DEGRADATION` | test 유지율 < 임계 |
| `WALK_FORWARD_OVERFIT_SUSPECTED` | train 양(+)인데 OOS 유지율 붕괴/음전 |
| `WALK_FORWARD_PERFORMANCE_COLLAPSE` | OOS 유지율 < collapse 임계 또는 expectancy 음전 |
| `WALK_FORWARD_STABLE` | 검증 구간 성과 유지(안정) |

## 3. 유지율 / overfit / stability 정의

- **유지율(retention ratio)** = segment expectancy / train expectancy.
  - train expectancy ≤ 0 또는 None → 측정 불가(None).
  - segment expectancy None(BUY 신호 없음) → 0.0 (성과 미유지로 *보수적* 판정).
- **overfit_suspected** (split): train expectancy > 0 인데 validation/test 유지율이
  `overfit_threshold`(기본 0.5) 미만이거나 OOS expectancy 가 음전이면 True.
- **stability_score** (0~1): `0.6 · retention_component + 0.4 · positive_fraction`
  - retention_component = clamp01(split별 min(val_ret, test_ret) 평균)
  - positive_fraction = (expectancy > 0 인 OOS segment 수) / (OOS segment 수)
- **overall_overfit_suspected**: 어떤 split 이라도 overfit 이거나 stability_score 가
  `overfit_threshold` 미만이면 True.
- **성과 붕괴(collapse) 구간**: OOS segment 유지율 < `collapse_threshold`(기본 0.2)
  또는 expectancy ≤ 0 인 구간.

## 4. 실행 명령어

```bash
# 60/20/20 3-way
python scripts/run_strategy_council_walk_forward.py \
    --input backend/tests/fixtures/backtest/sample_ohlcv.csv \
    --output reports/backtest/walk_forward.json

# rolling walk-forward
python scripts/run_strategy_council_walk_forward.py \
    --input data/backtest/sample_ohlcv.csv --mode ROLLING \
    --train-window-days 3 --validation-window-days 1 --test-window-days 1 \
    --step-days 1 --markdown reports/backtest/walk_forward.md
```

산출물 (`--output` 미지정 시 `reports/backtest/` 에 timestamp 파일):
- `walk_forward_report_YYYYMMDD_HHMMSS.json`
- `walk_forward_report_YYYYMMDD_HHMMSS.md`

> `reports/` 와 `data/` 는 `.gitignore` 등록 — 리포트/사용자 CSV 는 커밋하지 않는다.

exit code: `0` 정상 / `1` 데이터 부족·split 불가 / `2` 입력 파일 오류.

## 5. 출력 리포트 해석

Markdown 리포트(`walk_forward_report.md`)는 다음을 포함한다:
1. 요약 2. 설정 3. split별 성과표 4. train/validation/test 기간
5. validation/test 유지율 6. stability_score 7. overfit_suspected
8. degradation reasons 9. 성과 붕괴 구간 10. Agent Council vs best single (OOS)
11. market_regime 안정성 12. time_phase 안정성 13. 한계 14. 실전 전환 승인 아님
15. 수익 보장 아님.

- **stability_score 가 높고 overfit_suspected=False** → 검증 구간에서도 성과 유지(안정).
- **overfit_suspected=True** → train 에만 맞은 착시 의심 → 채택 보류 / 재설계.

## 6. Agent Council 안정성 보는 법

`council_vs_best_single`(OOS): validation+test 구간에서 Agent Council expectancy 가
best single 전략 expectancy 이상인 비율(`council_better_fraction`). 높을수록 Council
결합이 *검증 구간에서도* 단일 전략보다 안정적임을 시사한다(백테스트 비교일 뿐
실전 근거 아님).

`market_regime_stability` / `time_phase_stability`: OOS segment 들의 council 승률을
regime/phase 별로 모아 mean / min / max / spread + `consistent`(편차 ≤ 30%p) 표시.

## 7. 한계

- fixture / 사용자 CSV 의 품질·대표성에 결과가 좌우된다.
- forward return 은 당일 close 로 clamp 한 일중 보유 가정 — 익일 갭/슬리피지/
  부분체결 미반영.
- expectancy 기반 유지율은 가격대/수량이 segment 간 동일할 때 비교 가능하다.
- 데이터가 적으면 split 이 1개거나 불가(`WALK_FORWARD_INSUFFICIENT_DATA`).

## 8. 실전 전환 승인 아님 · 수익 보장 아님

- `WalkForwardReport.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` / `broker_order_sent=False` / `contains_secret=False`
  불변(dataclass 가드).
- 리포트/스크립트에 "수익 보장" / "실전 전환 승인" 문구 0건(테스트로 lock).
- 실전 전환은 별도 Paper Gate(#44/#72) → Live Capital Review(#41) → Manual
  Approval(#42) → Canary(#43) 게이트 + 운영자 명시 옵트인이 필요하다.

---

## 안전 가드 (정적 grep + dataclass 불변)

- `walk_forward_validation.py` / `run_strategy_council_walk_forward.py`: broker /
  OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 / anthropic /
  openai / httpx / requests import 0건, broker 주문/취소/route 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 변경 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건.
