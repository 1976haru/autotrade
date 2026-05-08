"""MarketRegimeFilter + apply_regime_filter_to_signal 단위 테스트 (#32).

8개 regime + 4개 decision 분기 + BUY/SELL/EXIT 정책 + signal 변환 helper.
필터는 *주문을 생성하지 않는다* — 모든 결과 신호는 `is_order_intent=False`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar
from app.filters.market_regime import (
    MarketRegime,
    MarketRegimeFilter,
    RegimeDecisionKind,
    apply_regime_filter_to_signal,
)
from app.strategies.base import (
    ExitPlan,
    SignalAction,
    SignalExplanation,
    SizingHint,
    StrategySignal,
)


_DAY1_OPEN = datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc)


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
) -> list[Bar]:
    return [
        _bar(base_ts + timedelta(minutes=i),
             o=close, h=close + 1, lo=close - 1, c=close, v=volume)
        for i in range(n)
    ]


# ====================================================================
# 입력 검증
# ====================================================================

class TestParamValidation:
    def test_invalid_params(self):
        with pytest.raises(ValueError):
            MarketRegimeFilter(opening_chaos_bars=-1)
        with pytest.raises(ValueError):
            MarketRegimeFilter(liquidity_window=1)
        with pytest.raises(ValueError):
            MarketRegimeFilter(risk_off_drop_pct=0)
        with pytest.raises(ValueError):
            MarketRegimeFilter(risk_off_lookback_bars=1)
        with pytest.raises(ValueError):
            MarketRegimeFilter(high_vol_cv_pct=0)
        with pytest.raises(ValueError):
            MarketRegimeFilter(min_bars_required=1)
        with pytest.raises(ValueError):
            MarketRegimeFilter(reduce_size_multiplier=1.5)
        with pytest.raises(ValueError):
            MarketRegimeFilter(min_avg_volume=-1)


# ====================================================================
# 분류 / 결정
# ====================================================================


class TestRegimeClassification:
    def test_unknown_when_bars_insufficient(self):
        f = MarketRegimeFilter(min_bars_required=20)
        bars = _flat_session(5)
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.UNKNOWN
        assert d.decision == RegimeDecisionKind.WATCH_ONLY
        assert d.buy_allowed is False
        assert d.sell_allowed is True

    def test_opening_chaos(self):
        f = MarketRegimeFilter(min_bars_required=2, opening_chaos_bars=10,
                                 risk_off_lookback_bars=2, high_vol_window=2,
                                 liquidity_window=2)
        bars = _flat_session(5)
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.OPENING_CHAOS
        assert d.decision == RegimeDecisionKind.BLOCK_NEW_BUY
        assert d.buy_allowed is False
        assert d.sell_allowed is True
        assert d.size_multiplier == 0.0

    def test_low_liquidity_volume(self):
        f = MarketRegimeFilter(opening_chaos_bars=2,
                                 min_avg_volume=10_000,  # very high
                                 risk_off_lookback_bars=10, high_vol_window=10,
                                 liquidity_window=10, min_bars_required=10)
        bars = _flat_session(20, volume=100)
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.LOW_LIQUIDITY
        assert d.decision == RegimeDecisionKind.BLOCK_NEW_BUY
        assert d.buy_allowed is False

    def test_low_liquidity_turnover(self):
        f = MarketRegimeFilter(opening_chaos_bars=2,
                                 min_avg_turnover=10_000_000,
                                 risk_off_lookback_bars=10, high_vol_window=10,
                                 liquidity_window=10, min_bars_required=10)
        bars = _flat_session(20, close=10, volume=100)
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.LOW_LIQUIDITY

    def test_risk_off_index_drop(self):
        f = MarketRegimeFilter(opening_chaos_bars=2,
                                 risk_off_drop_pct=-2.0, risk_off_lookback_bars=10,
                                 high_vol_window=10, liquidity_window=10,
                                 min_bars_required=10, min_avg_volume=0)
        # 100 → 90 over 10 bars = -10%
        bars = []
        for i in range(11):
            c = 100 - i  # 100, 99, 98, ..., 90
            bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                              o=c, h=c + 1, lo=c - 1, c=c, v=1000))
        # Add some prefix to get past opening cooldown easily
        prefix = _flat_session(5, close=100, volume=1000)
        d = f.evaluate(prefix + bars)
        assert d.regime == MarketRegime.RISK_OFF
        assert d.decision == RegimeDecisionKind.BLOCK_NEW_BUY
        assert d.buy_allowed is False

    def test_high_volatility(self):
        f = MarketRegimeFilter(opening_chaos_bars=2, min_avg_volume=0,
                                 high_vol_cv_pct=2.0, high_vol_window=20,
                                 risk_off_drop_pct=-50.0,  # disable risk_off
                                 risk_off_lookback_bars=20,
                                 liquidity_window=20, min_bars_required=20)
        # High volatility — alternating closes 90/110
        bars = []
        for i in range(25):
            c = 90 if i % 2 == 0 else 110
            bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                              o=c, h=c + 1, lo=c - 1, c=c, v=1000))
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.HIGH_VOLATILITY
        assert d.decision == RegimeDecisionKind.REDUCE_SIZE
        assert d.buy_allowed is True
        assert d.size_multiplier < 1.0

    def test_trend_up_via_legacy_classifier(self):
        f = MarketRegimeFilter(opening_chaos_bars=2, min_avg_volume=0,
                                 risk_off_drop_pct=-50.0,
                                 high_vol_cv_pct=50.0,  # disable high_vol
                                 high_vol_window=10, liquidity_window=10,
                                 risk_off_lookback_bars=10, min_bars_required=10)
        # Smooth uptrend with high price base — CV stays below legacy regime.py
        # 1.5% threshold (closes [1000..1069], CV ≈ 0.5%).
        bars = []
        for i in range(70):
            c = 1000 + i
            bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                              o=c, h=c + 1, lo=c - 1, c=c, v=1000))
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.TREND_UP
        assert d.decision == RegimeDecisionKind.ALLOW
        assert d.buy_allowed is True
        assert d.size_multiplier == 1.0

    def test_trend_down_via_legacy_classifier(self):
        # 동일한 이유로 high price base + smooth descent.
        f = MarketRegimeFilter(opening_chaos_bars=2, min_avg_volume=0,
                                 risk_off_drop_pct=-50.0,
                                 high_vol_cv_pct=50.0,
                                 high_vol_window=10, liquidity_window=10,
                                 risk_off_lookback_bars=10, min_bars_required=10)
        bars = []
        for i in range(70):
            c = 2000 - i  # downtrend ~3.5% over 70 bars, CV in window ≈ 0.18%
            bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                              o=c, h=c + 1, lo=c - 1, c=c, v=1000))
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.TREND_DOWN
        assert d.decision == RegimeDecisionKind.WATCH_ONLY
        assert d.buy_allowed is False
        assert d.sell_allowed is True

    def test_choppy_via_legacy_classifier(self):
        f = MarketRegimeFilter(opening_chaos_bars=2, min_avg_volume=0,
                                 risk_off_drop_pct=-50.0,
                                 high_vol_cv_pct=50.0,
                                 high_vol_window=10, liquidity_window=10,
                                 risk_off_lookback_bars=10, min_bars_required=10)
        # All same price — ranging
        bars = _flat_session(70, close=100, volume=1000)
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.CHOPPY
        assert d.decision == RegimeDecisionKind.REDUCE_SIZE


# ====================================================================
# regime_override
# ====================================================================

class TestRegimeOverride:
    def test_override_skips_classification(self):
        """운영자가 외부 지수 데이터로 RISK_OFF를 강제 주입할 수 있다."""
        f = MarketRegimeFilter()
        bars = _flat_session(50, close=100, volume=1000)
        d = f.evaluate(bars, regime_override=MarketRegime.RISK_OFF)
        assert d.regime == MarketRegime.RISK_OFF
        assert d.decision == RegimeDecisionKind.BLOCK_NEW_BUY

    def test_override_unknown(self):
        f = MarketRegimeFilter()
        bars = _flat_session(50)
        d = f.evaluate(bars, regime_override=MarketRegime.UNKNOWN)
        assert d.regime == MarketRegime.UNKNOWN
        assert d.decision == RegimeDecisionKind.WATCH_ONLY


# ====================================================================
# 정책 override
# ====================================================================

class TestPolicyOverride:
    def test_custom_policy_override(self):
        """운영자가 universe 별로 더 tight한 정책을 원할 때 override."""
        f = MarketRegimeFilter(
            opening_chaos_bars=2, min_avg_volume=0,
            risk_off_drop_pct=-50.0, high_vol_cv_pct=50.0,
            high_vol_window=10, liquidity_window=10,
            risk_off_lookback_bars=10, min_bars_required=10,
            regime_policy={
                MarketRegime.CHOPPY: RegimeDecisionKind.BLOCK_NEW_BUY,  # tighter
                MarketRegime.TREND_UP: RegimeDecisionKind.ALLOW,
                MarketRegime.UNKNOWN: RegimeDecisionKind.WATCH_ONLY,
                MarketRegime.HIGH_VOLATILITY: RegimeDecisionKind.WATCH_ONLY,
                MarketRegime.LOW_LIQUIDITY: RegimeDecisionKind.BLOCK_NEW_BUY,
                MarketRegime.RISK_OFF: RegimeDecisionKind.BLOCK_NEW_BUY,
                MarketRegime.OPENING_CHAOS: RegimeDecisionKind.BLOCK_NEW_BUY,
                MarketRegime.TREND_DOWN: RegimeDecisionKind.BLOCK_NEW_BUY,
            },
        )
        bars = _flat_session(70, close=100, volume=1000)
        d = f.evaluate(bars)
        assert d.regime == MarketRegime.CHOPPY
        assert d.decision == RegimeDecisionKind.BLOCK_NEW_BUY


# ====================================================================
# RegimeDecision serialization
# ====================================================================

class TestRegimeDecisionSerialization:
    def test_to_dict_round_trips(self):
        f = MarketRegimeFilter(min_bars_required=10)
        d = f.evaluate(_flat_session(50, close=100, volume=1000))
        out = d.to_dict()
        assert "regime" in out
        assert "decision" in out
        assert out["buy_allowed"] is d.buy_allowed
        assert out["sell_allowed"] is True
        assert "indicators" in out


# ====================================================================
# apply_regime_filter_to_signal
# ====================================================================


def _buy_signal(*, position_size_pct: float = 5.0) -> StrategySignal:
    return StrategySignal(
        action=SignalAction.BUY,
        symbol="X",
        sizing_hint=SizingHint(position_size_pct=position_size_pct, risk_pct=2.0),
        exit_plan=ExitPlan(take_profit_pct=4.0, stop_loss_pct=2.0),
        explanation=SignalExplanation(
            summary="strategy → BUY", reasons=["original reason"],
            confidence=70, indicators={"x": 1},
        ),
        is_order_intent=False,
    )


def _make_decision(regime: MarketRegime, *, kind: RegimeDecisionKind | None = None,
                   size_mult: float = 0.5) -> "object":
    """Lightweight helper — construct a RegimeDecision via filter for default policy."""
    f = MarketRegimeFilter(reduce_size_multiplier=size_mult)
    if kind is not None:
        f = MarketRegimeFilter(
            reduce_size_multiplier=size_mult,
            regime_policy={r: kind for r in MarketRegime},
        )
    return f.evaluate(_flat_session(50, close=100, volume=1000),
                       regime_override=regime)


class TestApplyRegimeFilter:
    def test_none_signal_returns_none(self):
        d = _make_decision(MarketRegime.RISK_OFF)
        assert apply_regime_filter_to_signal(None, d) is None

    def test_allow_passes_signal_through_unchanged(self):
        d = _make_decision(MarketRegime.TREND_UP)
        sig = _buy_signal()
        out = apply_regime_filter_to_signal(sig, d)
        assert out is sig  # unchanged
        assert out.action == SignalAction.BUY

    def test_block_new_buy_demotes_buy_to_no_signal(self):
        d = _make_decision(MarketRegime.RISK_OFF)
        sig = _buy_signal()
        out = apply_regime_filter_to_signal(sig, d)
        assert out is not None
        assert out.action == SignalAction.NO_SIGNAL
        assert out.is_order_intent is False
        # reasons에 차단 사유
        assert any("market regime filter" in r for r in out.explanation.reasons)
        assert any("BLOCK_NEW_BUY" in r for r in out.explanation.reasons)
        assert out.explanation.indicators.get("decision_kind") == "REJECT"
        assert "regime_filter" in out.explanation.indicators

    def test_watch_only_demotes_buy_to_watch(self):
        d = _make_decision(MarketRegime.TREND_DOWN)
        sig = _buy_signal()
        out = apply_regime_filter_to_signal(sig, d)
        assert out.action == SignalAction.WATCH
        assert any("WATCH_ONLY" in r for r in out.explanation.reasons)
        assert out.explanation.indicators.get("decision_kind") == "WATCH"

    def test_reduce_size_keeps_buy_but_scales_sizing(self):
        d = _make_decision(MarketRegime.HIGH_VOLATILITY, size_mult=0.4)
        sig = _buy_signal(position_size_pct=5.0)
        out = apply_regime_filter_to_signal(sig, d)
        assert out.action == SignalAction.BUY  # unchanged
        assert out.sizing_hint is not None
        assert out.sizing_hint.position_size_pct == pytest.approx(5.0 * 0.4)
        assert out.sizing_hint.note is not None
        assert "REDUCE_SIZE" in out.sizing_hint.note
        assert any("REDUCE_SIZE" in r for r in out.explanation.reasons)

    def test_reduce_size_handles_missing_sizing(self):
        d = _make_decision(MarketRegime.HIGH_VOLATILITY, size_mult=0.4)
        sig = StrategySignal(action=SignalAction.BUY, symbol="X")  # no sizing
        out = apply_regime_filter_to_signal(sig, d)
        assert out.action == SignalAction.BUY
        # sizing_hint=None 그대로 유지
        assert out.sizing_hint is None

    def test_sell_passes_through_even_on_block(self):
        """SELL은 리스크 축소 — regime 차단해도 통과."""
        d = _make_decision(MarketRegime.RISK_OFF)
        sig = StrategySignal(action=SignalAction.SELL, symbol="X")
        out = apply_regime_filter_to_signal(sig, d)
        assert out is sig

    def test_exit_passes_through_even_on_block(self):
        """EXIT은 청산 권고 — regime 차단해도 통과."""
        d = _make_decision(MarketRegime.RISK_OFF)
        sig = StrategySignal(action=SignalAction.EXIT, symbol="X")
        out = apply_regime_filter_to_signal(sig, d)
        assert out is sig

    def test_watch_signal_passes_through(self):
        """이미 WATCH인 signal은 그대로."""
        d = _make_decision(MarketRegime.RISK_OFF)
        sig = StrategySignal(action=SignalAction.WATCH, symbol="X")
        out = apply_regime_filter_to_signal(sig, d)
        assert out.action == SignalAction.WATCH

    def test_no_signal_passes_through(self):
        d = _make_decision(MarketRegime.RISK_OFF)
        sig = StrategySignal(action=SignalAction.NO_SIGNAL, symbol="X")
        out = apply_regime_filter_to_signal(sig, d)
        assert out.action == SignalAction.NO_SIGNAL

    def test_is_order_intent_remains_false_after_transform(self):
        """모든 변환 분기에서 is_order_intent=False invariant."""
        for regime in MarketRegime:
            d = _make_decision(regime)
            for action in SignalAction:
                sig = StrategySignal(action=action, symbol="X")
                out = apply_regime_filter_to_signal(sig, d)
                assert out is None or out.is_order_intent is False


# ====================================================================
# 직접 주문 invariant
# ====================================================================

class TestNoOrderImports:
    def test_filter_module_does_not_import_broker_or_risk(self):
        import inspect

        from app.filters import market_regime as mod
        src = inspect.getsource(mod)
        forbidden = (
            "from app.brokers", "from app.risk", "from app.permission",
            "from app.execution", "from app.governance",
        )
        for f in forbidden:
            assert f not in src, f"forbidden import: {f}"

    def test_filters_init_does_not_import_broker_or_risk(self):
        import inspect

        from app import filters as mod
        src = inspect.getsource(mod)
        forbidden = (
            "from app.brokers", "from app.risk", "from app.permission",
            "from app.execution", "from app.governance",
        )
        for f in forbidden:
            assert f not in src, f"forbidden import: {f}"

    def test_apply_signal_is_pure(self):
        """apply_regime_filter_to_signal은 외부 side effect 없이 신호만 변환."""
        d = _make_decision(MarketRegime.RISK_OFF)
        sig = _buy_signal()
        original_dict = sig.to_dict()
        apply_regime_filter_to_signal(sig, d)
        # 원본 sig는 frozen이라 변경 불가능 — to_dict가 변하지 않았는지 확인.
        assert sig.to_dict() == original_dict


# ====================================================================
# 기존 regime.py 호환성
# ====================================================================

class TestLegacyRegimeCompat:
    def test_legacy_classify_regime_still_works(self):
        """기존 app.market.regime.classify_regime은 그대로 동작 — 본 PR이 깨지 않음."""
        from app.market.regime import classify_regime
        bars = _flat_session(30)
        # 어떤 string이든 반환해야 (any/ranging/etc.)
        result = classify_regime(bars)
        assert isinstance(result, str)

    def test_filter_uses_classify_regime_internally(self):
        """필터는 classify_regime 결과를 매핑해 MarketRegime으로 변환."""
        f = MarketRegimeFilter(opening_chaos_bars=2, min_avg_volume=0,
                                 risk_off_drop_pct=-50.0, high_vol_cv_pct=50.0,
                                 high_vol_window=10, liquidity_window=10,
                                 risk_off_lookback_bars=10, min_bars_required=10)
        bars = _flat_session(50, close=100, volume=1000)
        d = f.evaluate(bars)
        # legacy_regime indicator should be carried.
        assert "legacy_regime" in d.indicators
