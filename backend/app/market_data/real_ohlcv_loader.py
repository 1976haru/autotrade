"""REAL-DATA-STRATEGY-01 — 실제/준실제 OHLCV 로더 (read-only).

기존 로더(`app.backtest.strategy_council_backtest.load_ohlcv_from_csv`,
`app.backtest.real_data.loader.load_real_ohlcv`)를 재사용해 실제/준실제 OHLCV 를
적재하고, **데이터 출처(real vs sample fixture)** 를 분류한다.

데이터 소스 우선순위 (CLAUDE.md / docs/real_data_strategy_validation.md):
  1) 사용자 제공 실제 CSV  2) (옵션) yfinance 준실제  3) sample fixture(기능 확인용)
KIS historical 시세 API 는 **미구현** (`kis_historical_supported()==False`) — 후속 옵션.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import 하지 않으며,
KIS 주문/모의주문을 호출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# 데이터 소스 분류.
SRC_CSV_USER = "CSV_USER"
SRC_CSV_REAL_FIXTURE = "CSV_REAL_FIXTURE"
SRC_YFINANCE = "YFINANCE"
SRC_SAMPLE_FIXTURE = "SAMPLE_FIXTURE"
SRC_NONE = "NONE"

# sample(합성) fixture 파일명 — 이게 출처면 real_data_used=False.
_SAMPLE_FIXTURE_MARKERS = ("sample_ohlcv", "backtest_ohlcv")


@dataclass(frozen=True)
class LoadedOhlcv:
    bars: tuple[Any, ...]            # OHLCVBar 튜플 (strategy_council_backtest.OHLCVBar)
    data_source: str
    real_data_used: bool
    sample_fixture_only: bool
    symbols: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    kis_historical_available: bool = False   # 항상 False (미구현)

    def __post_init__(self) -> None:
        if self.kis_historical_available:
            raise ValueError("KIS historical candle API is not implemented")


def kis_historical_supported() -> bool:
    """KIS 과거 일봉/분봉(시세) 수집 지원 여부 — **현재 미구현**.

    KIS Client 는 get_price(현재가) / inquire_balance / inquire_daily_ccld 만 제공하며
    historical candle endpoint(예: FHKST03010100) 는 구현되어 있지 않다. 백테스트에는
    과거 OHLCV 가 필요하므로 CSV / yfinance fallback 을 사용한다. (주문 API 미호출.)
    """
    return False


def _classify_source(path: Path) -> tuple[str, bool, bool]:
    """(data_source, real_data_used, sample_fixture_only)."""
    name = path.name.lower()
    if any(m in name for m in _SAMPLE_FIXTURE_MARKERS):
        return SRC_SAMPLE_FIXTURE, False, True
    if "real_data" in str(path).replace("\\", "/").lower():
        return SRC_CSV_REAL_FIXTURE, True, False
    return SRC_CSV_USER, True, False


def load_from_csv(path: str | Path) -> LoadedOhlcv:
    """단일 CSV(timestamp,open,high,low,close,volume[,vwap,market_regime,time_phase])."""
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv

    p = Path(path)
    if not p.exists():
        return LoadedOhlcv((), SRC_NONE, False, False, reasons=(f"파일 없음: {p}",))
    bars = load_ohlcv_from_csv(str(p))
    data_source, real_used, sample_only = _classify_source(p)
    symbols = tuple(sorted({getattr(b, "symbol", "") for b in bars}))
    return LoadedOhlcv(
        bars=tuple(bars), data_source=data_source, real_data_used=real_used,
        sample_fixture_only=sample_only, symbols=symbols, source_paths=(str(p),))


def load_from_dir(directory: str | Path, *, symbols: list[str] | None = None) -> LoadedOhlcv:
    """디렉토리의 {symbol}.csv 들을 적재해 합친다 (다종목 백테스트용)."""
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv

    d = Path(directory)
    if not d.is_dir():
        return LoadedOhlcv((), SRC_NONE, False, False, reasons=(f"디렉토리 없음: {d}",))
    files = sorted(d.glob("*.csv"))
    if symbols:
        wanted = {s.lower() for s in symbols}
        files = [f for f in files if f.stem.lower() in wanted]
    all_bars: list[Any] = []
    paths: list[str] = []
    real_used = False
    sample_only = True
    for f in files:
        all_bars.extend(load_ohlcv_from_csv(str(f)))
        paths.append(str(f))
        _, ru, so = _classify_source(f)
        real_used = real_used or ru
        sample_only = sample_only and so
    if not all_bars:
        return LoadedOhlcv((), SRC_NONE, False, False,
                           reasons=(f"{d} 에서 CSV 를 찾지 못함",))
    syms = tuple(sorted({getattr(b, "symbol", "") for b in all_bars}))
    return LoadedOhlcv(
        bars=tuple(all_bars),
        data_source=SRC_CSV_REAL_FIXTURE if real_used else SRC_SAMPLE_FIXTURE,
        real_data_used=real_used, sample_fixture_only=sample_only,
        symbols=syms, source_paths=tuple(paths))


def load_for_symbols(
    symbols: list[str],
    *,
    start: datetime,
    end: datetime,
    enable_yfinance: bool = False,
    extra_csv_dir: str | Path | None = None,
) -> LoadedOhlcv:
    """심볼별로 기존 real_data 로더 사용 (CSV-first, 옵션 yfinance). 주문 API 미호출."""
    from app.backtest.real_data.loader import load_real_ohlcv

    extra_paths = None
    if extra_csv_dir:
        extra_paths = [Path(extra_csv_dir) / f"{s}.csv" for s in symbols]
    all_bars: list[Any] = []
    paths: list[str] = []
    used_yf = False
    used_csv = False
    reasons: list[str] = []
    for i, sym in enumerate(symbols):
        ep = [extra_paths[i]] if extra_paths else None
        res = load_real_ohlcv(sym, start=start, end=end,
                              enable_yfinance=enable_yfinance, extra_csv_paths=ep)
        status = str(getattr(res, "status", ""))
        bars = getattr(res, "bars", None) or []
        if "YFINANCE" in status:
            used_yf = True
        if "CSV" in status:
            used_csv = True
        if bars:
            all_bars.extend(_records_to_ohlcv(bars, sym))
            src = getattr(res, "source", None)
            if src:
                paths.append(str(src))
        else:
            reasons.append(f"{sym}: {getattr(res, 'reason', status) or 'no data'}")
    if not all_bars:
        return LoadedOhlcv((), SRC_NONE, False, False, symbols=tuple(symbols),
                           reasons=tuple(reasons) or ("데이터 적재 실패",))
    data_source = SRC_YFINANCE if (used_yf and not used_csv) else SRC_CSV_REAL_FIXTURE
    syms = tuple(sorted({getattr(b, "symbol", "") for b in all_bars}))
    return LoadedOhlcv(
        bars=tuple(all_bars), data_source=data_source, real_data_used=True,
        sample_fixture_only=False, symbols=syms, source_paths=tuple(paths),
        reasons=tuple(reasons))


def _records_to_ohlcv(bars: list[Any], symbol: str) -> list[Any]:
    """real_data.loader 의 Bar(또는 dict)를 strategy_council_backtest.OHLCVBar 로 변환."""
    from app.backtest.strategy_council_backtest import load_ohlcv_from_records

    records: list[dict[str, Any]] = []
    for b in bars:
        if isinstance(b, dict):
            d = dict(b)
        else:
            d = {
                "timestamp": getattr(b, "timestamp", None),
                "open": getattr(b, "open", None),
                "high": getattr(b, "high", None),
                "low": getattr(b, "low", None),
                "close": getattr(b, "close", None),
                "volume": getattr(b, "volume", None),
            }
        d.setdefault("symbol", symbol)
        # timestamp 를 ISO 문자열로 정규화.
        ts = d.get("timestamp")
        if hasattr(ts, "isoformat"):
            d["timestamp"] = ts.isoformat()
        records.append(d)
    return load_ohlcv_from_records(records, default_symbol=symbol)
