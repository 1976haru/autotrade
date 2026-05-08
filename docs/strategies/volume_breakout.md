# Volume Breakout Strategy

> 코드: [`backend/app/strategies/concrete/volume_breakout.py`](../../backend/app/strategies/concrete/volume_breakout.py)
> 테스트: [`backend/tests/test_volume_breakout_strategy.py`](../../backend/tests/test_volume_breakout_strategy.py)
> 등록: `STRATEGY_REGISTRY["volume_breakout"]` ([`concrete/__init__.py`](../../backend/app/strategies/concrete/__init__.py))

## 1. 목적

거래대금 급증 + 최근 고점 돌파 + 세션 VWAP 상단 정렬을 동시에 만족하는
첫 봉에서 BUY *후보*를 생성하는 1차 전략. 단순/디버깅 가능한 구조이며,
급등주 추격과 데이터 오류로 인한 잘못된 진입을 코드 단에서 차단한다.

본 전략은 **신호만 만든다** — 실제 주문은 `route_order` 단일 진입점이
RiskManager → PermissionGate → OrderExecutor 순서로 처리한다 (CLAUDE.md
절대 원칙 2). broker / risk / permission / execution 어떤 모듈도 import하지
않는다 (전용 테스트로 강제).

## 2. 사용 이유

- **단타 자동매매의 1차 후보 생성**. 단순 SMA crossover보다 거래대금 검증
  + 추격 가드가 추가되어 잘못된 진입 케이스가 줄어든다.
- **운영자/감사가 읽을 수 있는 구조**. 모든 거부 사유가 reasons 배열에
  남고, indicators dict에 raw 지표(volume_multiplier, breakout_high, vwap,
  vwap_distance_pct, intraday_runup_pct)를 carry — 사후 디버깅과 이상 신호
  분석이 즉시 가능.
- **Strategy ABC 새 인터페이스 풀 지원**. `generate_signal`,
  `calculate_size`, `exit_rule`, `explain_signal` 모두 구현 — Agent /
  Scoreboard / quality 모듈이 일관 데이터로 소비.

## 3. 진입 조건 (BUY 후보)

모두 충족해야 BUY:

| 조건 | 파라미터 | 기본값 |
|---|---|---|
| 충분한 lookback | `min_bars_required` | 25 |
| 거래대금 ≥ 평균 × N | `volume_multiplier` | 2.0 |
| 종가 > 최근 N봉 종가 고점 | `breakout_lookback_bars` | 20 |
| 종가 > 세션 VWAP | `require_vwap_above=True` | 강제 |
| VWAP 격차 ≤ 임계 | `max_vwap_distance_pct` | 3.0% |
| 세션 시가 대비 등락률 ≤ 임계 | `max_intraday_runup_pct` | 8.0% |
| 세션 시작 후 N봉 경과 | `open_cooldown_bars` | 5 |
| stale 정도 ≤ 임계 | `stale_max_age_seconds` | 60 |
| regime ∉ blocked | `blocked_regimes` | trending_down / high_vol / blocked |
| volume > 0 + 평균 turnover > 0 | (liquidity) | 강제 |
| 일중 1회 진입 | `_fired_today` | 강제 |

## 4. 거래대금 조건

- `current_turnover = close × volume`
- `avg_turnover = mean(close × volume)` over 직전 `volume_lookback_bars`개
  봉 (현재 봉 *제외* — 자기 자신을 baseline 삼지 않음)
- `volume_multiplier = current_turnover / avg_turnover`
- BUY 후보가 되려면 `volume_multiplier ≥ self.volume_multiplier` (기본 2.0).
- `avg_turnover ≤ 0`이면 baseline 부재 — REJECT.

거래대금(turnover)을 단순 거래량(volume)이 아니라 close × volume으로 계산한
이유: 같은 거래량이라도 가격이 다르면 자금 흐름의 크기가 다르기 때문. 종목
간/시점 간 비교가 더 안정적.

## 5. 고점 돌파 조건

- `breakout_high = max(close)` over 직전 `breakout_lookback_bars`개 봉
  (현재 봉 *제외*)
- BUY 후보가 되려면 `current_close > breakout_high`.

고가(high)가 아닌 종가(close) 기준: 봉 중간의 spike wick은 신호로 보지
않고, 마감가가 고점 위에 있을 때만 진정한 돌파로 본다 (단타 잡음 회피).

## 6. VWAP 조건

- 세션 누적 VWAP — typical price `(h+l+c)/3` × volume의 누적합 / 누적 volume.
- 거래일이 바뀌면 reset (`bar.timestamp.date()` 기준 — timezone aware/naive
  모두 동작).
- `require_vwap_above=True`(기본)이면 `current_close > vwap`이 BUY 필수.
- `vwap_distance_pct = (close - vwap) / vwap × 100`이 `max_vwap_distance_pct`
  초과면 추격으로 간주 → REJECT.

## 7. 장 초반 급등 추격 제한

- 세션 시작 후 `open_cooldown_bars`개 봉 이내는 무조건 REJECT.
- 이유: 한국 KRX 09:00 동시호가 직후 주문 폭주 / 가짜 spike / VWAP 미정착
  구간을 회피.
- 기본 5봉 — 5분봉 기준 25분, 1분봉 기준 5분.

## 8. 과도한 상승 추격 금지

두 가드를 동시에 적용:

1. **VWAP 격차** — `vwap_distance_pct > max_vwap_distance_pct`이면 REJECT.
   spike 직후 VWAP에서 너무 멀어진 가격을 따라가지 않음.
