"""INTRADAY-DATA-02 — intraday_csv_normalizer 테스트 (한글 컬럼/쉼표/드롭)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.market_data.intraday_csv_normalizer import (
    NormalizeReport,
    bar_size_from_filename,
    build_column_mapping,
    normalize_intraday_csv,
    normalize_rows,
)

_REPO = Path(__file__).resolve().parents[2]


def test_korean_columns_mapped():
    m = build_column_mapping(["일자", "시가", "고가", "저가", "종가", "거래량"])
    assert set(m.values()) == {"timestamp", "open", "high", "low", "close", "volume"}


def test_english_columns_passthrough():
    m = build_column_mapping(["timestamp", "open", "high", "low", "close", "volume"])
    assert set(m.values()) == {"timestamp", "open", "high", "low", "close", "volume"}


def test_normalize_strips_commas_and_won():
    header = ["일자", "시가", "고가", "저가", "종가", "거래량"]
    rows = [{"일자": "2024-03-04 09:00:00", "시가": "71,000", "고가": "71,090원",
             "저가": "70,950", "종가": "71,050", "거래량": "250,000"}]
    recs, rep = normalize_rows(header, rows, default_symbol="005930")
    assert rep.valid_rows == 1 and rep.dropped_rows == 0
    assert recs[0]["open"] == 71000.0
    assert recs[0]["high"] == 71090.0
    assert recs[0]["symbol"] == "005930"


def test_normalize_drops_unparseable_rows():
    header = ["일자", "시가", "고가", "저가", "종가", "거래량"]
    rows = [
        {"일자": "2024-03-04 09:00:00", "시가": "100", "고가": "105", "저가": "98",
         "종가": "102", "거래량": "1000"},
        {"일자": "", "시가": "x", "고가": "", "저가": "", "종가": "", "거래량": ""},
    ]
    recs, rep = normalize_rows(header, rows)
    assert rep.total_rows == 2 and rep.valid_rows == 1 and rep.dropped_rows == 1
    assert rep.dropped_ratio == 0.5


def test_missing_required_columns_reported():
    recs, rep = normalize_rows(["일자", "시가", "고가"], [{}])
    assert rep.missing_required  # low/close/volume 누락
    assert recs == []


def test_does_not_fabricate_data():
    """드롭만 — 없는 값을 만들어내지 않는다."""
    header = ["일자", "시가", "고가", "저가", "종가", "거래량"]
    rows = [{"일자": "t", "시가": "1", "고가": "2", "저가": "0.5", "종가": "1.5", "거래량": ""}]
    recs, rep = normalize_rows(header, rows)
    # volume 빈값 → 0 으로(누락 거래량 허용), 가격은 그대로.
    assert recs[0]["volume"] == 0
    assert recs[0]["open"] == 1.0


@pytest.mark.parametrize("fn,expected", [
    ("005930_5m.csv", "5m"), ("005930_1m.csv", "1m"), ("005930_3min.csv", "3m"),
    ("005930.csv", None), ("000660_60m.csv", "60m"),
])
def test_bar_size_from_filename(fn, expected):
    assert bar_size_from_filename(fn) == expected


def test_normalize_real_fixture_csv_passthrough():
    """표준 영문 헤더 분봉 fixture 도 normalizer 통과."""
    recs, rep = normalize_intraday_csv(
        _REPO / "backend" / "tests" / "fixtures" / "intraday_clean" / "005930.csv")
    assert rep.valid_rows > 100 and rep.dropped_rows == 0
    assert rep.missing_required == ()


def test_report_guard_contains_secret():
    with pytest.raises(ValueError):
        NormalizeReport(1, 1, 0, 0.0, {}, (), contains_secret=True)


def test_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "market_data" / "intraday_csv_normalizer.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.execution", "order_router", "OrderExecutor", "app.brokers"):
                assert mod not in ln
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src
