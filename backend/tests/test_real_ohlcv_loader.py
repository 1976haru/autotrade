"""REAL-DATA-STRATEGY-01 — real_ohlcv_loader + ohlcv_quality 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.market_data.ohlcv_quality import (
    FAIL,
    OK,
    WARN,
    OhlcvQualityReport,
    check_ohlcv_quality,
    to_dict,
)
from app.market_data.real_ohlcv_loader import (
    SRC_NONE,
    LoadedOhlcv,
    kis_historical_supported,
    load_from_csv,
    load_from_dir,
)

_REPO = Path(__file__).resolve().parents[2]
_DEMO = _REPO / "backend" / "tests" / "fixtures" / "real_data" / "demo_quasi_real.csv"
_BROKEN = _REPO / "backend" / "tests" / "fixtures" / "real_data" / "005930.csv"
_SAMPLE = _REPO / "backend" / "tests" / "fixtures" / "backtest" / "sample_ohlcv.csv"


class _Bar:
    def __init__(self, symbol, timestamp, o, h, lo, c, v):
        self.symbol, self.timestamp = symbol, timestamp
        self.open, self.high, self.low, self.close, self.volume = o, h, lo, c, v


def _mkbars(n, *, bad=False, dup=False, neg_vol=False):
    from datetime import datetime, timedelta
    bars = []
    base = datetime(2025, 1, 1, 9, 0, 0)
    for i in range(n):
        ts = base + timedelta(days=i)
        o, h, lo, c = 100.0, 105.0, 98.0, 102.0
        if bad and i == 0:
            h = 90.0  # high < open/close → bad OHLC
        v = -1.0 if (neg_vol and i == 0) else 1000.0
        bars.append(_Bar("X", ts, o, h, lo, c, v))
    if dup and bars:
        bars.append(_Bar("X", bars[0].timestamp, 100.0, 105.0, 98.0, 102.0, 1000.0))
    return bars


# --------------------------- loader ----------------------------------------

def test_kis_historical_not_supported():
    assert kis_historical_supported() is False


def test_loaded_guard_rejects_kis_historical():
    with pytest.raises(ValueError):
        LoadedOhlcv((), "NONE", False, False, kis_historical_available=True)


def test_load_demo_csv_is_real_data():
    lo = load_from_csv(_DEMO)
    assert lo.real_data_used is True
    assert lo.sample_fixture_only is False
    assert lo.data_source == "CSV_REAL_FIXTURE"
    assert len(lo.bars) >= 100
    assert lo.symbols


def test_load_sample_csv_is_sample_only():
    lo = load_from_csv(_SAMPLE)
    assert lo.sample_fixture_only is True
    assert lo.real_data_used is False
    assert lo.data_source == "SAMPLE_FIXTURE"


def test_load_missing_csv_returns_none_source():
    lo = load_from_csv(_REPO / "nope_does_not_exist.csv")
    assert lo.data_source == SRC_NONE
    assert lo.bars == ()


def test_load_from_dir_reads_real_fixtures():
    lo = load_from_dir(_REPO / "backend" / "tests" / "fixtures" / "real_data")
    assert len(lo.bars) > 0
    assert lo.real_data_used is True


# --------------------------- quality ---------------------------------------

def test_quality_empty_is_fail():
    r = check_ohlcv_quality([])
    assert r.status == FAIL
    assert r.sufficient_for_backtest is False


def test_quality_required_columns_via_loaded_demo_ok():
    lo = load_from_csv(_DEMO)
    r = check_ohlcv_quality(list(lo.bars), min_bars=100, min_days=28)
    assert r.status == OK
    assert r.meets_min_sample is True
    assert r.sufficient_for_backtest is True


def test_quality_detects_bad_ohlc():
    r = check_ohlcv_quality(_mkbars(120, bad=True))
    assert r.status == FAIL
    assert r.issues.get("bad_ohlc", 0) >= 1
    assert r.sufficient_for_backtest is False


def test_quality_detects_bad_ohlc_in_real_fixture():
    """005930.csv 에 잘못된 OHLC(open>high) 가 있어 FAIL 로 검출."""
    lo = load_from_csv(_BROKEN)
    r = check_ohlcv_quality(list(lo.bars))
    assert r.status == FAIL
    assert r.issues.get("bad_ohlc", 0) >= 1


def test_quality_detects_duplicate_timestamp():
    r = check_ohlcv_quality(_mkbars(120, dup=True))
    assert r.issues.get("duplicate_timestamp", 0) >= 1
    assert r.status in (WARN, FAIL)


def test_quality_detects_negative_volume():
    r = check_ohlcv_quality(_mkbars(120, neg_vol=True))
    assert r.status == FAIL
    assert r.issues.get("negative_volume", 0) >= 1


def test_quality_insufficient_bars_is_warn():
    r = check_ohlcv_quality(_mkbars(10), min_bars=100, min_days=28)
    assert r.status == WARN
    assert r.meets_min_sample is False
    assert r.sufficient_for_backtest is True  # 무결하지만 표본 부족


def test_quality_insufficient_days_is_warn():
    # 120 bars 지만 모두 같은 날 → days=1 < 28.
    from datetime import datetime
    same = datetime(2025, 1, 1, 9, 0, 0)
    bars = [_Bar("X", same, 100.0, 105.0, 98.0, 102.0, 1000.0) for _ in range(120)]
    r = check_ohlcv_quality(bars, min_bars=100, min_days=28)
    # 중복 timestamp 도 잡히지만 days 부족이 핵심.
    assert r.day_count == 1
    assert r.meets_min_sample is False


def test_quality_to_dict_keys_and_no_secret():
    r = check_ohlcv_quality(_mkbars(120))
    d = to_dict(r)
    for k in ("status", "bar_count", "day_count", "symbol_count", "issues",
              "sufficient_for_backtest", "meets_min_sample", "contains_secret"):
        assert k in d
    assert d["contains_secret"] is False


def test_quality_report_guard_contains_secret():
    with pytest.raises(ValueError):
        OhlcvQualityReport(status=OK, bar_count=1, day_count=1, symbol_count=1,
                           contains_secret=True)


def test_loader_module_no_forbidden_imports():
    import re
    src = (_REPO / "backend" / "app" / "market_data" / "real_ohlcv_loader.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.execution", "order_router", "OrderExecutor",
                        "anthropic", "openai"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src, f"forbidden call: {call}"
