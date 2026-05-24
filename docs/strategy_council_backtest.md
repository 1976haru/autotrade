# 4전략 + Agent Council 백테스트 (#46 / 6-01)

ORB / Momentum / Gap / VWAP 4 전략의 vote 와 **Agent Council** 의 최종판단
(final_action)을 *과거 OHLCV 데이터* 로 검증하고, 전략별 / Council 별 성과지표를
산출하는 **Paper 성능 분석용 백테스트**.

> ⚠️ **본 작업은 성능 검증용 백테스트다.** 실제 주문을 보내지 않으며, KIS 주문
> 경로를 변경하지 않고, 실전 기능을 켜지 않는다. **백테스트 결과만으로 실전
> 전환(live promotion)을 허가하지 않으며, 과거 성과는 미래 수익을 보장하지
> 않는다.**

관련 코드:
- `backend/app/backtest/strategy_council_backtest.py` — 백테스트 엔진/지표/리포트
- `scripts/run_strategy_optimization.py` — CLI (JSON / Markdown 리포트)
- 테스트: `backend/tests/test_strategy_council_backtest.py`,
  `backend/tests/test_run_strategy_optimization_script.py`
- fixture: `backend/tests/fixtures/backtest_ohlcv.py`,
  `backend/tests/fixtures/backtest/sample_ohlcv.csv`

## 1. 백테스트 목적

장중 자동매매에서 **4 전략을 가중 투표(Agent Council)** 로 결합하는 것이 *단일
전략보다 나은가* 를 과거 데이터로 검증한다. 기존 `agent_council.py` 의
deterministic evaluator + `run_agent_council` 을 *그대로 재사용* 하므로,
백테스트 결과가 실제 장중 판단 로직과 동일하다.

## 2. ORB / Momentum / Gap / VWAP 설명

| 전략 | 핵심 | BUY 조건(요약) | SELL 조건(요약) |
|---|---|---|---|
| **ORB** | opening range 돌파 | 현재가 > 당일 첫 N bar 고가 | 현재가 < 당일 첫 N bar 저가 |
| **Momentum** | 최근 종가 추세 | 최근 종가 수익률 > +1% | < −1% |
| **Gap** | 전일 종가 대비 갭 + gap-and-go | 갭 > +2% & 시가 유지 | 갭 < −2% |
| **VWAP** | 평균거래가격 대비 위치 | VWAP 상회 > +0.3% | VWAP 하향 이탈 < −0.3% |

가중치(`STRATEGY_WEIGHTS`): MOMENTUM 30 / VWAP 25 / ORB 25 / GAP 20.

## 3. Agent Council 비교 목적

Agent Council 은 4 전략 vote 를 가중 집계한 뒤 MarketRegime / RiskOfficer /
ExitPlan 게이트를 거쳐 단일 `final_action`(BUY/SELL/HOLD) + confidence +
quality_score + selected_strategies 를 만든다. 백테스트는 이 final_action 의
성과를 **단일 전략 최고 성과(best single)** 와 expectancy 기준으로 비교한다
(`council_better_than_best_single`).

## 4. 입력 데이터 형식

CSV 헤더 (필수): `timestamp,open,high,low,close,volume`
선택 컬럼: `symbol,vwap,market_regime,time_phase,gap_pct`

```csv
symbol,timestamp,open,high,low,close,volume
005930,2026-05-11T09:00:00+09:00,70000,70090,69970,70060,8000000
```

- 외부 KIS / yfinance 호출 없음 — *사용자가 제공한 CSV* 또는 fixture 로 동작.
- 중복 `(symbol, timestamp)` 는 자동 제거, 시간순 정렬.
- 데이터 부족 시 `BACKTEST_INSUFFICIENT_DATA` 반환(조용히 멈추지 않음).
- `(symbol, KST date)` 별로 그룹핑 — opening range / 일중 horizon 기준.

## 5. 실행 명령어

```bash
# JSON 리포트
python scripts/run_strategy_optimization.py \
    --input data/backtest/sample_ohlcv.csv \
    --output reports/backtest/latest.json

# Markdown 리포트 (+ 옵션)
python scripts/run_strategy_optimization.py \
    --input backend/tests/fixtures/backtest/sample_ohlcv.csv \
    --markdown reports/backtest/latest.md \
    --risk-profile BALANCED --primary-horizon close --horizons 5,10,30,60
```

산출물 (`--output` 미지정 시 `reports/backtest/` 에 timestamp 파일):
- `strategy_backtest_YYYYMMDD_HHMMSS.json`
- `strategy_backtest_YYYYMMDD_HHMMSS.md`

> `reports/` 와 `data/` 는 `.gitignore` 에 등록 — 생성 리포트와 사용자 CSV 는
> 커밋하지 않는다.

exit code: `0` 정상 / `1` 데이터 부족·검증 실패 / `2` 입력 파일 오류.

## 6. 출력 리포트 해석

