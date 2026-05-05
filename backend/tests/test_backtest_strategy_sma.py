from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.strategies.sma_crossover import SmaCrossoverStrategy
from app.backtest.types import Bar, Signal


def _bars(closes: list[int]) -> list[Bar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(symbol="X", timestamp=base + timedelta(days=i),
            open=c, high=c, low=c, close=c, volume=1)
        for i, c in enumerate(closes)
    ]


def test_holds_until_long_window_filled():
    s = SmaCrossoverStrategy(short=2, long=4)
    bars = _bars([100, 101, 102])
    for i in range(len(bars)):
        assert s.on_bar(bars[: i + 1]) == Signal.HOLD


def test_first_compare_after_warmup_is_baseline_hold():
    s = SmaCrossoverStrategy(short=2, long=4)
    bars = _bars([100, 99, 98, 97])
    signals = [s.on_bar(bars[: i + 1]) for i in range(len(bars))]
    assert signals == [Signal.HOLD, Signal.HOLD, Signal.HOLD, Signal.HOLD]


def test_buy_emitted_on_upward_crossover():
    s = SmaCrossoverStrategy(short=2, long=4)
    closes = [100, 99, 98, 97, 100, 105, 110]
    bars = _bars(closes)
    signals = [s.on_bar(bars[: i + 1]) for i in range(len(bars))]
    assert Signal.BUY in signals[4:]
    assert Signal.SELL not in signals


def test_sell_emitted_on_downward_crossover():
    s = SmaCrossoverStrategy(short=2, long=4)
    closes = [100, 105, 110, 115, 110, 100, 95]
    bars = _bars(closes)
    signals = [s.on_bar(bars[: i + 1]) for i in range(len(bars))]
    assert Signal.SELL in signals[4:]


def test_invalid_periods_rejected():
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(short=5, long=5)
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(short=10, long=5)
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(short=0, long=5)
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(short=2, long=0)
