#!/usr/bin/env python3
"""TEMP — L1+L2: 가용 종목 union + turnover proxy 시총 4분위 + 60m resample.

Read-only. Writes to data/market/lens_60m/{symbol}_60m.csv (gitignored).
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas required"); sys.exit(2)


SOURCES_5M = [
    Path("data/market/robust_intraday_5m"),
    Path("data/market/intraday_5m_1y_old"),
    Path("data/market/diversified_5m"),
    Path("data/market/intraday_5m_forward_extra"),
    Path("data/market/intraday_ohlcv"),
]
OUTPUT_DIR = Path("data/market/lens_60m")
TARGET_PERIOD_START = "2025-05-15"
TARGET_PERIOD_END   = "2026-05-26"

AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def discover_files():
    """{symbol: [(source_path, bar_count)]} — symbol's 5m CSVs across all sources."""
    by_sym: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    for src in SOURCES_5M:
        if not src.exists(): continue
        for f in src.glob("*.csv"):
            stem = f.stem
            # strip suffix _5m, _1m, etc.
            sym = stem
            for suf in ("_5m", "_1m", "_30m", "_60m", "_1d"):
                if sym.endswith(suf):
                    sym = sym[:-len(suf)]; break
            try:
                n = sum(1 for _ in open(f, "r", encoding="utf-8")) - 1
            except Exception:
                n = 0
            by_sym[sym].append((f, n))
    return by_sym


def load_and_filter(src: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(src)
    except Exception:
        return None
    if "timestamp" not in df.columns or "close" not in df.columns:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False, errors="coerce")
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])
    # filter to target period (KST naive comparison via date)
    df = df[(df["timestamp"] >= pd.Timestamp(TARGET_PERIOD_START + "T00:00:00+09:00")) &
            (df["timestamp"] <= pd.Timestamp(TARGET_PERIOD_END + "T23:59:59+09:00"))]
    return df if not df.empty else None


def resample_60m(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("timestamp").set_index("timestamp")
    out = df.resample("60min", label="right", closed="right").agg(AGG)
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out.reset_index()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    by_sym = discover_files()
    print(f"discovered {len(by_sym)} unique syms across {len(SOURCES_5M)} sources")

    universe: list[dict] = []
    for sym, sources in by_sym.items():
        # pick source with most bars in the target period
        best_df = None; best_src = None
        for src, _n in sorted(sources, key=lambda x: x[1], reverse=True):
            df = load_and_filter(src)
            if df is not None and len(df) > (best_df is not None and len(best_df) or 0):
                best_df = df; best_src = src
        if best_df is None or len(best_df) < 500:   # need ≥500 5m bars (~1 month)
            continue
        df60 = resample_60m(best_df)
        if df60.empty or len(df60) < 80:
            continue
        # write
        df60["symbol"] = sym
        out_path = OUTPUT_DIR / f"{sym}_60m.csv"
        df60.to_csv(out_path, index=False)

        # turnover proxy (avg daily $ volume) for L2 cap-quartile
        df60["_dollar_vol"] = df60["close"].astype(float) * df60["volume"].astype(float)
        df60["_date"] = df60["timestamp"].dt.date
        daily = df60.groupby("_date")["_dollar_vol"].sum()
        avg_daily_turnover = float(daily.mean()) if not daily.empty else 0.0

        # buy&hold over period
        first_close = float(df60["close"].iloc[0])
        last_close = float(df60["close"].iloc[-1])
        bh_return = (last_close / first_close - 1.0) if first_close > 0 else 0.0

        universe.append({
            "symbol":           sym,
            "source":           str(best_src),
            "bars_60m":         int(len(df60)),
            "trading_days":     int(df60["_date"].nunique()),
            "first_ts":         str(df60["timestamp"].iloc[0]),
            "last_ts":          str(df60["timestamp"].iloc[-1]),
            "first_close":      first_close,
            "last_close":       last_close,
            "buy_hold_return":  bh_return,
            "avg_daily_turnover": avg_daily_turnover,
        })

    # Sort by turnover (descending = most liquid = larger cap proxy)
    universe.sort(key=lambda u: u["avg_daily_turnover"], reverse=True)
    n = len(universe)
    # 4-quartile by turnover (proxy for market cap × liquidity)
    BUCKETS = ["대형", "중대형", "중소형", "소형"]
    for i, u in enumerate(universe):
        bucket_idx = min(3, (i * 4) // n)
        u["bucket"] = BUCKETS[bucket_idx]

    # Slippage per bucket (one-side bps)
    SLIPPAGE_BPS = {"대형": 5.0, "중대형": 10.0, "중소형": 20.0, "소형": 35.0}
    for u in universe:
        u["slippage_bps_per_side"] = SLIPPAGE_BPS[u["bucket"]]
        # round-trip cost = 1.5*2 commission + 20 tax + slip*2
        u["round_trip_cost_bps"] = 1.5 * 2 + 20.0 + u["slippage_bps_per_side"] * 2

    # Summary
    print(f"\n  universe written to {OUTPUT_DIR}: {n} symbols")
    for bk in BUCKETS:
        bk_syms = [u for u in universe if u["bucket"] == bk]
        if not bk_syms: continue
        med_turnover = statistics.median(u["avg_daily_turnover"] for u in bk_syms)
        med_bh = statistics.median(u["buy_hold_return"] for u in bk_syms)
        slip = SLIPPAGE_BPS[bk]
        rt = 1.5 * 2 + 20.0 + slip * 2
        print(f"  {bk:5s}: {len(bk_syms):3d} syms, median turnover={med_turnover:,.0f} KRW/day, "
              f"median buy&hold={med_bh*100:+6.1f}%, slip={slip}bps/side → round-trip {rt}bps")

    import json
    (OUTPUT_DIR / "_universe.json").write_text(
        json.dumps(universe, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\n  manifest → {OUTPUT_DIR}/_universe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
