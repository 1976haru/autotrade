"""VolumeBreakoutStrategy 신호 로직 단위 테스트 (#29).

거래대금 + 고점 돌파 + VWAP 합성 + 추격 가드(VWAP 격차/intraday runup) +
운영 가드(stale/blocked regime/open cooldown/liquidity) + 일중 1회 진입.

본 전략은 *주문을 직접 만들지 않는다* (CLAUDE.md 절대 원칙 2). 모든
StrategySignal은 `is_order_intent=False`이며, 실제 주문은 route_order →
RiskManager → PermissionGate → OrderExecutor 흐름이 처리한다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar, Signal
from app.strategies.base import (
    SignalAction,
    StrategyContext,
)
from app.strategies.concrete.volume_breakout import VolumeBreakoutStrategy


_DAY1_OPEN = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
_DAY2_OPEN = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)


def _bar(
    ts: datetime,
    *,
    o: int = 100, h: int = 102, lo: int = 99, c: int = 100, v: int = 1000,
    symbol: str = "X",
) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=lo, close=c, volume=v)


def _flat_session(
    n: int,
    *,
    base_ts: datetime = _DAY1_OPEN,
    close: int = 100,
    volume: int = 1000,
    interval_minutes: int = 1,
) -> list[Bar]:
    """평탄한 세션 — 같은 거래일의 N봉, 가격/거래량 일정."""
    return [
        _bar(
            base_ts + timedelta(minutes=i * interval_minutes),
            o=close, h=close + 1, lo=close - 1, c=close, v=volume,
        )
        for i in range(n)
    ]


def _ctx(bars: list[Bar], **kwargs) -> StrategyContext:
    return StrategyContext(bars=bars, symbol="X", **kwargs)


# ---------- 입력 검증 ----------

def test_rejects_invalid_params():
    with pytest.raises(ValueError):
        VolumeBreakoutStrategy(min_bars_required=1)
    with pytest.raises(ValueError):
        VolumeBreakoutStrategy(volume_lookback_bars=0)
    with pytest.raises(ValueError):
        VolumeBreakoutStrategy(volume_multiplier=1.0)
    with pytest.raises(ValueError):
        VolumeBreakoutStrategy(max_vwap_distance_pct=0)
    with pytest.raises(ValueError):
        VolumeBreakoutStrategy(open_cooldown_bars=-1)
    with pytest.raises(ValueError):
        VolumeBreakoutStrategy(stop_loss_pct=0)


# ---------- 1. bars 부족 ----------

def test_bars_insufficient_returns_no_signal():
    s = VolumeBreakoutStrategy(min_bars_required=25)
    bars = _flat_session(5)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.is_order_intent is False
    # decision_kind는 단순 NO_SIGNAL (안전 차단 아님)
    assert out.explanation.indicators.get("decision_kind") in (None, "NO_SIGNAL")


# ---------- 2. 거래대금 증가 없음 ----------

def test_no_volume_increase_returns_no_signal():
    """평탄한 세션 — 거래량/가격 일정 → 거래대금 증가 0, 고점 돌파 0 → NO_SIGNAL."""
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3)
    bars = _flat_session(30)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.is_order_intent is False


# ---------- 3. 고점 돌파 없음 ----------

def test_volume_spike_without_breakout_is_watch():
    """거래대금 증가했으나 종가가 고점 미돌파 — WATCH."""
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0)
    # 첫 25봉은 close=100 v=1000. 26번째 봉은 v=10000(거래대금 10x), close=100.
    bars = _flat_session(25)
    spike = _bar(
        _DAY1_OPEN + timedelta(minutes=25),
        o=100, h=101, lo=99, c=100, v=10_000,
    )
    bars.append(spike)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.WATCH
    assert out.is_order_intent is False


# ---------- 4. 거래대금 + 고점 돌파 + VWAP 위 → BUY ----------

def test_volume_breakout_above_vwap_returns_buy():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=10.0,
                                max_intraday_runup_pct=15.0)
    bars = _flat_session(25)
    # 26번째 봉: volume 5x + close 105 (5% breakout > 100 high & > VWAP).
    spike = _bar(
        _DAY1_OPEN + timedelta(minutes=25),
        o=100, h=106, lo=100, c=105, v=5000,
    )
    bars.append(spike)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.BUY, out.explanation.reasons
    assert out.is_order_intent is False
    assert out.sizing_hint is not None
    assert out.exit_plan is not None
    assert out.explanation.confidence is not None
    assert 0 <= out.explanation.confidence <= 100


# ---------- 5. VWAP 아래이면 BUY 불가 ----------

def test_below_vwap_does_not_buy():
    """첫 봉에서 huge volume + high close로 VWAP을 끌어올린 뒤, 이후 고점 돌파해도
    VWAP 아래면 BUY 아님."""
    s = VolumeBreakoutStrategy(min_bars_required=10, open_cooldown_bars=2,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=50.0)
    bars: list[Bar] = []
    # 1번째 봉: 매우 큰 거래량으로 VWAP을 200으로 끌어올린다.
    bars.append(_bar(_DAY1_OPEN, o=200, h=200, lo=200, c=200, v=1_000_000))
    # 2-10: close 95, volume 1000 (VWAP 여전히 200 근처).
    for i in range(1, 11):
        bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                         o=95, h=96, lo=94, c=95, v=1000))
    # 11번째: close 100 (이전 95들 대비 고점 돌파, volume 10x). VWAP은 여전히 ≈ 200 → close < VWAP.
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=11),
                     o=99, h=101, lo=99, c=100, v=10_000))
    out = s.generate_signal(_ctx(bars))
    # 수많은 안전 가드를 통과한 뒤 close < VWAP에서 WATCH 또는 REJECT.
    assert out.action != SignalAction.BUY
    assert out.is_order_intent is False


# ---------- 6. VWAP 격차 너무 멀면 REJECT ----------

def test_vwap_distance_too_far_rejects():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=2.0,  # 2% cap
                                max_intraday_runup_pct=50.0)
    bars = _flat_session(25)
    # close 110 → VWAP ≈ 100.x → 격차 > 2% → REJECT.
    spike = _bar(_DAY1_OPEN + timedelta(minutes=25),
                 o=100, h=112, lo=100, c=110, v=5000)
    bars.append(spike)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("VWAP 격차" in r for r in out.explanation.reasons)


# ---------- 7. intraday runup 과도하면 REJECT ----------

def test_intraday_runup_excessive_rejects():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=50.0,  # VWAP 가드 미발동
                                max_intraday_runup_pct=5.0)  # 5% cap
    # 세션 시가 100 → 마지막 close 110 → 10% runup > 5% cap.
    bars = _flat_session(25, close=100)
    spike = _bar(_DAY1_OPEN + timedelta(minutes=25),
                 o=100, h=112, lo=100, c=110, v=5000)
    bars.append(spike)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("intraday runup" in r for r in out.explanation.reasons)


# ---------- 8. open cooldown 안이면 REJECT ----------

def test_open_cooldown_rejects():
    s = VolumeBreakoutStrategy(min_bars_required=3, open_cooldown_bars=10,
                                volume_lookback_bars=2,
                                breakout_lookback_bars=2,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=50.0)
    # 세션 첫 5봉만 — bars_in_session=5 ≤ open_cooldown=10 → REJECT.
    bars = _flat_session(5)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("open cooldown" in r for r in out.explanation.reasons)


# ---------- 9. blocked regime ----------

def test_blocked_regime_rejects():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0)
    bars = _flat_session(25)
    spike = _bar(_DAY1_OPEN + timedelta(minutes=25),
                 o=100, h=106, lo=100, c=105, v=5000)
    bars.append(spike)
    out = s.generate_signal(_ctx(bars, regime="trending_down"))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("blocked regime" in r for r in out.explanation.reasons)


# ---------- 10. stale data ----------

def test_stale_data_rejects():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0,
                                stale_max_age_seconds=30)
    bars = _flat_session(25)
    spike = _bar(_DAY1_OPEN + timedelta(minutes=25),
                 o=100, h=106, lo=100, c=105, v=5000)
    bars.append(spike)
    # explicit data_age_seconds 주입.
    out = s.generate_signal(_ctx(bars, extra={"data_age_seconds": 120}))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("stale data" in r for r in out.explanation.reasons)


def test_stale_via_now_timestamp_rejects():
    """now timestamp를 extra에 넣으면 봉 timestamp와의 차이로 stale 판정."""
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0,
                                stale_max_age_seconds=30)
    bars = _flat_session(25)
    spike_ts = _DAY1_OPEN + timedelta(minutes=25)
    bars.append(_bar(spike_ts, o=100, h=106, lo=100, c=105, v=5000))
    # now가 봉 시각보다 5분 늦음 — stale.
    out = s.generate_signal(_ctx(bars, extra={"now": spike_ts + timedelta(minutes=5)}))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"


# ---------- 11. volume 0 안전 처리 ----------

def test_zero_volume_current_bar_rejects():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0)
    bars = _flat_session(25)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=25),
                     o=100, h=106, lo=100, c=105, v=0))
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("volume == 0" in r for r in out.explanation.reasons)


def test_zero_session_volume_rejects():
    """세션 전체 거래량 0 — VWAP 정의 불가."""
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0)
    bars = _flat_session(25, volume=0)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=25),
                     o=100, h=106, lo=100, c=105, v=0))
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"


# ---------- 계산: VWAP / average_turnover / volume_multiplier / breakout high ----------

def test_vwap_calculation_matches_session_typical_price_weighted():
    s = VolumeBreakoutStrategy(min_bars_required=5, open_cooldown_bars=2,
                                volume_lookback_bars=3,
                                breakout_lookback_bars=3,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=50.0,
                                max_intraday_runup_pct=50.0)
    bars: list[Bar] = []
    # 4봉 + 1 spike. VWAP을 손으로 계산할 수 있도록 단순 값.
    for i in range(4):
        bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                         o=100, h=100, lo=100, c=100, v=1000))
    spike = _bar(_DAY1_OPEN + timedelta(minutes=4),
                 o=100, h=110, lo=100, c=110, v=5000)
    bars.append(spike)
    out = s.generate_signal(_ctx(bars))
    # VWAP = (typical=100, v=1000)*4 + (typical=(110+100+110)/3≈106.667, v=5000) /
    #        (1000*4 + 5000) = (400000 + 533333.33)/9000 ≈ 103.7
    vwap = out.explanation.indicators["vwap"]
    assert 103.0 <= vwap <= 104.5, vwap


def test_volume_multiplier_uses_lookback_excluding_current():
    s = VolumeBreakoutStrategy(min_bars_required=5, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=50.0,
                                max_intraday_runup_pct=50.0)
    bars = _flat_session(4, close=100, volume=1000)  # avg turnover = 100*1000 = 100k
    spike = _bar(_DAY1_OPEN + timedelta(minutes=4),
                 o=100, h=110, lo=100, c=105, v=4000)  # cur = 105*4000 = 420k → 4.2x
    bars.append(spike)
    out = s.generate_signal(_ctx(bars))
    mult = out.explanation.indicators["volume_multiplier"]
    assert 4.0 < mult < 4.5, mult
    avg = out.explanation.indicators["avg_turnover"]
    assert avg == 100_000


def test_breakout_high_excludes_current_bar():
    s = VolumeBreakoutStrategy(min_bars_required=5, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=50.0,
                                max_intraday_runup_pct=50.0)
    bars: list[Bar] = []
    for i, c in enumerate([95, 100, 102, 101]):
        bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                         o=c, h=c + 1, lo=c - 1, c=c, v=1000))
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=4),
                     o=101, h=110, lo=101, c=108, v=5000))
    out = s.generate_signal(_ctx(bars))
    assert out.explanation.indicators["breakout_high"] == 102


# ---------- 점수 범위 ----------

def test_quality_score_and_confidence_bounds():
    s = VolumeBreakoutStrategy(min_bars_required=25, open_cooldown_bars=3,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=10.0,
                                max_intraday_runup_pct=15.0)
    bars = _flat_session(25)
    spike = _bar(_DAY1_OPEN + timedelta(minutes=25),
                 o=100, h=106, lo=100, c=105, v=5000)
    bars.append(spike)
    out = s.generate_signal(_ctx(bars, regime="trending_up"))
    indicators = out.explanation.indicators
    assert 0 <= indicators["confidence"] <= 100
    assert 0 <= indicators["quality_score"] <= 100


# ---------- StrategySignal invariant ----------

def test_signal_is_not_order_intent_for_all_branches():
    """모든 분기(BUY/WATCH/NO_SIGNAL/REJECT)에서 is_order_intent=False."""
    s = VolumeBreakoutStrategy(min_bars_required=10, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=50.0)

    # NO_SIGNAL — bars 부족
    s1 = VolumeBreakoutStrategy(min_bars_required=99)
    out = s1.generate_signal(_ctx(_flat_session(10)))
    assert out.is_order_intent is False
    assert out.action == SignalAction.NO_SIGNAL

    # WATCH — volume spike but no breakout
    bars = _flat_session(10, close=100, volume=1000)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=10),
                     o=100, h=101, lo=99, c=100, v=10_000))
    out2 = s.generate_signal(_ctx(bars))
    assert out2.is_order_intent is False

    # REJECT — runup excessive
    s2 = VolumeBreakoutStrategy(min_bars_required=10, open_cooldown_bars=2,
                                 volume_lookback_bars=4,
                                 breakout_lookback_bars=4,
                                 volume_multiplier=2.0,
                                 max_vwap_distance_pct=50.0,
                                 max_intraday_runup_pct=2.0)
    bars2 = _flat_session(10, close=100)
    bars2.append(_bar(_DAY1_OPEN + timedelta(minutes=10),
                      o=100, h=120, lo=100, c=115, v=5000))
    out3 = s2.generate_signal(_ctx(bars2))
    assert out3.is_order_intent is False
    assert out3.explanation.indicators.get("decision_kind") == "REJECT"

    # BUY
    bars3 = _flat_session(10, close=100, volume=1000)
    bars3.append(_bar(_DAY1_OPEN + timedelta(minutes=10),
                      o=100, h=106, lo=100, c=104, v=5000))
    out4 = s.generate_signal(_ctx(bars3))
    assert out4.is_order_intent is False
    if out4.action == SignalAction.BUY:
        assert out4.sizing_hint is not None
        assert out4.exit_plan is not None


# ---------- 일중 1회 진입 invariant ----------

def test_only_first_buy_fires_within_session():
    """일중 동일 조건이 두 번 나타나도 BUY는 한 번만."""
    s = VolumeBreakoutStrategy(min_bars_required=10, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=20.0)
    bars = _flat_session(10, close=100, volume=1000)
    # 11번째: BUY 후보 (volume 5x + breakout)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=10),
                     o=100, h=106, lo=100, c=105, v=5000))
    out1 = s.generate_signal(_ctx(bars))
    assert out1.action == SignalAction.BUY

    # 12번째: 다시 BUY 후보 — fired_today로 차단.
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=11),
                     o=105, h=108, lo=104, c=107, v=5000))
    out2 = s.generate_signal(_ctx(bars))
    assert out2.action == SignalAction.NO_SIGNAL


# ---------- 일별 reset ----------

def test_resets_state_at_day_boundary():
    s = VolumeBreakoutStrategy(min_bars_required=8, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=20.0)
    # day1: BUY 발화
    day1 = _flat_session(8, base_ts=_DAY1_OPEN, close=100, volume=1000)
    day1.append(_bar(_DAY1_OPEN + timedelta(minutes=8),
                     o=100, h=106, lo=100, c=105, v=5000))
    out1 = s.generate_signal(_ctx(day1))
    assert out1.action == SignalAction.BUY

    # day2: 새 세션 — VWAP / fired_today 모두 reset. BUY 다시 가능.
    day2 = _flat_session(8, base_ts=_DAY2_OPEN, close=200, volume=1000)
    day2.append(_bar(_DAY2_OPEN + timedelta(minutes=8),
                     o=200, h=212, lo=200, c=210, v=5000))
    bars = day1 + day2
    out2 = s.generate_signal(_ctx(bars))
    assert out2.action == SignalAction.BUY


# ---------- 직접 주문 invariant ----------

def test_strategy_does_not_import_broker_or_risk():
    """volume_breakout.py는 broker/risk/permission/execution import 0건."""
    import inspect

    from app.strategies.concrete import volume_breakout as mod
    src = inspect.getsource(mod)
    forbidden = (
        "from app.brokers", "from app.risk", "from app.permission",
        "from app.execution", "from app.governance",
    )
    for f in forbidden:
        assert f not in src, f"forbidden import: {f}"


def test_strategy_signal_dict_has_no_order_fields():
    """StrategySignal.to_dict()에 주문 필드 없음."""
    s = VolumeBreakoutStrategy(min_bars_required=10, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=20.0)
    bars = _flat_session(10, close=100, volume=1000)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=10),
                     o=100, h=106, lo=100, c=104, v=5000))
    out = s.generate_signal(_ctx(bars))
    d = out.to_dict()
    forbidden = ("side", "order_type", "limit_price", "decision",
                 "broker_order_id", "client_order_id", "quantity_to_execute")
    for f in forbidden:
        assert f not in d


# ---------- exit_rule / calculate_size ----------

def test_exit_rule_includes_trailing_and_time_exit():
    s = VolumeBreakoutStrategy(stop_loss_pct=2.0, take_profit_pct=4.0,
                                trailing_pct=1.5, time_exit_bars=30)
    plan = s.exit_rule(None)  # exit_rule's signal arg is unused for stateless metadata path
    assert plan.take_profit_pct == 4.0
    assert plan.stop_loss_pct == 2.0
    assert plan.time_exit_bars == 30
    assert "trailing" in plan.invalidation
    assert "trailing" in plan.rule_summary


def test_calculate_size_reduces_for_low_confidence():
    s = VolumeBreakoutStrategy()
    base_pct = float(s.risk_profile["position_size_pct"])
    h_high = s.calculate_size(None, risk_context={"confidence": 80})
    h_low  = s.calculate_size(None, risk_context={"confidence": 30})
    assert h_high.position_size_pct == base_pct
    assert h_low.position_size_pct  < base_pct


def test_calculate_size_reduces_for_chasing_risk():
    s = VolumeBreakoutStrategy(max_vwap_distance_pct=3.0, max_intraday_runup_pct=8.0)
    h = s.calculate_size(None, risk_context={
        "confidence": 80,
        "vwap_distance_pct": 2.5,   # > 3.0 * 0.7 = 2.1
        "intraday_runup_pct": 6.5,  # > 8.0 * 0.7 = 5.6
    })
    base_pct = float(s.risk_profile["position_size_pct"])
    assert h.position_size_pct < base_pct
    assert h.note is not None  # risk note 포함


# ---------- legacy on_bar ----------

def test_on_bar_returns_buy_for_full_signal():
    s = VolumeBreakoutStrategy(min_bars_required=10, open_cooldown_bars=2,
                                volume_lookback_bars=4,
                                breakout_lookback_bars=4,
                                volume_multiplier=2.0,
                                max_vwap_distance_pct=20.0,
                                max_intraday_runup_pct=20.0)
    bars = _flat_session(10, close=100, volume=1000)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=10),
                     o=100, h=106, lo=100, c=104, v=5000))
    sigs = [s.on_bar(bars[:i + 1]) for i in range(len(bars))]
    assert sigs[-1] == Signal.BUY
    # 워밍업 동안은 HOLD만.
    assert all(x == Signal.HOLD for x in sigs[:10])


def test_on_bar_holds_on_flat_data():
    """평탄한 데이터 — 어떤 BUY/SELL도 발생하지 않음 (registry test 호환)."""
    s = VolumeBreakoutStrategy()
    bars = _flat_session(50)
    for i in range(1, len(bars) + 1):
        sig = s.on_bar(bars[:i])
        assert sig == Signal.HOLD


# ---------- contract metadata ----------

def test_metadata_is_complete():
    s = VolumeBreakoutStrategy()
    assert s.entry
    assert s.exit
    assert s.invalidation
    assert s.required_regime != "any"
    assert s.risk_profile
    assert s.risk_profile["stop_loss_pct"] == 2.0
    assert s.risk_profile["take_profit_pct"] == 4.0
