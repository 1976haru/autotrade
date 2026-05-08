# Pullback Rebreak Strategy

> 코드: [`backend/app/strategies/concrete/pullback_rebreak.py`](../../backend/app/strategies/concrete/pullback_rebreak.py)
> 테스트: [`backend/tests/test_pullback_rebreak_strategy.py`](../../backend/tests/test_pullback_rebreak_strategy.py)
> 등록: `STRATEGY_REGISTRY["pullback_rebreak"]` ([`concrete/__init__.py`](../../backend/app/strategies/concrete/__init__.py))

## 1. 목적

1차 강한 상승(impulse) 직후의 *재돌파*를 그대로 추격하지 않고, 거래량이
줄어드는 *눌림(pullback)* 구간을 기다렸다가 그 이후 재돌파(rebreak)하는
첫 봉에서만 BUY 후보를 만든다.

VolumeBreakoutStrategy(#29)가 1차 첫 돌파를 잡는다면, 본 전략은 *그 다음
안전한 진입 후보*를 노린다 — 1차 진입 기회를 놓친 운영자가 추격매수
위험을 줄이며 들어갈 수 있는 2차 후보.

## 2. 왜 추격매수보다 안전한가

추격매수의 위험:
- impulse 직후의 종가는 단기 매도 압력이 누적된 자리. 매수자가 차익 실현하면
  급락 가능.
- 1차 돌파 후 거래량 동반 없이 가격만 따라 올라간 자리는 false breakout
  비중이 높다.

본 전략의 안전성:
- **눌림 형성 검증** — 가격이 일정 폭 retrace됐다는 것은 매도 압력이 한 번
  소진됐다는 의미.
- **거래량 fade 검증** — 눌림 구간에서 거래량이 줄어들면 매도가 강제가 아닌
  *시간 경과* 또는 차익실현 정도임을 시사.
- **재돌파 + 거래량 검증** — 재돌파 봉에서 거래량이 다시 증가하면 새로운
  매수세 진입의 신호.
- **모든 임계 hard-cap** — impulse가 너무 강하거나 pullback이 너무 깊으면
  reject. 패턴 인식의 over-fit을 방지.

## 3. 1차 상승 (impulse) 조건

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `impulse_lookback_bars` | 12 | impulse 검색 윈도우 (peak 좌측) |
| `min_impulse_pct` | 1.5% | 최소 상승률 — 미만이면 NO_SIGNAL |
| `max_impulse_pct` | 12.0% | 최대 상승률 — 초과면 REJECT (추격 위험) |

- `peak_close`는 현재 봉을 *제외한* 최근 (impulse_lookback + pullback_lookback)
  봉 중 close 최대인 지점.
- `impulse_low_close`는 peak 좌측 `impulse_lookback_bars` 윈도우 내 close 최저.
- `impulse_pct = (peak_close - impulse_low_close) / impulse_low_close × 100`.
- `min_impulse_pct` 미만이면 *유효한 1차 상승*이 아니라고 판단 → NO_SIGNAL.
- `max_impulse_pct` 초과면 이미 너무 멀리 갔다고 판단 → REJECT (추격 위험).

## 4. 눌림목 (pullback) 조건

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `pullback_lookback_bars` | 10 | pullback 검색 윈도우 (peak 우측) |
| `pullback_min_pct` | 0.3% | 최소 눌림 — 미만이면 WATCH |
| `pullback_max_pct` | 4.0% | 최대 눌림 — 초과면 REJECT (추세 깨짐) |

- `pullback_low_close`는 peak 우측에서 현재 봉 직전까지의 close 최저.
- `pullback_pct = (peak_close - pullback_low_close) / peak_close × 100`.
- `pullback_min_pct` 미만이면 눌림이 거의 없는 셈 — pattern 미형성. WATCH.
- `pullback_max_pct` 초과면 추세가 무너진 가능성 — REJECT.

## 5. 거래량 감소 (volume fade) 조건

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `pullback_volume_fade_ratio` | 0.85 | pullback 평균 거래대금 / impulse 평균의 상한 |

- impulse 구간 평균 turnover (= close × volume) 와 pullback 구간 평균 turnover
  를 비교.
- `volume_fade_ratio = pullback_avg_turnover / impulse_avg_turnover`.
- `volume_fade_ratio > pullback_volume_fade_ratio`이면 거래량이 충분히 줄지
  않았다는 뜻 → NO_SIGNAL.
- 거래량(volume)이 아니라 거래대금(turnover)으로 비교하는 이유 — 종목/시점 간
  비교 안정화 (volume_breakout과 동일 정책).

## 6. VWAP / 지지 조건

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `require_vwap_above` | True | 현재 종가가 세션 VWAP 위에 있어야 BUY |
| `max_vwap_distance_pct` | 4.0% | VWAP 격차 cap — 초과면 REJECT |

- 세션 누적 VWAP — typical price `(h+l+c)/3` × volume의 누적합 / 누적 volume.
  거래일이 바뀌면 reset.
- `require_vwap_above=True`(기본)이면 현재 종가가 VWAP 아래면 WATCH.
- `vwap_distance_pct = (close - vwap) / vwap × 100`이 cap 초과면 REJECT —
  rebreak 시점에 VWAP에서 너무 멀어졌다면 이미 추격.
- `pullback_held_above_vwap` (pullback_low가 VWAP 위) 여부도 indicators에
  carry — 운영자/감사 surface (참고용 + confidence 가산).

## 7. 재돌파 (rebreak) 조건

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `rebreak_volume_min_ratio` | 1.2 | 현재 봉 turnover / pullback 평균의 최소 |

- `is_rebreak = cur_close > peak_close` — 현재 종가가 impulse peak를 재돌파.
- `rebreak_volume_ratio = cur_turnover / pullback_avg_turnover`.
- `rebreak_volume_ratio < rebreak_volume_min_ratio`이면 새로운 매수세 부족
  → WATCH.

## 8. 장 초반 급등 추격 제한

| 파라미터 | 기본값 |
|---|---|
| `open_cooldown_bars` | 5 |
| `max_intraday_runup_pct` | 12.0% |

- 세션 시작 후 `open_cooldown_bars`개 봉 이내는 무조건 REJECT (한국 KRX
  09:00 동시호가 직후 회피).
- 세션 시가 대비 누적 등락률이 `max_intraday_runup_pct` 초과면 REJECT —
  당일 누적 급등주 추격 차단.

## 9. 손절/익절/트레일링/시간청산 (exit_rule)

| 필드 | 기본값/계산 |
|---|---|
| `take_profit_pct` | 4.0% |
| `stop_loss_pct` | 동적 — `pullback_low * (1 - stop_loss_below_pullback_low_pct/100)`을 stop으로 잡고 entry 대비 % 산출. context 미제공 시 baseline 2.0%. |
| `time_exit_bars` | 30봉 |
| `trailing_pct` | 1.5% |
| `invalidation` | "pullback_low 이탈 / VWAP 하향 이탈 / trailing N% / 30봉 청산" |

핵심 차이 — `stop_loss_pct`는 **동적**. position_context에 `pullback_low_close`
+ `current_close`가 주어지면 *실제 pullback_low 기반*으로 손절폭을 계산.
운영자/Agent가 주어진 context에 따라 다른 stop을 갖게 된다.

## 10. 사용 가능한 / 차단되는 Market Regime

| Regime | 처리 |
|---|---|
| `trending_up` | 권장 — confidence 가산 |
| `news_driven` | 권장 — confidence 가산 |
| `gap_day` | 권장 — confidence 가산 |
| `any` | 허용 |
| `ranging` | 허용 (가산 없음) |
| `trending_down` | **차단** — REJECT |
| `high_vol` | **차단** — REJECT |
| `blocked` | **차단** — REJECT |

`blocked_regimes` / `allowed_regimes` 파라미터로 override 가능.

## 11. 과최적화 주의 (anti-overfit)

본 전략은 패턴 인식 over-fit이 가장 큰 리스크. 다음으로 방어:

- **모든 임계가 명시 파라미터** — magic number 0건. 운영자가 universe / 시간
  단위 / 시장 환경별로 sensitivity 분석 가능.
- **양방향 hard-cap** — impulse가 강해도 reject (max), 약해도 reject (min).
  pullback도 마찬가지. 단방향 임계만 두면 한 축이 폭주해도 신호가 나가는
  over-fit이 발생.
- **3 종류 거래량 검증** — impulse는 baseline 대비 증가(간접 검증), pullback은
  impulse 대비 감소, rebreak는 pullback 대비 증가. 한 축의 noise만으로 신호가
  나가지 않도록 다중화.
- **VWAP + runup 두 추격 가드** — 한 가드만 두면 spike 직후 회복 / 누적 급등의
  한쪽만 잡힌다.
- **일중 1회 진입** — 같은 날 두 번째 패턴은 운영자 결정.
- **`_fired_today` 체크가 패턴 검출보다 먼저** — BUY 후 같은 날 다른 구조가
  형성돼도 무의미한 계산을 피함. 운영자 동선에서도 "이미 한 번 발화"가 우선
  surface.

## 12. 백테스트 필요 항목

LIVE 승격 평가 전 (`docs/promotion_policy.md`):

1. KOSPI 200 / 코스닥 150 universe에서 N개월 backtest
2. `min_impulse_pct` 1.0 / 1.5 / 2.0 / 3.0 sensitivity
3. `max_impulse_pct` 8.0 / 12.0 / 15.0 / 20.0 sensitivity
4. `pullback_min_pct` 0.3 / 0.5 / 1.0 sensitivity
5. `pullback_max_pct` 2.0 / 4.0 / 6.0 sensitivity
6. `pullback_volume_fade_ratio` 0.7 / 0.85 / 0.95 sensitivity
7. `rebreak_volume_min_ratio` 1.0 / 1.2 / 1.5 / 2.0 sensitivity
8. data_source `market` 비율 ≥ 70% (`docs/strategy_promotion_gate.md`)
9. Monte Carlo (`docs/monte_carlo_policy.md`)
10. Walk-forward (`docs/walk_forward_policy.md`) — overfit 검증 필수

## 13. 실전 전 검증 기준

- [ ] 백테스트 expectancy > 0 (비용 반영)
- [ ] profit_factor > 1.3
- [ ] max_consecutive_loss ≤ 5
- [ ] approval_rate ≥ 60%
- [ ] **walk-forward** out-of-sample expectancy > 0 (over-fit 검증)
- [ ] LIVE_SHADOW 모드에서 30거래일 이상 read-only 운영, 신호 분포 정상
- [ ] PAPER 모드에서 30거래일 이상 KIS 모의투자 운영
- [ ] strategy scoreboard에서 backtest와 live의 win_rate 차이 ≤ 10%p
- [ ] VolumeBreakoutStrategy(#29)와 신호 중복도 분석 — 동일 종목/시점 중복은
      operator UI에서 single-shot으로 dedup 가능해야 함
- [ ] 운영자 LIVE 옵트인 PR (별도)

## 14. 전략은 주문하지 않는다

CLAUDE.md 절대 원칙 2를 코드 단에서 강제:

- broker / risk / permission / execution / governance 어떤 모듈도 import하지
  않음 — `test_strategy_does_not_import_broker_or_risk` 가드.
- `StrategySignal.is_order_intent`는 항상 `False`. 모든 분기(BUY/WATCH/
  NO_SIGNAL/REJECT)에 대해 테스트 가드.
- `StrategySignal.to_dict()`에 `side` / `quantity_to_execute` /
  `order_type` / `limit_price` / `decision` / `broker_order_id` /
  `client_order_id` 필드 없음 — 회귀 가드.
- 청산 결정도 ExitPlan(신호)만 반환 — 실제 SELL 주문은 운영자/Agent +
  RiskManager가 결정.

본 전략에서 만든 `StrategySignal.action == BUY`는 *후보*. 실제 주문이
나가는지는 `route_order`가 RiskManager → PermissionGate → OrderExecutor 순서로
결정하며, 운용모드(SIMULATION / PAPER / LIVE_SHADOW / LIVE_MANUAL_APPROVAL /
LIVE_AI_*)에 따라 자동 분기.
