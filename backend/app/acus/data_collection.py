"""ACUS Stage 01 — universe 수집 + 분봉 품질 검증 (read-only).

input_dir 의 ``{symbol}_5m.csv`` / ``{symbol}.csv`` 를 스캔해 종목별 분봉 품질을
검증하고 메타데이터만 기록한다(bars 자체는 checkpoint 에 저장하지 않고 후속 stage
가 디스크에서 재적재). broker / KIS 주문 API 호출 0건.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acus.deps import PipelineDeps, default_deps
from app.universe.universe_status import is_valid_kr_symbol


def _symbol_of(path: Path, meta: dict[str, Any]) -> str:
    syms = meta.get("symbols") or []
    if syms:
        return str(syms[0])
    stem = path.stem
    return stem[:-3] if stem.lower().endswith("_5m") else stem


def collect_universe(
    input_dir: str | Path,
    *,
    symbols: list[str] | None = None,
    min_bars: int = 100,
    min_days: int = 5,
    deps: PipelineDeps | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Stage 01 결과 dict 생성. 사람 입력 요구 0건."""
    from app.market_data.intraday_ohlcv import check_intraday_quality

    deps = deps or default_deps()
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    d = Path(input_dir)
    files = sorted(d.glob("*.csv")) if d.is_dir() else []
    if symbols:
        wanted = {s.lower() for s in symbols}
        files = [f for f in files
                 if f.stem.lower() in wanted or f.stem.lower().removesuffix("_5m") in wanted]

    per: list[dict[str, Any]] = []
    for f in files:
        try:
            bars, meta = deps.load_bars(str(f))
            sym = _symbol_of(f, meta)
            q = check_intraday_quality(bars, min_bars=min_bars, min_days=min_days)
            ready = bool(bars) and q.sufficient_for_backtest and q.status != "FAIL"
            per.append({
                "symbol": sym,
                "path": str(f),
                "valid_symbol": is_valid_kr_symbol(sym),
                "quality_status": q.status,
                "bar_count": q.bar_count,
                "day_count": q.day_count,
                "bars_per_day": q.bars_per_day,
                "bar_size_minutes": q.bar_size_minutes,
                "intraday_detected": q.intraday_detected,
                "ready_for_backtest": ready,
                "reason": "; ".join(q.reasons[:2]),
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 — 단일 종목 오류가 전체를 멈추지 않음
            per.append({
                "symbol": _symbol_of(f, {}),
                "path": str(f),
                "valid_symbol": is_valid_kr_symbol(f.stem.removesuffix("_5m")),
                "quality_status": "FAIL",
                "ready_for_backtest": False,
                "reason": f"load_error:{type(exc).__name__}",
                "error": f"{type(exc).__name__}: {exc}",
            })

    ready_syms = [p["symbol"] for p in per if p["ready_for_backtest"]]
    return {
        "stage": "stage_01_collection_ready",
        "generated_at": gen,
        "input_dir": str(d),
        "min_bars": min_bars,
        "min_days": min_days,
        "total_symbols": len(per),
        "ready_count": len(ready_syms),
        "ready_symbols": ready_syms,
        "per_symbol": per,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
    }
