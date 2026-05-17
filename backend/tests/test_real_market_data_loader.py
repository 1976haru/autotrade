"""3-02 — 실제 OHLCV 데이터 loader 테스트.

invariant:
- CSV 우선 사용 — 성공 시 status=CSV_LOCAL.
- CSV 없고 yfinance 비활성 → DISABLED + reason.
- yfinance 호출 실패 시 FETCH_FAILED (mock 으로 silent swap 0건).
- 어떤 입력으로도 예외 raise 0건 (graceful).
- broker / OrderExecutor / route_order / KIS 주문 API import 0건 (정적 grep).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.backtest.real_data.loader import (
    LoadResult,
    LoadStatus,
    load_real_ohlcv,
    summarize_load_results,
)
from app.backtest.real_data.symbols import yahoo_ticker


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "real_data"


# ─────────────────────────────────────────────────────────────────────────────
# 1. CSV 우선 로드 — happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestCsvLoadingHappyPath:
    def test_loads_csv_for_005930_fixture(self):
        result = load_real_ohlcv(
            "005930",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
        )
        assert result.status == LoadStatus.CSV_LOCAL
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


# ─────────────────────────────────────────────────────────────────────────────
# 2. NO DATA / DISABLED — graceful 처리
# ─────────────────────────────────────────────────────────────────────────────

class TestNoDataPaths:
    def test_missing_symbol_with_yfinance_disabled(self):
        result = load_real_ohlcv(
            "999999",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
        )
        assert result.status == LoadStatus.DISABLED
        assert result.bars is None
        assert "csv_missing" in result.reason
        assert "yfinance_disabled" in result.reason

    def test_yfinance_failure_graceful(self, monkeypatch):
        """yfinance 호출 후 네트워크/파싱 실패도 raise 0건."""
        import app.backtest.real_data.loader as loader_mod

        def fake_fail(symbol, start, end):
            return LoadResult(
                symbol=symbol,
                status=LoadStatus.FETCH_FAILED,
                reason="yfinance_error: ConnectionError",
                source="yfinance",
            )

        monkeypatch.setattr(loader_mod, "_try_load_yfinance", fake_fail)
        result = load_real_ohlcv(
            "999998",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=True,
        )
        assert result.status == LoadStatus.FETCH_FAILED
        assert result.bars is None
        assert "yfinance" in result.reason

    def test_yfinance_adapter_unavailable_graceful(self, monkeypatch):
        """yfinance 패키지 미설치 환경도 raise 0건."""
        import app.backtest.real_data.loader as loader_mod

        def fake_unavailable(symbol, start, end):
            return LoadResult(
                symbol=symbol,
                status=LoadStatus.NO_DATA,
                reason="yfinance_adapter_unavailable: ImportError",
            )

        monkeypatch.setattr(loader_mod, "_try_load_yfinance", fake_unavailable)
        result = load_real_ohlcv(
            "999997",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=True,
        )
        assert result.status == LoadStatus.NO_DATA
        assert "yfinance" in result.reason or "unavailable" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 3. extra_csv_paths — 테스트 fixture 주입
# ─────────────────────────────────────────────────────────────────────────────

class TestExtraCsvPath:
    def test_extra_csv_path_takes_priority(self, tmp_path):
        custom = tmp_path / "custom_999996.csv"
        custom.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2025-01-01T09:00:00,1000,1100,950,1050,100000\n"
            "2025-01-02T09:00:00,1050,1150,1000,1100,110000\n",
            encoding="utf-8",
        )
        result = load_real_ohlcv(
            "999996",
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            enable_yfinance=False,
            extra_csv_paths=[custom],
        )
        assert result.status == LoadStatus.CSV_LOCAL
        assert result.bar_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. summarize_load_results — status 별 집계
# ─────────────────────────────────────────────────────────────────────────────

class TestSummary:
    def test_summarize_groups_by_status(self):
        results = [
            LoadResult("005930", LoadStatus.CSV_LOCAL, bars=[], bar_count=0),
            LoadResult("000660", LoadStatus.DISABLED, reason="x"),
            LoadResult("035420", LoadStatus.DISABLED, reason="x"),
            LoadResult("035720", LoadStatus.FETCH_FAILED, reason="y"),
        ]
        summary = summarize_load_results(results)
        assert summary["CSV_LOCAL"]    == ["005930"]
        assert summary["DISABLED"]     == ["000660", "035420"]
        assert summary["FETCH_FAILED"] == ["035720"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. yahoo_ticker — KOSPI/KOSDAQ suffix 분기
# ─────────────────────────────────────────────────────────────────────────────

class TestYahooTicker:
    def test_kospi_default_suffix(self):
        assert yahoo_ticker("005930") == "005930.KS"
        assert yahoo_ticker("000660") == "000660.KS"

    def test_existing_suffix_preserved(self):
        assert yahoo_ticker("005930.KS") == "005930.KS"
        assert yahoo_ticker("005930.KQ") == "005930.KQ"

    def test_invalid_symbol_returned_as_is(self):
        assert yahoo_ticker("AAPL") == "AAPL"


# ─────────────────────────────────────────────────────────────────────────────
# 6. 정적 가드 — broker / OrderExecutor / route_order / KIS 주문 import 0건
# ─────────────────────────────────────────────────────────────────────────────

class TestNoForbiddenImports:
    def test_loader_module_has_no_broker_imports(self):
        import app.backtest.real_data.loader as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden pattern: {pat}"

    def test_symbols_module_has_no_broker_imports(self):
        import app.backtest.real_data.symbols as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.",
            r"from\s+app\.execution\.",
            r"broker\.place_order",
            r"route_order\s*\(",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden in symbols.py: {pat}"

    def test_verdicts_module_has_no_broker_imports(self):
        import app.backtest.real_data.verdicts as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.",
            r"from\s+app\.execution\.",
            r"broker\.place_order",
            r"route_order\s*\(",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden in verdicts.py: {pat}"
