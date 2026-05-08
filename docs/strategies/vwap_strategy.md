# VWAP Strategy

> 코드: [`backend/app/strategies/concrete/vwap_strategy.py`](../../backend/app/strategies/concrete/vwap_strategy.py)
> 유틸: [`backend/app/strategies/vwap.py`](../../backend/app/strategies/vwap.py)
> 테스트: [`backend/tests/test_vwap_strategy.py`](../../backend/tests/test_vwap_strategy.py)
> 등록: `STRATEGY_REGISTRY["vwap_strategy"]` ([`concrete/__init__.py`](../../backend/app/strategies/concrete/__init__.py))

## 1. 목적

세션 VWAP을 *기준선*으로 삼는 보조 전략. 1차 전략(VolumeBreakout/
PullbackRebreak)이 momentum을 잡는다면, 본 전략은 VWAP을 신뢰 가능한
참조점으로 사용해 *VWAP 회귀(reclaim)*에서 BUY 후보를, *VWAP 이탈(loss)*에서
EXIT 후보를 surface한다.

본 전략은 *주문을 실행하지 않는다* (CLAUDE.md 절대 원칙 2). broker / risk /
permission / execution / governance 어떤 모듈도 import하지 않으며, 모든
StrategySignal은 `is_order_intent=False`. 실제 주문은 `route_order`가
RiskManager → PermissionGate → OrderExecutor 흐름으로 처리.

## 2. 보조 전략으로서의 위치