2. **세션 시가 대비 runup** — `(close - session_open) / session_open × 100`이
   `max_intraday_runup_pct` 초과면 REJECT. 당일 누적 급등주 추격 차단.

또한 임계의 70% 초과 영역에서는 BUY로 가더라도 `risk_notes`와 sizing
축소가 자동 적용된다 (calculate_size).

## 9. 손절/익절/트레일링/시간청산 (exit_rule)

`exit_rule`이 다음을 반환:

| 필드 | 기본값 | 의미 |
|---|---|---|
| `take_profit_pct` | 4.0% | 익절 |
| `stop_loss_pct` | 2.0% | 손절 |
| `time_exit_bars` | 30 | 미청산 시 시간 청산 |
| `invalidation` | "VWAP 하향 이탈 / trailing 1.5% / 30봉" | 신호 무효화 조건 |
| `rule_summary` | "TP 4% / SL 2% / trailing 1.5% / 30봉 청산" | 사람이 읽는 한 줄 |

운영자/Agent는 본 plan을 *읽고* 청산 결정을 내린다 — 본 모듈이 직접
청산 주문을 만들지 않는다.

## 10. 사용 가능한 Market Regime

| Regime | 처리 |
|---|---|
| `trending_up` | 권장 — confidence 가산 |
| `news_driven` | 권장 — confidence 가산 |
| `gap_day` | 권장 — confidence 가산 |
| `any` | 허용 (신호 자체는 그대로) |
| `ranging` | 허용 (가산 없음) |

## 11. 차단되는 Market Regime

| Regime | 사유 |
|---|---|
| `trending_down` | 추세 역행 — 돌파가 noise일 가능성 |
| `high_vol` | 변동성 과다 — VWAP 추격 가드가 잡지 못한 spike 위험 |
| `blocked` | 운영자/Agent 명시 차단 |

`blocked_regimes` 파라미터로 override 가능.

## 12. 리스크

- **단방향 BUY only** — SHORT/SELL 신호 없음. 청산은 exit_rule이 담당.
- **VWAP 가정** — 세션 첫 봉부터 거래량이 정상이라고 가정. 거래량 0 세션은
  REJECT.
- **추격 임계값** — `max_vwap_distance_pct=3%`, `max_intraday_runup_pct=8%`는
  KRX KOSPI 분봉 가정. 다른 시장/봉 간격에서는 백테스트로 튜닝 권장.
- **lookback window** — `volume_lookback_bars`/`breakout_lookback_bars`는
  rolling N봉이라 거래일 경계를 가로지를 수 있음. 장초 갭 케이스에서는
  baseline이 전일 종가 흐름을 반영하는 것이 의도 (당일 첫 봉만으로 결정하지
  않음).
- **단순 평균** — Wilder 지수 평활화 대신 단순 평균 사용. 결정적이고 테스트
  가능, 다만 spike에 대한 반응 속도는 EMA 대비 느림.

## 13. 백테스트 필요 항목

LIVE 승격 평가 전 (`docs/promotion_policy.md` 기준):

1. KOSPI 200 / 코스닥 150 universe에서 N개월 backtest
2. `volume_multiplier` 1.5 / 2.0 / 2.5 / 3.0 sensitivity
3. `breakout_lookback_bars` 10 / 20 / 40 sensitivity
4. `max_vwap_distance_pct` 1.5 / 3.0 / 5.0 sensitivity
5. `max_intraday_runup_pct` 5.0 / 8.0 / 12.0 sensitivity
6. data_source `market` 비율 ≥ 70% (`docs/strategy_promotion_gate.md`)
7. Monte Carlo 시뮬 (`docs/monte_carlo_policy.md`)
8. Walk-forward (`docs/walk_forward_policy.md`)

## 14. 실전 전 검증 기준

LIVE 활성화 옵트인(`docs/promotion_policy.md`) 전 충족:

- [ ] 백테스트 expectancy > 0 (비용 반영)
- [ ] profit_factor > 1.3
- [ ] max_consecutive_loss ≤ 5
- [ ] approval_rate ≥ 60% (REJECT 비율이 너무 높지 않음)
- [ ] LIVE_SHADOW 모드에서 30거래일 이상 read-only 운영, 신호 분포 정상
- [ ] PAPER 모드에서 30거래일 이상 KIS 모의투자 운영, 가격 align 정상
- [ ] strategy scoreboard에서 backtest와 live의 win_rate 차이 ≤ 10%p
- [ ] 운영자 LIVE 옵트인 PR (별도)

## 15. 전략은 주문하지 않는다

CLAUDE.md 절대 원칙 2를 코드 단에서 강제:

- broker / risk / permission / execution 어떤 모듈도 import하지 않음
  (`test_strategy_does_not_import_broker_or_risk` 테스트로 가드).
- `StrategySignal.is_order_intent`는 항상 `False` (StrategyBase 계약).
- `StrategySignal.to_dict()` 응답에 `side` / `quantity_to_execute` /
  `order_type` / `limit_price` / `decision` / `broker_order_id` /
  `client_order_id` 같은 주문 필드 없음 — 테스트로 회귀 가드.
- 청산 결정도 신호 (`exit_rule`)만 반환 — 실제 청산 주문은 운영자/Agent
  + RiskManager가 결정.

본 전략에서 만든 `StrategySignal.action == BUY`는 *후보*다. 실제로 주문이
나가는지는 `route_order`가 RiskManager → PermissionGate → OrderExecutor
순서로 결정하며, 운용모드(SIMULATION / PAPER / LIVE_SHADOW /
LIVE_MANUAL_APPROVAL / LIVE_AI_*)에 따라 자동 분기된다.
