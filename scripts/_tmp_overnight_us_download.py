#!/usr/bin/env python3
"""TEMP — 오버나이트 백테스트용 미국 지수 일봉 다운로드 (read-only).

S&P500(^GSPC) / 나스닥(^IXIC) / 필라델피아반도체(^SOX) 일봉.
data/market/us_indices/{name}.csv (gitignore). NO trading.
"""
from __future__ import annotations
import sys
from pathlib import Path
try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed"); sys.exit(2)

OUT = Path("data/market/us_indices"); OUT.mkdir(parents=True, exist_ok=True)
TICKERS = {"^GSPC": "SP500", "^IXIC": "NASDAQ", "^SOX": "SOX"}

for tkr, name in TICKERS.items():
    out = OUT / f"{name}.csv"
    if out.exists() and out.stat().st_size > 1000:
        print(f"skip {name} cached"); continue
    try:
        h = yf.Ticker(tkr).history(period="max", interval="1d", auto_adjust=False)
        if h.empty:
            print(f"{name}: EMPTY"); continue
        h = h.reset_index()
        h.columns = [c if c == "Date" else c.lower() for c in h.columns]
        h = h.rename(columns={"Date": "timestamp"})
        h[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(out, index=False)
        print(f"OK {name}: {len(h)} rows {str(h['timestamp'].iloc[0])[:10]}..{str(h['timestamp'].iloc[-1])[:10]}")
    except Exception as e:
        print(f"{name}: ERROR {e}")
print("DONE")
