"""VWAPStrategy + VWAP utils 단위 테스트 (#31).

VWAP 계산 유틸 + 전략 신호 (reclaim BUY / loss EXIT / 거래량 가드 / 운영
가드 / 일중 1회 진입). 전략은 *주문을 직접 만들지 않는다* — 모든
StrategySignal은 `is_order_intent=False`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar, Signal
from app.strategies.base import (
    SignalAction,
    StrategyContext,
)
from app.strategies.concrete.vwap_strategy import VWAPStrategy
from app.strategies.vwap import (
    average_turnover,
    average_volume,
    check_liquidity,
    extract_session_bars,
    rolling_vwap,
    session_vwap,
    typical_price,
    vwap_deviation_pct,
    vwap_of,
)


_DAY1_OPEN = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
_DAY2_OPEN = datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc)


def _bar(
    ts: datetime,
    *,
    o: int = 100, h: int = 102, lo: int = 99, c: int = 100, v: int = 1000,
    symbol: str = "X",
) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=lo, close=c, volume=v)


def _ctx(bars: list[Bar], **kwargs) -> StrategyContext:
    return StrategyContext(bars=bars, symbol="X", **kwargs)


# ====================================================================
# VWAP utils
# ====================================================================


class TestVwapUtils:
    def test_typical_price_is_hlc_average(self):
        b = _bar(_DAY1_OPEN, h=120, lo=80, c=100)
        assert typical_price(b) == pytest.approx((120 + 80 + 100) / 3.0)

    def test_vwap_of_empty_returns_none(self):
        assert vwap_of([]) is None

    def test_vwap_of_zero_volume_returns_none(self):
        bars = [_bar(_DAY1_OPEN + timedelta(minutes=i), v=0) for i in range(5)]
        assert vwap_of(bars) is None

    def test_vwap_of_known_values(self):
        # 두 봉: typical_price 100/200, volume 100/300 → vwap = (100*100 + 200*300)/(100+300) = 70000/400 = 175
        bars = [
            _bar(_DAY1_OPEN, h=100, lo=100, c=100, v=100),
            _bar(_DAY1_OPEN + timedelta(minutes=1), h=200, lo=200, c=200, v=300),
        ]
        assert vwap_of(bars) == pytest.approx(175.0)

    def test_session_vwap_only_uses_current_day(self):
        bars = [
            _bar(_DAY1_OPEN,                          h=100, lo=100, c=100, v=1000),
            _bar(_DAY1_OPEN + timedelta(minutes=1),   h=200, lo=200, c=200, v=1000),
            _bar(_DAY2_OPEN,                          h=300, lo=300, c=300, v=1000),
            _bar(_DAY2_OPEN + timedelta(minutes=1),   h=400, lo=400, c=400, v=1000),
        ]
        # session = day2 두 봉. typical=300, 400 / vol=1000 each → vwap = 350
        assert session_vwap(bars) == pytest.approx(350.0)

    def test_extract_session_bars_returns_only_last_date(self):
        bars = [
            _bar(_DAY1_OPEN), _bar(_DAY1_OPEN + timedelta(minutes=1)),
            _bar(_DAY2_OPEN), _bar(_DAY2_OPEN + timedelta(minutes=1)),
        ]
        sess = extract_session_bars(bars)
        assert len(sess) == 2
        assert all(b.timestamp.date() == _DAY2_OPEN.date() for b in sess)

    def test_rolling_vwap_window_validation(self):
        with pytest.raises(ValueError):
            rolling_vwap([], 0)

    def test_rolling_vwap_uses_last_n_bars(self):
        bars = [
            _bar(_DAY1_OPEN + timedelta(minutes=i), h=c, lo=c, c=c, v=1000)
            for i, c in enumerate([100, 200, 300, 400])
        ]
        # window=2: 마지막 2봉 typical=300/400 → vwap=350
        assert rolling_vwap(bars, 2) == pytest.approx(350.0)

    def test_vwap_deviation_pct_basic(self):
        assert vwap_deviation_pct(110, 100) == pytest.approx(10.0)
        assert vwap_deviation_pct(90, 100)  == pytest.approx(-10.0)
        assert vwap_deviation_pct(100, 100) == pytest.approx(0.0)

    def test_vwap_deviation_pct_handles_none_or_zero(self):
        assert vwap_deviation_pct(100, None) is None
        assert vwap_deviation_pct(100, 0)    is None

    def test_average_volume_and_turnover(self):
        bars = [_bar(_DAY1_OPEN + timedelta(minutes=i), c=100, v=1000) for i in range(5)]
        assert average_volume(bars) == 1000.0
        assert average_turnover(bars) == 100_000.0
        assert average_volume(bars, window=3) == 1000.0
        assert average_volume([]) == 0.0

    def test_check_liquidity_pass(self):
        bars = [_bar(_DAY1_OPEN + timedelta(minutes=i), c=100, v=1000) for i in range(20)]
        result = check_liquidity(bars, min_avg_volume=500, min_avg_turnover=50_000)
        assert result.ok is True
        assert result.avg_volume == 1000.0
        assert result.avg_turnover == 100_000.0

    def test_check_liquidity_fails_on_low_volume(self):
        bars = [_bar(_DAY1_OPEN + timedelta(minutes=i), c=100, v=10) for i in range(20)]
        result = check_liquidity(bars, min_avg_volume=100)
        assert result.ok is False
        assert "LOW_LIQUIDITY" in (result.reason or "")
        assert "avg_volume" in (result.reason or "")

    def test_check_liquidity_fails_on_low_turnover(self):
        bars = [_bar(_DAY1_OPEN + timedelta(minutes=i), c=10, v=100) for i in range(20)]
        result = check_liquidity(bars, min_avg_turnover=10_000)
        assert result.ok is False
        assert "avg_turnover" in (result.reason or "")

    def test_check_liquidity_skips_zero_thresholds(self):
        """min_*=0이면 해당 축은 검사하지 않음."""
        bars = [_bar(_DAY1_OPEN + timedelta(minutes=i), c=10, v=1) for i in range(20)]
        result = check_liquidity(bars)  # 둘 다 0 default
        assert result.ok is True


# ====================================================================
# Strategy signal logic
# ====================================================================


def _flat_session(
    n: int,
    *,
    base_ts: datetime = _DAY1_OPEN,
    close: int = 100,
    volume: int = 1000,
) -> list[Bar]:
    return [
        _bar(base_ts + timedelta(minutes=i),
             o=close, h=close + 1, lo=close - 1, c=close, v=volume)
        for i in range(n)
    ]


def _build_reclaim_pattern(
    *,
    n_baseline:        int = 25,
    baseline_close:    int = 100,
    baseline_volume:   int = 1000,
    pre_reclaim_close: int = 99,    # 직전 봉 — VWAP 살짝 아래
    pre_reclaim_volume: int = 1000,
    reclaim_close:     int = 102,   # 현재 봉 — VWAP 위로 reclaim
    reclaim_volume:    int = 3000,  # 거래량 증가
    base_ts:           datetime = _DAY1_OPEN,
) -> list[Bar]:
    bars = _flat_session(n_baseline, base_ts=base_ts, close=baseline_close,
                         volume=baseline_volume)
    bars.append(_bar(
        base_ts + timedelta(minutes=n_baseline),
        o=pre_reclaim_close, h=pre_reclaim_close + 1, lo=pre_reclaim_close - 1,
        c=pre_reclaim_close, v=pre_reclaim_volume,
    ))
    bars.append(_bar(
        base_ts + timedelta(minutes=n_baseline + 1),
        o=pre_reclaim_close, h=reclaim_close + 1, lo=pre_reclaim_close - 1,
        c=reclaim_close, v=reclaim_volume,
    ))
    return bars


# ---------- 입력 검증 ----------

def test_rejects_invalid_params():
    with pytest.raises(ValueError):
        VWAPStrategy(min_bars_required=1)
    with pytest.raises(ValueError):
        VWAPStrategy(rolling_vwap_window=1)
    with pytest.raises(ValueError):
        VWAPStrategy(max_deviation_pct_for_entry=0)
    with pytest.raises(ValueError):
        VWAPStrategy(max_deviation_pct_for_entry=2, overextension_deviation_pct=2)
    with pytest.raises(ValueError):
        VWAPStrategy(reclaim_volume_min_ratio=0)
    with pytest.raises(ValueError):
        VWAPStrategy(open_cooldown_bars=-1)
    with pytest.raises(ValueError):
        VWAPStrategy(stop_loss_pct=0)


# ---------- 1. bars 부족 ----------

def test_bars_insufficient_returns_no_signal():
    s = VWAPStrategy(min_bars_required=25)
    bars = _flat_session(5)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.is_order_intent is False


# ---------- 2/3. 평균 거래량 / 거래대금 부족 → REJECT ----------

def test_low_avg_volume_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=10_000)  # 매우 큰 임계
    bars = _flat_session(30, volume=100)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("LOW_LIQUIDITY" in r for r in out.explanation.reasons)


def test_low_avg_turnover_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0, min_avg_turnover=100_000_000)
    bars = _flat_session(30, close=10, volume=100)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("LOW_LIQUIDITY" in r and "turnover" in r for r in out.explanation.reasons)


# ---------- 4. VWAP reclaim → BUY ----------

def test_vwap_reclaim_returns_buy():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=1.5)
    # baseline close=100. pre-reclaim close=99 (below VWAP≈100). reclaim close=102 (above).
    bars = _build_reclaim_pattern(
        n_baseline=25, baseline_close=100, baseline_volume=1000,
        pre_reclaim_close=99, pre_reclaim_volume=1000,
        reclaim_close=102, reclaim_volume=3000,
    )
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.BUY, out.explanation.reasons
    assert out.is_order_intent is False
    assert out.sizing_hint is not None
    assert out.exit_plan is not None
    assert out.explanation.confidence is not None
    assert 0 <= out.explanation.confidence <= 100


# ---------- 5. VWAP 위지만 과도한 이격 → REJECT ----------

def test_overextension_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=2.0,
                      overextension_deviation_pct=3.0)
    # baseline close=100, last bar close=110 → deviation ≈ 10% > 3% cap
    bars = _flat_session(25, close=100, volume=1000)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=25),
                     o=105, h=112, lo=105, c=110, v=3000))
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("괴리율" in r and "추격" in r for r in out.explanation.reasons)


# ---------- 6. VWAP 근처 → WATCH ----------

def test_near_vwap_returns_watch():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0)
    # 평탄 데이터 — close=VWAP 근처, reclaim 없음, 거래량 증가도 없음.
    bars = _flat_session(28, close=100, volume=1000)
    # 마지막 봉을 VWAP 살짝 아래로 (no reclaim cross-up)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=28),
                     o=99, h=100, lo=99, c=99, v=1000))
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.WATCH


# ---------- 7. VWAP 이탈 (loss) — position이 있을 때 EXIT ----------

def test_vwap_loss_returns_exit_when_position_open():
    s = VWAPStrategy(min_bars_required=10, open_cooldown_bars=2,
                      rolling_vwap_window=5, liquidity_window=5,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0)
    # Step 1: 봉 12개. typical=(100+99+100)/3=99.67, vwap≈99.67, close=100 > vwap → prev_above=True.
    bars1 = [_bar(_DAY1_OPEN + timedelta(minutes=i), o=100, h=100, lo=99, c=100, v=1000)
             for i in range(12)]
    out1 = s.generate_signal(_ctx(bars1, extra={"position_context": {"has_open_position": True}}))
    assert out1.action != SignalAction.EXIT
    assert s._prev_above_vwap is True

    # Step 2: 새 봉 close=95 (cross-down, clearly below VWAP).
    bars2 = bars1 + [_bar(_DAY1_OPEN + timedelta(minutes=12),
                           o=99, h=100, lo=94, c=95, v=2000)]
    out2 = s.generate_signal(_ctx(bars2, extra={"position_context": {"has_open_position": True}}))
    assert out2.action == SignalAction.EXIT
    assert out2.explanation.indicators.get("decision_kind") == "EXIT"


def test_vwap_loss_no_exit_without_position():
    s = VWAPStrategy(min_bars_required=10, open_cooldown_bars=2,
                      rolling_vwap_window=5, liquidity_window=5,
                      min_avg_volume=0)
    # Same prev_above=True setup, then cross-down without position
    bars1 = [_bar(_DAY1_OPEN + timedelta(minutes=i), o=100, h=100, lo=99, c=100, v=1000)
             for i in range(12)]
    s.generate_signal(_ctx(bars1))  # prime prev_above_vwap
    bars2 = bars1 + [_bar(_DAY1_OPEN + timedelta(minutes=12),
                           o=99, h=100, lo=94, c=95, v=2000)]
    out = s.generate_signal(_ctx(bars2))
    assert out.action != SignalAction.EXIT


# ---------- 8. 장 초반 cooldown ----------

def test_open_cooldown_rejects():
    s = VWAPStrategy(min_bars_required=3, open_cooldown_bars=20,
                      rolling_vwap_window=2, liquidity_window=2,
                      min_avg_volume=0)
    bars = _flat_session(5)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("open cooldown" in r for r in out.explanation.reasons)


# ---------- 9. blocked regime ----------

def test_blocked_regime_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0)
    bars = _build_reclaim_pattern()
    out = s.generate_signal(_ctx(bars, regime="trending_down"))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("blocked regime" in r for r in out.explanation.reasons)


# ---------- 10. stale data ----------

def test_stale_data_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      stale_max_age_seconds=30)
    bars = _build_reclaim_pattern()
    out = s.generate_signal(_ctx(bars, extra={"data_age_seconds": 120}))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("stale data" in r for r in out.explanation.reasons)


def test_stale_via_now_timestamp_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      stale_max_age_seconds=30)
    bars = _build_reclaim_pattern()
    last_ts = bars[-1].timestamp
    out = s.generate_signal(_ctx(bars, extra={"now": last_ts + timedelta(minutes=5)}))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"


# ---------- 11. volume 0 안전 처리 ----------

def test_zero_volume_current_bar_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0)
    bars = _flat_session(28, volume=1000)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=28),
                     o=100, h=100, lo=100, c=100, v=0))
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("volume == 0" in r for r in out.explanation.reasons)


def test_zero_session_volume_rejects():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0)
    bars = _flat_session(28, volume=0)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=28),
                     o=100, h=100, lo=100, c=100, v=0))
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"


# ---------- 12. reclaim 거래량 부족 → WATCH ----------

def test_reclaim_low_volume_returns_watch():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=3.0)
    # reclaim volume == prior avg (1.0x) < 3.0x 임계
    bars = _build_reclaim_pattern(reclaim_volume=1000)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.WATCH
    assert any("reclaim_volume_min_ratio" in r for r in out.explanation.reasons)


# ---------- 13. 일중 1회 진입 invariant ----------

def test_only_first_buy_fires_within_session():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=1.5)
    bars = _build_reclaim_pattern()
    out1 = s.generate_signal(_ctx(bars))
    assert out1.action == SignalAction.BUY

    # 두 번째 reclaim 시도 — fired_today로 차단
    last_ts = bars[-1].timestamp
    # 다시 VWAP 아래로 갔다가 위로
    bars.append(_bar(last_ts + timedelta(minutes=1),
                     o=100, h=101, lo=98, c=99, v=1000))
    bars.append(_bar(last_ts + timedelta(minutes=2),
                     o=99, h=104, lo=99, c=103, v=4000))
    out2 = s.generate_signal(_ctx(bars))
    assert out2.action != SignalAction.BUY


# ---------- 14. 일별 reset ----------

def test_resets_state_at_day_boundary():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=1.5)
    day1 = _build_reclaim_pattern(base_ts=_DAY1_OPEN)
    out1 = s.generate_signal(_ctx(day1))
    assert out1.action == SignalAction.BUY

    day2 = _build_reclaim_pattern(base_ts=_DAY2_OPEN, baseline_close=200,
                                  pre_reclaim_close=199, reclaim_close=204)
    out2 = s.generate_signal(_ctx(day1 + day2))
    assert out2.action == SignalAction.BUY


# ---------- 15. 직접 주문 invariant ----------

def test_strategy_does_not_import_broker_or_risk():
    import inspect

    from app.strategies.concrete import vwap_strategy as mod
    src = inspect.getsource(mod)
    forbidden = (
        "from app.brokers", "from app.risk", "from app.permission",
        "from app.execution", "from app.governance",
    )
    for f in forbidden:
        assert f not in src, f"forbidden import: {f}"


def test_vwap_util_does_not_import_broker_or_risk():
    import inspect

    from app.strategies import vwap as mod
    src = inspect.getsource(mod)
    forbidden = (
        "from app.brokers", "from app.risk", "from app.permission",
        "from app.execution", "from app.governance",
    )
    for f in forbidden:
        assert f not in src


def test_signal_dict_has_no_order_fields():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=1.5)
    bars = _build_reclaim_pattern()
    out = s.generate_signal(_ctx(bars))
    d = out.to_dict()
    forbidden = ("side", "order_type", "limit_price", "decision",
                 "broker_order_id", "client_order_id", "quantity_to_execute")
    for f in forbidden:
        assert f not in d
    assert d["is_order_intent"] is False


def test_signal_is_not_order_intent_for_all_branches():
    """모든 분기에서 is_order_intent=False."""
    s_short = VWAPStrategy(min_bars_required=99, min_avg_volume=0)
    out_n = s_short.generate_signal(_ctx(_flat_session(20)))
    assert out_n.action == SignalAction.NO_SIGNAL
    assert out_n.is_order_intent is False

    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=3.0)
    # WATCH — reclaim volume 부족
    bars_w = _build_reclaim_pattern(reclaim_volume=1000)
    out_w = s.generate_signal(_ctx(bars_w))
    assert out_w.is_order_intent is False

    # REJECT — overextension
    s_strict = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                              min_avg_volume=0,
                              max_deviation_pct_for_entry=2.0,
                              overextension_deviation_pct=3.0)
    bars_r = _flat_session(25, close=100)
    bars_r.append(_bar(_DAY1_OPEN + timedelta(minutes=25),
                       o=105, h=112, lo=105, c=110, v=3000))
    out_r = s_strict.generate_signal(_ctx(bars_r))
    assert out_r.is_order_intent is False
    assert out_r.explanation.indicators.get("decision_kind") == "REJECT"

    # BUY
    s_buy = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                          min_avg_volume=0,
                          max_deviation_pct_for_entry=5.0,
                          overextension_deviation_pct=10.0,
                          reclaim_volume_min_ratio=1.5)
    out_b = s_buy.generate_signal(_ctx(_build_reclaim_pattern()))
    assert out_b.is_order_intent is False
    if out_b.action == SignalAction.BUY:
        assert out_b.sizing_hint is not None
        assert out_b.exit_plan is not None


# ---------- 16. exit_rule / calculate_size ----------

def test_exit_rule_includes_vwap_invalidation():
    s = VWAPStrategy(stop_loss_pct=1.5, take_profit_pct=2.5,
                      trailing_pct=1.0, time_exit_bars=20)
    plan = s.exit_rule(None)
    assert plan.take_profit_pct == 2.5
    assert plan.stop_loss_pct == 1.5
    assert plan.time_exit_bars == 20
    assert "VWAP" in plan.invalidation
    assert "VWAP" in plan.rule_summary


def test_calculate_size_reduces_for_low_confidence():
    s = VWAPStrategy()
    base_pct = float(s.risk_profile["position_size_pct"])
    h_high = s.calculate_size(None, risk_context={"confidence": 80})
    h_low  = s.calculate_size(None, risk_context={"confidence": 30})
    assert h_high.position_size_pct == base_pct
    assert h_low.position_size_pct  < base_pct


def test_calculate_size_reduces_for_chasing_deviation():
    s = VWAPStrategy(max_deviation_pct_for_entry=2.0)
    base_pct = float(s.risk_profile["position_size_pct"])
    h = s.calculate_size(None, risk_context={
        "confidence":         80,
        "vwap_deviation_pct": 1.8,  # > 2.0 * 0.7 = 1.4
    })
    assert h.position_size_pct < base_pct
    assert h.note is not None


# ---------- 17. legacy on_bar ----------

def test_on_bar_returns_buy_for_reclaim():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0,
                      reclaim_volume_min_ratio=1.5)
    bars = _build_reclaim_pattern()
    sigs = [s.on_bar(bars[:i + 1]) for i in range(len(bars))]
    assert sigs[-1] == Signal.BUY


def test_on_bar_holds_on_flat_data():
    """평탄 데이터에서 어떤 BUY/SELL도 발생하지 않음 (registry 호환 — flat → HOLD 검증)."""
    s = VWAPStrategy()
    bars = _flat_session(50, close=100, volume=1000)
    for i in range(1, len(bars) + 1):
        sig = s.on_bar(bars[:i])
        assert sig == Signal.HOLD


# ---------- 18. metadata ----------

def test_metadata_is_complete():
    s = VWAPStrategy()
    assert s.entry
    assert s.exit
    assert s.invalidation
    assert s.required_regime != "any"
    assert s.risk_profile
    assert s.risk_profile["stop_loss_pct"] == 1.5
    assert s.risk_profile["take_profit_pct"] == 2.5


# ---------- 19. indicators carry session+rolling VWAP + deviation ----------

def test_indicators_include_session_and_rolling_vwap():
    s = VWAPStrategy(min_bars_required=25, open_cooldown_bars=3,
                      min_avg_volume=0,
                      max_deviation_pct_for_entry=5.0,
                      overextension_deviation_pct=10.0)
    bars = _flat_session(28, close=100, volume=1000)
    bars.append(_bar(_DAY1_OPEN + timedelta(minutes=28),
                     o=99, h=100, lo=99, c=99, v=1000))
    out = s.generate_signal(_ctx(bars))
    ind = out.explanation.indicators
    assert "session_vwap" in ind
    assert "rolling_vwap" in ind
    assert "vwap_deviation_pct" in ind
    assert "avg_volume" in ind
    assert "avg_turnover" in ind
