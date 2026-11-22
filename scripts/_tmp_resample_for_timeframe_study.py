#!/usr/bin/env python3
"""TEMP — Resample 5m → 30m/60m/1d for the 2026-05-30 timeframe study.

Read-only on source data. Writes to data/market/intraday_30m_resampled/,
intraday_60m_resampled/, intraday_1d_resampled/ (all gitignored under
data/market/).

This script is NOT a backtest — it only reshapes existing bars. No broker,
no order, no .env mutation.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas required: pip install pandas")
    sys.exit(2)


SRC = Path("data/market/robust_intraday_5m")
DESTS = {
    "30m": (Path("data/market/intraday_30m_resampled"), "30min"),
    "60m": (Path("data/market/intraday_60m_resampled"), "60min"),
    "1d":  (Path("data/market/intraday_1d_resampled"),  "1D"),
}

AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_one(src_csv: Path, rule: str) -> pd.DataFrame:
    df = pd.read_csv(src_csv)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    # Drop rows missing OHLCV (no synthetic fill)
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    out = df.resample(rule, label="right", closed="right").agg(AGG)
    out = out.dropna(subset=["open", "high", "low", "close"])  # incomplete bars dropped
    out = out.reset_index()
    return out


def main() -> int:
    if not SRC.exists():
        print(f"source missing: {SRC}")
        return 2
    src_files = sorted(SRC.glob("*_5m.csv"))
    print(f"source files: {len(src_files)} in {SRC}")

    for label, (dest_dir, rule) in DESTS.items():
        dest_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for src in src_files:
            symbol = src.stem.replace("_5m", "")
            try:
                out = resample_one(src, rule)
            except Exception as e:
                print(f"  ERR {symbol} {label}: {type(e).__name__}: {e}")
                continue
            if out.empty:
                continue
            out["symbol"] = symbol
            # Match intraday script's expected filename pattern
            if label == "1d":
                fname = f"{symbol}.csv"  # daily script reads {symbol}.csv
            else:
                fname = f"{symbol}_{label}.csv"
            dest = dest_dir / fname
            out.to_csv(dest, index=False)
            written += 1
        print(f"  {label}: wrote {written} → {dest_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
