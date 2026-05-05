import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.market.base import Interval
from app.market.mock import MockMarketData


def run(coro):
    return asyncio.run(coro)


def test_returns_one_bar_per_day_inclusive():
    m = MockMarketData()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, tzinfo=timezone.utc)
    bars = run(m.get_bars("005930", start, end))
    assert len(bars) == 5
    assert bars[0].timestamp == start
    assert bars[-1].timestamp == end


def test_output_is_deterministic_across_calls():
    m1 = MockMarketData()
    m2 = MockMarketData()
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 7, tzinfo=timezone.utc)
    a = run(m1.get_bars("005930", start, end))
    b = run(m2.get_bars("005930", start, end))
    assert a == b


def test_known_symbol_centered_on_base_price():
    m = MockMarketData()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = run(m.get_bars("005930", start, start + timedelta(days=30)))
    avg = sum(b.close for b in bars) / len(bars)
    assert 70_000 <= avg <= 80_000  # base 75_000 ± 5_000 잡음


def test_unknown_symbol_uses_default_base():
    m = MockMarketData()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = run(m.get_bars("ZZZZZZ", start, start + timedelta(days=10)))
    for b in bars:
        assert 40_000 <= b.close <= 60_000  # default 50_000 ± 5_000


def test_high_low_bracket_close():
    m = MockMarketData()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = run(m.get_bars("005930", start, start + timedelta(days=5)))
    for b in bars:
        assert b.low <= b.close <= b.high


def test_start_after_end_returns_empty():
    m = MockMarketData()
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert run(m.get_bars("005930", start, end)) == []


def test_unsupported_interval_raises():
    m = MockMarketData()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="daily interval"):
        run(m.get_bars("005930", start, start, interval=Interval.HOUR_1))
