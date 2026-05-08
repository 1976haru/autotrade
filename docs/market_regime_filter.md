# Market Regime Filter

> 코드: [`backend/app/filters/market_regime.py`](../backend/app/filters/market_regime.py)
> 테스트: [`backend/tests/test_market_regime_filter.py`](../backend/tests/test_market_regime_filter.py)
> 호출 helper: `apply_regime_filter_to_signal(signal, regime_decision)`

## 1. 목적

좋은 전략도 나쁜 장세에서는 손실 가능성이 커진다. 본 필터는 *지수 급락 /
변동성 확대 / 거래대금 위축 / 장 초반 혼란* 구간에서 신규 BUY 신호를
제한하거나 차단해 **운영자가 잘못된 진입을 자동으로 잡을 수 있게** 한다.

기존 [`backend/app/market/regime.py`](../backend/app/market/regime.py)
(checklist #135)는 단순 trending/ranging/high_vol 분류를 advisory로만 노출
한다. 본 필터는 그 위에:

- 더 풍부한 8개 시장 국면 (`MarketRegime`)
- 4개 결정 정책 (`RegimeDecisionKind`)
- BUY/SELL 분리 + size_multiplier
- Strategy 신호를 변환하는 `apply_regime_filter_to_signal` helper

를 얹는다. 기존 regime.py는 그대로 유지 — advisory 호출자가 깨지지 않는다.

## 2. 기본 국면 (`MarketRegime`)

| 국면 | 의미 |
|---|---|
| `TREND_UP` | 단기 SMA가 장기 SMA보다 +0.5% 이상 + 변동성 정상 |
| `TREND_DOWN` | 단기 SMA가 장기 SMA보다 -0.5% 이하 + 변동성 정상 |
| `CHOPPY` | 횡보 (legacy `ranging`) — 추세 없음 |
| `HIGH_VOLATILITY` | 종가 CV(표준편차/평균) 임계 이상 — 일중 spike 잦음 |
| `LOW_LIQUIDITY` | 평균 거래량 또는 거래대금 임계 미만 — VWAP 왜곡 위험 |
| `RISK_OFF` | 최근 N봉 누적 등락률 ≤ 임계 (지수 급락) |
| `OPENING_CHAOS` | 세션 시작 후 N봉 이내 — VWAP/추세 신뢰 낮음 |
| `UNKNOWN` | 데이터 부족 또는 분류 불가 |

## 3. 초기 분류 기준 (휴리스틱 — 단순)

`MarketRegimeFilter.evaluate(bars)`가 다음 순서로 검사:

1. **`OPENING_CHAOS`** — 마지막 봉 거래일과 같은 날짜의 봉 수 ≤
   `opening_chaos_bars` (기본 5).
2. **`LOW_LIQUIDITY`** — 최근 `liquidity_window`개 봉(기본 20)의 평균 거래량
   < `min_avg_volume`(기본 100) 또는 평균 거래대금 < `min_avg_turnover`
   (기본 0=skip).
3. **`RISK_OFF`** — 최근 `risk_off_lookback_bars`(기본 30)봉 누적 등락률 ≤
   `risk_off_drop_pct`(기본 -2.0%).
4. **`HIGH_VOLATILITY`** — `high_vol_window`(기본 20)봉 종가 CV(표준편차/
   평균) ≥ `high_vol_cv_pct`(기본 2.5%).
5. 그 외 — `classify_regime(bars)` 호출 결과를 매핑:
   - `trending_up` → `TREND_UP`
   - `trending_down` → `TREND_DOWN`
   - `trending` → `TREND_UP` (방향 무관 추세는 보수적 매핑)
   - `ranging` → `CHOPPY`
   - `high_vol` → `HIGH_VOLATILITY`
   - `any` (데이터 부족) → `UNKNOWN`

**KOSPI/KOSDAQ 실시간 지수 연동은 Phase 2** — 현재는 종목 봉 자체를 proxy로
사용. 운영자가 외부 지수 데이터에서 RISK_OFF를 직접 감지했다면
`evaluate(bars, regime_override=MarketRegime.RISK_OFF)`로 강제 주입.

## 4. 결정 정책 (`RegimeDecisionKind`)

| 결정 | buy_allowed | sell_allowed | size_multiplier | 의미 |
|---|---|---|---|---|
| `ALLOW` | ✅ | ✅ | 1.0 | 정상 — 신규 BUY 허용 |
| `REDUCE_SIZE` | ✅ | ✅ | < 1.0 | BUY는 가능하나 사이즈 축소 권고 |
| `WATCH_ONLY` | ❌ | ✅ | 0.0 | 신규 BUY 차단, SELL/EXIT은 허용 |
| `BLOCK_NEW_BUY` | ❌ | ✅ | 0.0 | 신규 BUY 강제 차단 (audit/UI 강조) |

기본 정책 매핑:

| 국면 | 기본 결정 |
|---|---|
| `TREND_UP` | `ALLOW` |
| `TREND_DOWN` | `WATCH_ONLY` |
| `CHOPPY` | `REDUCE_SIZE` |
| `HIGH_VOLATILITY` | `REDUCE_SIZE` (× 0.5) |
| `LOW_LIQUIDITY` | `BLOCK_NEW_BUY` |
| `RISK_OFF` | `BLOCK_NEW_BUY` |
| `OPENING_CHAOS` | `BLOCK_NEW_BUY` |
| `UNKNOWN` | `WATCH_ONLY` |

운영자/Agent는 `MarketRegimeFilter(regime_policy={...})`로 universe 별 정책
override 가능.

## 5. BUY와 SELL의 차이

**핵심 원칙: SELL/EXIT은 차단하지 않는다.**

리스크 축소 주문(SELL/EXIT)을 regime 필터로 막으면, 시장이 악화되는 와중에
손절을 못 하게 되어 위험이 더 커진다. 따라서:

- `apply_regime_filter_to_signal`은 `signal.action ∈ {SELL, EXIT}`이면
  regime decision과 무관하게 신호를 통과시킨다.
- `RegimeDecision.sell_allowed`는 항상 `True`.
- 본 필터의 `buy_allowed`만이 정책에 따라 변동.

## 6. Strategy와의 관계

본 필터는 **advisory layer** — Strategy 신호를 받아 변환만 한다.

```python
strategy_signal = strategy.generate_signal(context)
regime_decision = filter.evaluate(bars)
final_signal    = apply_regime_filter_to_signal(strategy_signal, regime_decision)
# final_signal.action이 BUY → operator/Agent가 RiskManager → PermissionGate → OrderExecutor 순서로 라우팅.
```

`apply_regime_filter_to_signal`은:

- `signal.action == BUY`이고:
  - `decision == ALLOW` → 그대로 통과.
  - `decision == REDUCE_SIZE` → action 유지, `sizing_hint.position_size_pct ×=
    size_multiplier`, `sizing_hint.note`에 사유 기록.
  - `decision == WATCH_ONLY` → action을 `WATCH`로 강등.
  - `decision == BLOCK_NEW_BUY` → action을 `NO_SIGNAL`로 강등,
    `indicators["decision_kind"]="REJECT"` 표시.
- `signal.action ∈ {SELL, EXIT}` → 그대로 통과.
- 그 외(`WATCH`/`NO_SIGNAL`) → 그대로 통과.
- 모든 변환 결과는 `is_order_intent=False` invariant 유지.

**자동 적용은 신중히** — 본 PR에서는 `LiveStrategyEngine` /
`StrategyEngine` / `route_order`에 자동 연결하지 않는다. 운영자/Agent가
명시적으로 helper를 호출. 자동 연결은 별도 옵트인 PR.

## 7. Agent와의 관계

- Agent는 `RegimeDecision.to_dict()` 결과를 자신의 결정 요약에 carry — 운영자
  / 감사가 "왜 BUY 신호를 강등했나" 즉시 인지.
- `RiskOfficerAgent`(185)는 본 decision을 사전 검토 input으로 활용 — regime
  WATCH_ONLY/BLOCK_NEW_BUY면 자체 REJECT 사유에 추가.
- 자동 자동매매 흐름에는 강하게 묶지 않음. 본 필터는 *advisory + 신호 변환*
  까지만 — 실제 주문 결정은 RiskManager → PermissionGate → OrderExecutor가
  단일 진입점.

## 8. 한계 (현재 단계)

- 단순 휴리스틱 — 종목 봉을 proxy로 사용 (실제 지수 데이터 미연동).
- KOSPI/KOSDAQ 지수 실시간 연동은 Phase 2.
- market breadth (상승/하락 종목 비율), sector breadth (섹터별 강세도)
  미반영.
- volatility index(VKOSPI 등) 미연동.
- regime별 strategy performance backtest 미반영.
- regime-aware position sizing은 `REDUCE_SIZE` 단일 multiplier만 — regime
  별 size 정책 다양화는 후속.

자세한 backlog는 [`docs/backlog.md`](backlog.md) 참조.

## 9. 전략은 주문하지 않는다

본 필터 모듈도 broker / RiskManager / PermissionGate / OrderExecutor /
governance 어떤 모듈도 import하지 않는다 — 테스트
`test_filter_module_does_not_import_broker_or_risk` 가드. 모든 변환된 신호는
`is_order_intent=False` invariant. 실제 주문은 `route_order` 단일 진입점이
운용모드에 따라 자동 분기.