- **주력 진입은 다른 전략이 만든다** — VolumeBreakout(#29) / PullbackRebreak
  (#30)이 시그널을 만들면, 본 전략은 VWAP 정렬 여부로 합산 평가의 한 축이
  된다.
- 단독 진입도 가능하지만 `position_size_pct=3%` 보수적으로 배치 — 추세
  추격이 아닌 회귀 진입이라 손익비가 좁다.
- VWAP 이탈 EXIT은 *보유 중일 때만* 발화. 운영자가 `position_context.
  has_open_position=True`를 context에 넘겨야 한다.

## 3. VWAP 계산 방식

`app.strategies.vwap` 유틸이 제공:

| 함수 | 의미 |
|---|---|
| `typical_price(bar)` | (h+l+c)/3 — 표준 정의 |
| `vwap_of(bars)` | typical × volume 누적합 / volume 누적합. volume=0이면 None |
| `extract_session_bars(bars)` | 마지막 봉의 거래일과 같은 날짜의 봉만 추출 |
| `session_vwap(bars)` | extract_session_bars + vwap_of — 세션 누적 VWAP |
| `rolling_vwap(bars, window)` | 최근 N봉의 VWAP (거래일 경계 무시) |
| `vwap_deviation_pct(price, vwap)` | (price - vwap) / vwap × 100. vwap None/0이면 None |
| `average_volume(bars, window)` | 평균 거래량 |
| `average_turnover(bars, window)` | 평균 거래대금 (close × volume) |
| `check_liquidity(bars, ...)` | 거래량/거래대금 임계 검사 — `LiquidityCheck(ok, reason)` |

본 전략은:
- **session_vwap** — 1차 기준선 (BUY/EXIT 분기).
- **rolling_vwap** (기본 20봉) — 보조 — 단기 deviation 비교용. indicators에
  carry해 운영자/감사 surface.
- **vwap_deviation_pct** — entry cap / overextension cap 체크.

기존 `OrbVwapStrategy`(orb_vwap.py)는 자체 VWAP 누적을 인라인으로 가지고
있고 본 모듈을 import하지 않는다 — 기존 동작 보존이 우선이라 추후 통합 PR
에서 정리.

## 4. 진입 조건 (BUY 후보)

| 조건 | 파라미터 | 기본값 |
|---|---|---|
| 충분한 lookback | `min_bars_required` | 25 |
| liquidity (avg volume) | `min_avg_volume` | 100 |
| liquidity (avg turnover) | `min_avg_turnover` | 0 (skip) |
| reclaim cross-up | (직전 봉 ≤ VWAP, 현재 봉 > VWAP) | 강제 |
| 거래량 증가 | `reclaim_volume_min_ratio` | 1.2× |
| 괴리율 entry cap | `max_deviation_pct_for_entry` | 1.5% |
| 과도한 이격 차단 | `overextension_deviation_pct` | 3.0% |
| open cooldown | `open_cooldown_bars` | 5 |
| stale ≤ N초 | `stale_max_age_seconds` | 60 |
| regime ∉ blocked | `blocked_regimes` | trending_down/high_vol/blocked |
| 일중 1회 | `_fired_today` | 강제 |

## 5. 청산 조건

`exit_rule`이 반환하는 ExitPlan + EXIT 신호:

| 항목 | 기본값 |
|---|---|
| `take_profit_pct` | 2.5% |
| `stop_loss_pct` | 1.5% |
| `trailing_pct` | 1.0% |
| `time_exit_bars` | 20 |
| `invalidation` | "VWAP 하향 이탈 / trailing 1% / 20봉 청산" |

**EXIT 신호** — `position_context.has_open_position=True`이고 직전 봉 ≥ VWAP,
현재 봉 < VWAP인 cross-down 봉에서 `action=EXIT` 발화. 운영자/Agent가 청산
결정 시 활용. 보유 중이 아니면 EXIT 신호는 surface하지 않음.

## 6. 거래량 부족 guard (LOW_LIQUIDITY)

거래량 적은 종목에서 소수 큰 체결로 VWAP이 왜곡되는 케이스를 방어:

- `check_liquidity(bars, window=20, min_avg_volume, min_avg_turnover)`이
  최근 N봉(기본 20)의 평균을 계산.
- `min_avg_volume` 또는 `min_avg_turnover` 임계 미만이면 `LiquidityCheck.
  ok=False, reason="LOW_LIQUIDITY: ..."` 반환.
- 전략이 받으면 `action=NO_SIGNAL` + `decision_kind="REJECT"` + reason 그대로
  carry.

운영자 가이드:
- KOSPI 200 / 코스닥 150 large-cap에서는 default `min_avg_volume=100`,
  `min_avg_turnover=0`이 충분.
- 소형주 / 신규 상장 / 거래정지 직후 종목에서는 `min_avg_volume=10000+`,
  `min_avg_turnover=1억+`로 올려 운영.

## 7. 장 초반 VWAP 불안정 cooldown

- 세션 시작 후 `open_cooldown_bars`개 봉 이내는 무조건 REJECT.
- 한국 KRX 09:00 동시호가 직후의 VWAP은 첫 몇 봉의 거래량/가격이 dominate해
  매우 불안정 — 이 구간 진입을 자동 차단.

## 8. 과도한 VWAP 이격 추격 금지

두 단계 가드:

1. **`overextension_deviation_pct=3%`** — 현재 deviation이 이 cap 초과면
   REJECT. VWAP에서 너무 멀어진 가격은 추격 매수 위험.
2. **`max_deviation_pct_for_entry=1.5%`** — reclaim이 발생해도 deviation이
   이 cap 초과면 BUY 보류 (NO_SIGNAL with risk_notes). VWAP "근처" reclaim
   만 신호로 인정.

`overextension * 0.7` 영역에서는 BUY로 가더라도 sizing 축소 권고.

## 9. 차단되는 Market Regime

| Regime | 처리 |
|---|---|
| `trending_up` / `ranging` | 권장 — confidence 가산 |
| `any` | 허용 |
| `trending_down` | **차단** — REJECT |
| `high_vol` | **차단** — REJECT (변동성 과다 시 VWAP 신뢰 낮음) |
| `blocked` | **차단** |

`blocked_regimes` / `allowed_regimes` 파라미터로 override 가능.

## 10. 거래량 부족 종목에서의 사용 조건

본 전략은 보조라고 해도 *거래량 적은 종목에서는 VWAP 자체를 신뢰하기
어렵다*:

- ❌ 거래정지 직후 / 단일 거래로 거래량 채워지는 종목 — REJECT.
- ❌ 분봉 평균 거래량 < 100 — VWAP이 1~2 봉의 spike에 dominated.
- ⚠️ 분봉 평균 거래량 100~1000 — 작동하나 `min_avg_volume`을 1000+로 올려
  운영 권장.
- ✅ 분봉 평균 거래량 1000+ — default 임계로 안전.

`check_liquidity` 결과가 `decision_kind=REJECT`면 운영자/감사 UI에서 "이
종목은 VWAP 전략 부적합"으로 즉시 인지 가능.

## 11. 백테스트 필요 항목

LIVE 승격 평가 전 (`docs/promotion_policy.md`):

1. KOSPI 200 / 코스닥 150 universe N개월 backtest
2. `max_deviation_pct_for_entry` 0.5 / 1.0 / 1.5 / 2.0 sensitivity
3. `overextension_deviation_pct` 2.0 / 3.0 / 5.0 sensitivity
4. `reclaim_volume_min_ratio` 1.0 / 1.2 / 1.5 / 2.0 sensitivity
5. `min_avg_volume` 100 / 1000 / 10000 — universe 별 적정값
6. EXIT 신호의 실제 손실 회피율 측정 (보유 → cross-down EXIT 시점의 손실 vs
   미실행 hold)
7. data_source `market` 비율 ≥ 70% (`docs/strategy_promotion_gate.md`)

## 12. 실전 전 검증 기준

- [ ] 백테스트 expectancy > 0 (비용 반영)
- [ ] profit_factor > 1.2 (보조 전략은 1차 전략보다 낮은 임계 허용)
- [ ] LOW_LIQUIDITY REJECT 비율이 universe별로 적절 (소형주 100%, 대형주
      <5%)
- [ ] LIVE_SHADOW 모드 30거래일 이상 read-only 운영
- [ ] PAPER 모드 30거래일 이상 KIS 모의투자 운영
- [ ] strategy scoreboard에서 backtest와 live의 win_rate 차이 ≤ 10%p
- [ ] EXIT 신호 호환성 — 운영자가 보유 중일 때 EXIT 신호로 청산했을 때의
      누적 손실 회피 vs 무청산 비교
- [ ] OrbVwapStrategy(#142)와 신호 중복도 분석 — 동일 종목/시점 dedup

## 13. 전략은 주문하지 않는다

CLAUDE.md 절대 원칙 2를 코드 단에서 강제:

- broker / risk / permission / execution / governance 모듈 import 0건 —
  `test_strategy_does_not_import_broker_or_risk` + `test_vwap_util_does_not_
  import_broker_or_risk` 가드.
- `StrategySignal.is_order_intent`는 항상 `False`. 모든 분기(BUY/EXIT/WATCH/
  NO_SIGNAL/REJECT)에 대해 테스트 가드.
- `StrategySignal.to_dict()`에 `side` / `quantity_to_execute` /
  `order_type` / `limit_price` / `decision` / `broker_order_id` 등 주문
  필드 없음 — 회귀 가드.
- 청산 결정도 ExitPlan + EXIT *신호*만 반환 — 실제 SELL 주문은 운영자/Agent
  + RiskManager가 결정.

본 전략에서 만든 `StrategySignal.action == BUY`는 *후보*. `action == EXIT`는
운영자/Agent에 surface하는 *청산 권고 신호*. 실제 주문이 나가는지는
`route_order`가 운용모드(SIMULATION / PAPER / LIVE_SHADOW /
LIVE_MANUAL_APPROVAL / LIVE_AI_*)에 따라 자동 분기.
