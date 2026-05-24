"""REAL-DATA-INPUT-01 — 실제/준실제 OHLCV 데이터셋 수집 + 품질 매니페스트 (read-only).

데이터 소스(우선순위): 사용자 CSV(existing) > yfinance(준실제) > (KIS historical 미구현).
**데이터 수집 실패를 성공처럼 보이지 않으며, sample/mock 으로 몰래 대체하지 않는다.**
품질 FAIL CSV 는 통과시키지 않고, 깨진 OHLC 를 자동 보정하지 않는다.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.market_data.ohlcv_quality import check_ohlcv_quality, to_dict as quality_to_dict

SRC_EXISTING = "existing"
SRC_YFINANCE = "yfinance"


@dataclass(frozen=True)
class SymbolCollectResult:
    symbol: str
    source: str             # existing / yfinance / none
    status: str             # PASS / WARN / FAIL
    bar_count: int
    day_count: int
    quality: dict[str, Any]
    written_path: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class CollectManifest:
    generated_at: str
    requested_source: str
    symbols: tuple[str, ...]
    results: tuple[SymbolCollectResult, ...]
    pass_symbols: tuple[str, ...] = ()
    warn_symbols: tuple[str, ...] = ()
    fail_symbols: tuple[str, ...] = ()
    yfinance_available: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.contains_secret:
            raise ValueError("CollectManifest.contains_secret must be False")


def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _load_existing(symbol: str, source_dir: Path) -> tuple[list[Any], str]:
    from app.market_data.real_ohlcv_loader import load_from_csv
    p = source_dir / f"{symbol}.csv"
    if not p.exists():
        return [], f"CSV 없음: {p}"
    lo = load_from_csv(p)
    return list(lo.bars), "" if lo.bars else "빈 CSV"


def _load_yfinance(symbol: str, start: datetime, end: datetime) -> tuple[list[Any], str]:
    """yfinance(준실제). 미설치/네트워크 실패 시 ([], reason) — *fake 대체 없음*."""
    if not _yfinance_available():
        return [], "yfinance 미설치 — 준실제 데이터 수집 불가 (네트워크 환경에서 설치/재시도)"
    try:
        from app.backtest.real_data.loader import load_real_ohlcv
        from app.market_data.real_ohlcv_loader import _records_to_ohlcv
        res = load_real_ohlcv(symbol, start=start, end=end, enable_yfinance=True)
        bars = getattr(res, "bars", None) or []
        if not bars:
            return [], f"yfinance 데이터 없음: {getattr(res, 'reason', '') or 'no data'}"
        return _records_to_ohlcv(bars, symbol), ""
    except Exception as e:  # noqa: BLE001
        return [], f"yfinance 수집 실패: {type(e).__name__}"


def collect_ohlcv(
    symbols: list[str],
    *,
    source: str = SRC_EXISTING,
    source_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    min_days: int = 28,
    recommended_days: int = 100,
    write: bool = True,
    now: datetime | None = None,
) -> CollectManifest:
    """심볼별 OHLCV 수집 + 품질검증 + (옵션) output_dir 에 PASS/WARN 만 기록."""
    gen = (now or datetime.now(timezone.utc)).isoformat()
    sdir = Path(source_dir) if source_dir else None
    odir = Path(output_dir) if output_dir else None
    if write and odir:
        odir.mkdir(parents=True, exist_ok=True)

    results: list[SymbolCollectResult] = []
    for sym in symbols:
        if source == SRC_YFINANCE:
            bars, reason = _load_yfinance(
                sym, start or datetime(2024, 1, 1), end or datetime.now(timezone.utc))
            src_label = SRC_YFINANCE
        else:
            if sdir is None:
                bars, reason = [], "source-dir 미지정 (existing 모드)"
            else:
                bars, reason = _load_existing(sym, sdir)
            src_label = SRC_EXISTING
        if not bars:
            results.append(SymbolCollectResult(
                sym, "none", "FAIL", 0, 0, {}, None, reason or "데이터 없음"))
            continue
        q = check_ohlcv_quality(bars, min_bars=recommended_days, min_days=min_days)
        # 품질 OK → PASS, WARN/FAIL 유지.
        status = "PASS" if q.status == "OK" else q.status
        written = None
        # FAIL 데이터는 절대 기록/통과시키지 않는다.
        if write and odir and q.sufficient_for_backtest and status != "FAIL":
            written = str(odir / f"{sym}.csv")
            _write_csv(bars, Path(written))
        results.append(SymbolCollectResult(
            sym, src_label, status, q.bar_count, q.day_count,
            quality_to_dict(q), written, "" if status != "FAIL" else "; ".join(q.reasons)))

    return CollectManifest(
        generated_at=gen,
        requested_source=source,
        symbols=tuple(symbols),
        results=tuple(results),
        pass_symbols=tuple(r.symbol for r in results if r.status == "PASS"),
        warn_symbols=tuple(r.symbol for r in results if r.status == "WARN"),
        fail_symbols=tuple(r.symbol for r in results if r.status == "FAIL"),
        yfinance_available=_yfinance_available(),
    )


def _write_csv(bars: list[Any], path: Path) -> None:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            ts = getattr(b, "timestamp", "")
            ts = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            w.writerow([ts, int(getattr(b, "open", 0)), int(getattr(b, "high", 0)),
                        int(getattr(b, "low", 0)), int(getattr(b, "close", 0)),
                        int(getattr(b, "volume", 0))])


def manifest_to_dict(m: CollectManifest) -> dict[str, Any]:
    return {
        "generated_at": m.generated_at,
        "requested_source": m.requested_source,
        "symbols": list(m.symbols),
        "yfinance_available": m.yfinance_available,
        "pass_symbols": list(m.pass_symbols),
        "warn_symbols": list(m.warn_symbols),
        "fail_symbols": list(m.fail_symbols),
        "contains_secret": m.contains_secret,
        "results": [
            {"symbol": r.symbol, "source": r.source, "status": r.status,
             "bar_count": r.bar_count, "day_count": r.day_count,
             "written_path": r.written_path, "reason": r.reason, "quality": r.quality}
            for r in m.results
        ],
    }


def render_manifest_markdown(m: CollectManifest) -> str:
    lines = [
        "# REAL-DATA-INPUT-01 — OHLCV 데이터셋 수집 품질 매니페스트",
        "",
        "> 수집 실패를 성공으로 표시하지 않음 · sample/mock 대체 0건 · 품질 FAIL 데이터 미사용.",
        "",
        f"- source: **{m.requested_source}** · yfinance_available={m.yfinance_available}",
        f"- PASS {len(m.pass_symbols)} · WARN {len(m.warn_symbols)} · FAIL {len(m.fail_symbols)}",
        "",
        "| symbol | source | status | bars | days | written | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in m.results:
        lines.append(
            f"| {r.symbol} | {r.source} | {r.status} | {r.bar_count} | {r.day_count} | "
            f"{'예' if r.written_path else '—'} | {r.reason[:60]} |")
    return "\n".join(lines) + "\n"
