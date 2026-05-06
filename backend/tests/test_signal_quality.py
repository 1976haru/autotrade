"""Unit tests for signal quality scoring (136, MUST)."""

from datetime import datetime, timedelta, timezone

from app.backtest.types import Bar, Signal
from app.strategies.quality import signal_quality


def _bars(closes):
    base = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    return [
        Bar(symbol="X", timestamp=base + timedelta(minutes=i),
            open=int(c), high=int(c)+1, low=int(c)-1, close=int(c), volume=10)
        for i, c in enumerate(closes)
    ]


def test_hold_signal_yields_zero_quality():
    bars = _bars([100] * 60 + [102] * 20)
    q = signal_quality(bars, Signal.HOLD, regime_matches=True)
    assert q == {"strength": 0, "confidence": 100} or q["strength"] == 0


def test_buy_with_strong_trend_high_strength():
    # 60봉 평탄 100, 20봉 평탄 110 → SMA gap ~9.4% → strength near 100.
    bars = _bars([100] * 60 + [110] * 20)
    q = signal_quality(bars, Signal.BUY, regime_matches=True)
    assert q["strength"] >= 80


def test_buy_with_weak_trend_low_strength():
    bars = _bars([100] * 60 + [100] * 20)
    q = signal_quality(bars, Signal.BUY, regime_matches=True)
    assert q["strength"] == 0


def test_confidence_penalizes_short_history():
    bars = _bars([100] * 25)  # 25 bars only — not enough for full credit
    q = signal_quality(bars, Signal.BUY, regime_matches=True)
    assert q["confidence"] < 100


def test_confidence_full_when_full_history_matched_and_low_vol():
    bars = _bars([100] * 60 + [102] * 20)  # low vol, plenty of history
    q = signal_quality(bars, Signal.BUY, regime_matches=True)
    assert q["confidence"] == 100


def test_confidence_drops_when_regime_mismatched():
    bars = _bars([100] * 60 + [102] * 20)
    matched   = signal_quality(bars, Signal.BUY, regime_matches=True)
    mismatched = signal_quality(bars, Signal.BUY, regime_matches=False)
    assert mismatched["confidence"] < matched["confidence"]


def test_confidence_drops_when_high_volatility():
    # Highly volatile closes
    closes = [100, 110, 90, 115, 85, 120, 80, 125, 75, 130,
              70, 135, 65, 140, 60, 145, 55, 150, 50, 155] * 4
    bars = _bars(closes)
    q = signal_quality(bars, Signal.BUY, regime_matches=True)
    # Volatility hit drops confidence below 100 (no vol bonus).
    assert q["confidence"] < 100


def test_empty_bars_yields_zero_confidence():
    q = signal_quality([], Signal.BUY, regime_matches=True)
    assert q == {"strength": 0, "confidence": 0}


def test_returns_int_values_clamped_to_0_100():
    bars = _bars([100] * 60 + [200] * 20)  # extreme gap
    q = signal_quality(bars, Signal.BUY, regime_matches=True)
    for k in ("strength", "confidence"):
        assert isinstance(q[k], int)
        assert 0 <= q[k] <= 100
