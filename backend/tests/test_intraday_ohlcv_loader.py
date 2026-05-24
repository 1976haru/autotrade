"""INTRADAY-DATA-01 — intraday_ohlcv 품질/로더 테스트."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.market_data.intraday_ohlcv import (
    FAIL,
    OK,
    WARN,
    IntradayQualityReport,
    check_intraday_quality,
    load_intraday_csv,
)

_REPO = Path(__file__).resolve().parents[2]
_INTRADAY = _REPO / "backend" / "tests" / "fixtures" / "intraday_clean"
_DAILY = _REPO / "backend" / "tests" / "fixtures" / "real_data_clean"

_KST = timezone(timedelta(hours=9))


class _Bar:
    def __init__(self, symbol, ts, o, h, lo, c, v):
        self.symbol, self.timestamp = symbol, ts
        self.open, self.high, self.low, self.close, self.volume = o, h, lo, c, v


def _intraday_bars(days=6, per_day=80, *, bad=False, out_session=False, dup=False):
    bars = []
    d = datetime(2024, 3, 4, 9, 0, 0, tzinfo=_KST)
    for _ in range(days):
        t = d.replace(hour=9, minute=0)
        for i in range(per_day):
            o, h, lo, c = 100.0, 105.0, 98.0, 102.0
            ts = t
            if out_session and i == 0:
                ts = t.replace(hour=8, minute=0)  # 장 전
            if bad and i == 0:
                h = 90.0  # high<open invalid
            bars.append(_Bar("X", ts, o, h, lo, c, 1000.0))
            t += timedelta(minutes=5)
        d += timedelta(days=1)
    if dup and bars:
        bars.append(_Bar("X", bars[0].timestamp, 100.0, 105.0, 98.0, 102.0, 1000.0))
    return bars


# --------------------------- loader ----------------------------------------

def test_load_intraday_csv_parses_datetime():
    bars, meta = load_intraday_csv(str(_INTRADAY / "005930.csv"))
    assert len(bars) > 100
    assert hasattr(bars[0].timestamp, "timestamp")  # datetime
    assert meta["symbols"] == ["005930"]


# --------------------------- quality ---------------------------------------

def test_clean_intraday_fixture_passes():
    bars, _ = load_intraday_csv(str(_INTRADAY / "000660.csv"))
    r = check_intraday_quality(bars, min_bars=100, min_days=5)
    assert r.status in (OK, WARN)
    assert r.intraday_detected is True
    assert r.bars_per_day >= 5
    assert r.bar_size_minutes == 5.0
    assert r.sufficient_for_backtest is True


def test_daily_data_is_fail_not_intraday():
    """일봉(1 bar/day)은 분봉 아님 → FAIL."""
    bars, _ = load_intraday_csv(str(_DAILY / "005930.csv"))
    r = check_intraday_quality(bars, min_bars=100, min_days=5)
    assert r.status == FAIL
    assert r.intraday_detected is False
    assert r.sufficient_for_backtest is False


def test_empty_is_fail():
    r = check_intraday_quality([])
    assert r.status == FAIL
    assert r.sufficient_for_backtest is False


def test_bad_ohlc_is_fail():
    r = check_intraday_quality(_intraday_bars(bad=True), min_bars=100, min_days=5)
    assert r.status == FAIL
    assert r.issues.get("bad_ohlc", 0) >= 1


def test_out_of_session_is_warn():
    r = check_intraday_quality(_intraday_bars(out_session=True), min_bars=100, min_days=5)
    assert r.out_of_session >= 1
    assert r.status in (WARN, FAIL)
    assert "장중" in " ".join(r.reasons)


def test_duplicate_timestamp_warn():
    r = check_intraday_quality(_intraday_bars(dup=True), min_bars=100, min_days=5)
    assert r.issues.get("duplicate_timestamp", 0) >= 1


def test_insufficient_bars_warn():
    r = check_intraday_quality(_intraday_bars(days=1, per_day=20), min_bars=100, min_days=5)
    assert r.status == WARN
    assert r.meets_min_sample is False


def test_quality_to_dict_and_guard():
    from app.market_data.intraday_ohlcv import quality_to_dict
    r = check_intraday_quality(_intraday_bars(), min_bars=100, min_days=5)
    d = quality_to_dict(r)
    for k in ("status", "bar_count", "bars_per_day", "bar_size_minutes",
              "intraday_detected", "out_of_session", "contains_secret"):
        assert k in d
    assert d["contains_secret"] is False
    with pytest.raises(ValueError):
        IntradayQualityReport(OK, 1, 1, 80.0, 5.0, True, 0, contains_secret=True)


def test_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "market_data" / "intraday_ohlcv.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.execution", "order_router", "OrderExecutor", "anthropic", "openai"):
                assert mod not in ln
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src