- **전략별 성과 표**: BUY/SELL/HOLD 카운트 + primary horizon 의 승률 / 평균수익 /
  손익비 / profit_factor / MDD / 연속손실 / expectancy.
- **horizon별(`by_horizon`)**: 5 / 10 / 30 / 60 bar + 당일 `close` 각각의 지표.
- **SELL 신호 표**: 보유 청산 / 하락 방향 판단 평가의 하락 적중률(아래 §아님 참고).
- **market_regime / time_phase 버킷**: 장세·시간대별 BUY 신호 승률·평균수익.
- **Agent Council vs 단일 전략**: expectancy 비교 + ranking.

## 7. 승률 / 손익비 / PF / MDD / 연속손실 설명

- **승률(win_rate)** = 이익 거래 수 / 전체 거래 수.
- **평균수익(average_win) / 평균손실(average_loss)** = 이익·손실 거래의 평균 PnL
  (손실은 음수).
- **손익비(payoff_ratio)** = 평균수익 / |평균손실|.
- **profit_factor(PF)** = 총이익 / |총손실| (손실 0건이면 `None` — JSON 안전).
- **MDD(max_drawdown)** = 누적 PnL 곡선의 최대 peak-to-trough 낙폭(절대값).
- **연속손실(max_consecutive_losses)** = 손실이 연속된 최대 길이.
- **expectancy** = 승률 × 평균수익 + 패율 × 평균손실 (1거래 기대 PnL).

지표는 `app/backtest/metrics.py` 의 *단일 진실* 순수 함수를 재사용한다.

## 8. Agent Council 이 단일 전략보다 나은지 보는 법

리포트 `comparison` 블록:
- `council_expectancy` vs `best_single_expectancy` (최우수 단일 전략).
- `council_better_than_best_single` = Council expectancy ≥ best single expectancy.
- `expectancy_delta` = 차이. `ranking` = 전략별 expectancy 내림차순.

> 이 비교는 *백테스트 비교* 일 뿐, 실전 전환 근거가 아니다.

## 9. 백테스트의 한계

- fixture / 사용자 CSV 의 품질·대표성에 결과가 좌우된다.
- forward return 은 *당일 마지막 bar 로 clamp* 한 일중 보유 가정 — 익일 갭/
  슬리피지/부분체결/호가공백 미반영.
- 실제 체결가는 호가/유동성에 따라 다르며, 백테스트는 신호 시점 종가 기준.
- 신호가 *겹쳐* 발생할 수 있어(중복 진입) 단일 포지션 운용과 다르다 — 본
  백테스트는 *신호 품질 평가* 이지 자금 곡선 시뮬레이션이 아니다.

## 10. 수수료 / 슬리피지 반영 여부

본 백테스트는 **신호 forward-return 평가** 중심이라 기본적으로 수수료/세금/
슬리피지를 PnL 에 반영하지 않는다(전략 신호 품질 비교 목적). 비용 반영 자금
곡선 시뮬레이션이 필요하면 기존 `BacktestEngine`(`BacktestConfig.commission_bps /
tax_bps / slippage_bps`) + `scripts/run_backtest_all_strategies.py` 를 사용한다.

## 11. SELL 의 해석 (중요)

- **백테스트의 SELL 은 신규 숏 진입으로 해석하지 않는다.**
- 전략 SELL 신호는 *"보유 청산 신호 / 하락 방향 판단 평가"* 로 **분리** 집계되며,
  BUY 손익 풀에 섞지 않는다. 평가지표는 *하락 적중률(down_hit_rate)* — 신호 후
  가격이 실제로 하락했는지.
- Agent Council 의 SELL final_action 평가도 동일하게 *방향 판단* 평가다
  (`council_eval_held_position` 은 보유 청산/하락 판단 평가용 플래그이며 실제
  포지션/숏이 아니다).

## 12. 실전 전환 승인 아님 · 수익 보장 아님

- `BacktestReport.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` / `contains_secret=False` 불변(dataclass 가드).
- 리포트/스크립트 출력에 "수익 보장" / "실전 전환 승인" 문구 0건(테스트로 lock).
- 실전 전환은 별도 Paper Gate(#44/#72) → Live Capital Review(#41) → Manual
  Approval(#42) → Canary(#43) 게이트 + 운영자 명시 옵트인이 필요하다.

## 13. 장중 KIS 모의 테스트와 별개

본 백테스트는 *과거 데이터* 기반 오프라인 분석이다. 장중 KIS 모의(#89 KIS Paper
one-click)와는 별개 — KIS API 를 호출하지 않으며 실시간 체결과 무관하다.

---

## 안전 가드 (정적 grep + dataclass 불변)

- `strategy_council_backtest.py` / `run_strategy_optimization.py`: broker /
  OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 / anthropic /
  openai / httpx / requests import 0건, broker 주문/취소/route 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 변경 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건, 리포트 invariant `is_order_signal=False` /
  `is_live_authorization=False`.
