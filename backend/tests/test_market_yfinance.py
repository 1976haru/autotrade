import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("yfinance")

from app.market.base import Interval  # noqa: E402
from app.market.yfinance_adapter import YfinanceMarketData  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _df(rows: list[tuple]) -> "pd.DataFrame":
    """rows: list of (timestamp, open, high, low, close, volume)."""
    return pd.DataFrame(
        [{"Open": o, "High": h, "Low": low, "Close": c, "Volume": v} for _, o, h, low, c, v in rows],
        index=pd.DatetimeIndex([ts for ts, *_ in rows]),
    )


def test_six_digit_korean_code_gets_default_suffix():
    a = YfinanceMarketData()
    assert a._yahoo_ticker("005930") == "005930.KS"


def test_existing_dotted_ticker_passes_through():
    a = YfinanceMarketData()
    assert a._yahoo_ticker("005930.KQ") == "005930.KQ"


def test_non_korean_ticker_passes_through():
    a = YfinanceMarketData()
    assert a._yahoo_ticker("AAPL") == "AAPL"


def test_custom_default_suffix():
    a = YfinanceMarketData(default_suffix=".KQ")
    assert a._yahoo_ticker("247540") == "247540.KQ"


def test_start_after_end_returns_empty_without_calling_yfinance():
    bars = run(YfinanceMarketData().get_bars(
        "005930",
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 1, tzinfo=timezone.utc),
    ))
    assert bars == []


def test_unsupported_interval_raises():
    class FakeInterval:
        value = "weekly"
    with pytest.raises(ValueError, match="unsupported interval"):
        run(YfinanceMarketData().get_bars(
            "005930",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            interval=FakeInterval(),
        ))


def test_dataframe_converted_to_int_priced_bars():
    df = _df([
        (datetime(2026, 1, 1, tzinfo=timezone.utc), 100.7, 110.3, 95.1, 105.9, 1000),
        (datetime(2026, 1, 2, tzinfo=timezone.utc), 106.0, 112.5, 102.0, 110.4, 1500),
    ])
    with patch("yfinance.Ticker") as MockTicker:
        instance = MagicMock()
        instance.history.return_value = df
        MockTicker.return_value = instance

        bars = run(YfinanceMarketData().get_bars(
            "005930",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        ))

    assert MockTicker.call_args.args[0] == "005930.KS"
    assert len(bars) == 2
    assert bars[0].symbol == "005930"
    assert bars[0].open  == 101  # rounded from 100.7
    assert bars[0].high  == 110  # rounded from 110.3
    assert bars[0].low   == 95   # rounded from 95.1
    assert bars[0].close == 106  # rounded from 105.9
    assert bars[0].volume == 1000
    assert bars[1].close == 110  # rounded from 110.4


def test_empty_dataframe_returns_empty_list():
    with patch("yfinance.Ticker") as MockTicker:
        instance = MagicMock()
        instance.history.return_value = pd.DataFrame()
        MockTicker.return_value = instance

        bars = run(YfinanceMarketData().get_bars(
            "005930",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
        ))
    assert bars == []


def test_naive_dataframe_index_normalized_to_utc():
    df = pd.DataFrame(
        [{"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 100}],
        index=pd.DatetimeIndex([datetime(2026, 1, 1)]),
    )
    with patch("yfinance.Ticker") as MockTicker:
        instance = MagicMock()
        instance.history.return_value = df
        MockTicker.return_value = instance

        bars = run(YfinanceMarketData().get_bars(
            "005930",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
    assert bars[0].timestamp.tzinfo == timezone.utc


def test_kst_timestamps_converted_to_utc():
    kst = timezone(__import__("datetime").timedelta(hours=9))
    df = pd.DataFrame(
        [{"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 100}],
        index=pd.DatetimeIndex([datetime(2026, 1, 2, 9, 0, tzinfo=kst)]),  # 09:00 KST
    )
    with patch("yfinance.Ticker") as MockTicker:
        instance = MagicMock()
        instance.history.return_value = df
        MockTicker.return_value = instance

        bars = run(YfinanceMarketData().get_bars(
            "005930",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 3, tzinfo=timezone.utc),
        ))
    # 09:00 KST = 00:00 UTC
    assert bars[0].timestamp == datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)


def test_end_passed_to_yfinance_is_exclusive_one_interval_later():
    df = pd.DataFrame()
    with patch("yfinance.Ticker") as MockTicker:
        instance = MagicMock()
        instance.history.return_value = df
        MockTicker.return_value = instance

        run(YfinanceMarketData().get_bars(
            "005930",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            interval=Interval.DAY_1,
        ))
    end_arg = instance.history.call_args.kwargs["end"]
    assert end_arg == datetime(2026, 1, 6, tzinfo=timezone.utc)
