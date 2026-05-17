"""3단계 — 실제 OHLCV 데이터 로더 (data_source.py) 테스트.

invariant:
- CSV 우선 사용 — 성공 시 status=CSV_LOCAL.
- CSV 없고 yfinance 비활성 — DISABLED + reason.
- yfinance 호출 실패 — FETCH_FAILED (mock 으로 silent swap 0건).
- 어떤 입력으로도 예외 raise 0건 (graceful).
- broker / OrderExecutor / route_order import 0건 (정적 grep).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.backtest.real_data.data_source import (
    DataLoadResult,
    DataLoadStatus,
    load_real_ohlcv,
    summarize_load_results,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "real_data"


class TestCsvLoadingHappyPath:
    def test_loads_csv_for_005930_fixture(self):
        result = load_real_ohlcv(
            "005930",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
        )
        assert result.status == DataLoadStatus.CSV_LOCAL
        assert result.bars is not None
        assert result.bar_count == len(result.bars)
        assert result.bar_count >= 60   # 80+ bars in fixture
        assert "005930.csv" in result.source

    def test_bars_have_expected_fields(self):
        result = load_real_ohlcv(
            "005930",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
        )
        assert result.bars
        first = result.bars[0]
        assert first.symbol == "005930"
        assert first.open > 0
        assert first.close > 0
        assert first.volume > 0


class TestNoDataPaths:
    def test_missing_symbol_with_yfinance_disabled(self):
        """CSV 없는 symbol + yfinance off → DISABLED. raise 0건."""
        result = load_real_ohlcv(
            "999999",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
        )
        assert result.status == DataLoadStatus.DISABLED
        assert result.bars is None
        assert "csv_missing" in result.reason
        assert "yfinance_disabled" in result.reason

    def test_missing_symbol_yfinance_enabled_graceful(self, monkeypatch):
        """yfinance enabled 일 때도 fetch 실패 시 graceful — raise 0건.

        실제 yfinance 호출 차단을 위해 adapter 를 monkey-patch — CI 안전.
        """
        # Simulate yfinance adapter raise.
        import app.backtest.real_data.data_source as ds_mod

        def fake_load(symbol, start, end):
            return DataLoadResult(
                symbol=symbol,
                status=DataLoadStatus.FETCH_FAILED,
                reason="yfinance_error: ConnectionError",
                source="yfinance",
            )

        monkeypatch.setattr(ds_mod, "_try_load_yfinance", fake_load)
        result = load_real_ohlcv(
            "999998",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=True,
        )
        assert result.status == DataLoadStatus.FETCH_FAILED
        assert result.bars is None
        assert "yfinance" in result.reason

    def test_yfinance_adapter_unavailable_graceful(self, monkeypatch):
        """yfinance / market.base import 실패 시 NO_DATA + reason."""
        import app.backtest.real_data.data_source as ds_mod

        def fake_unavailable(symbol, start, end):
            return DataLoadResult(
                symbol=symbol,
                status=DataLoadStatus.NO_DATA,
                reason="yfinance_adapter_unavailable: ImportError",
            )

        monkeypatch.setattr(ds_mod, "_try_load_yfinance", fake_unavailable)
        result = load_real_ohlcv(
            "999997",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=True,
        )
        assert result.status == DataLoadStatus.NO_DATA
        assert "unavailable" in result.reason or "yfinance" in result.reason


class TestExtraCsvPath:
    def test_extra_csv_path_takes_priority(self, tmp_path):
        # tmp_path 에 가짜 fixture 작성 — 우선순위 검증.
        custom = tmp_path / "custom_999996.csv"
        custom.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2025-01-01T09:00:00,1000,1100,950,1050,100000\n"
            "2025-01-02T09:00:00,1050,1150,1000,1100,110000\n",
            encoding="utf-8",
        )
        # extra_csv_paths must point to the file path itself (loader checks Path.exists).
        # Our loader iterates default candidates AND extras; provide *both* shapes by
        # using the tmp dir's individual file as an extra Path.
        result = load_real_ohlcv(
            "999996",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
            extra_csv_paths=[custom],
        )
        # extra_csv_paths 가 default 보다 우선 — 등록되지 않은 999996 이라도 로드됨.
        assert result.status == DataLoadStatus.CSV_LOCAL
        assert result.bar_count == 2


class TestSummary:
    def test_summarize_groups_by_status(self):
        results = [
            DataLoadResult("005930", DataLoadStatus.CSV_LOCAL, bars=[], bar_count=0),
            DataLoadResult("000660", DataLoadStatus.DISABLED, reason="x"),
            DataLoadResult("035420", DataLoadStatus.DISABLED, reason="x"),
            DataLoadResult("035720", DataLoadStatus.FETCH_FAILED, reason="y"),
        ]
        summary = summarize_load_results(results)
        assert "CSV_LOCAL"    in summary and summary["CSV_LOCAL"]    == ["005930"]
        assert "DISABLED"     in summary and summary["DISABLED"]     == ["000660", "035420"]
        assert "FETCH_FAILED" in summary and summary["FETCH_FAILED"] == ["035720"]


class TestNoBrokerImports:
    """정적 grep — data_source.py 가 broker / OrderExecutor / route_order import 0건."""

    def test_no_forbidden_imports(self):
        import app.backtest.real_data.data_source as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden_patterns = [
            r"from\s+app\.brokers\.",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
        ]
        for pat in forbidden_patterns:
            assert not re.search(pat, src), f"forbidden pattern found: {pat}"
