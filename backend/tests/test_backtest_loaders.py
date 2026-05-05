from datetime import datetime, timezone

import pytest

from app.backtest.loaders import load_bars_from_csv


def _write_csv(path, rows):
    path.write_text(
        "timestamp,open,high,low,close,volume\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def test_loads_well_formed_csv(tmp_path):
    p = tmp_path / "ohlcv.csv"
    _write_csv(p, [
        "2026-01-02T00:00:00+00:00,100,110,95,105,1000",
        "2026-01-03T00:00:00+00:00,105,115,100,112,1500",
    ])
    bars = load_bars_from_csv(p, symbol="005930")
    assert len(bars) == 2
    assert bars[0].symbol == "005930"
    assert bars[0].timestamp == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert bars[0].open == 100 and bars[0].close == 105
    assert bars[1].volume == 1500


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_bars_from_csv(tmp_path / "nope.csv", symbol="X")


def test_missing_required_column_raises(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("timestamp,open,high,low,close\n2026-01-01T00:00:00+00:00,1,2,3,4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        load_bars_from_csv(p, symbol="X")


def test_non_integer_price_raises(tmp_path):
    p = tmp_path / "float.csv"
    _write_csv(p, ["2026-01-02T00:00:00+00:00,100.5,110,95,105,1000"])
    with pytest.raises(ValueError):
        load_bars_from_csv(p, symbol="X")


def test_empty_csv_returns_empty_list(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    assert load_bars_from_csv(p, symbol="X") == []
