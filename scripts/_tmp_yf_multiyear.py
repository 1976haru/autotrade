#!/usr/bin/env python3
"""TEMP — D1: yfinance 다년 일봉 다운로드 + cycle 자동 식별.

Read-only. Writes to data/market/yf_multiyear/{symbol}.csv (gitignored).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed"); sys.exit(2)

OUT_DIR = Path("data/market/yf_multiyear")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Major Korean equities + KOSPI index
TICKERS = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "005380.KS": "현대차",
    "000270.KS": "기아",
    "035420.KS": "NAVER",
    "035720.KS": "카카오",
    "051910.KS": "LG화학",
    "005490.KS": "POSCO홀딩스",
    "055550.KS": "신한지주",
    "068270.KS": "셀트리온",
    "006400.KS": "삼성SDI",
    "012330.KS": "현대모비스",
    "^KS11":     "KOSPI",
}


def main():
    summary = []
    for tkr, name in TICKERS.items():
        out_path = OUT_DIR / f"{tkr.replace('^','').replace('.KS','')}.csv"
        if out_path.exists() and out_path.stat().st_size > 1000:
            n_lines = sum(1 for _ in open(out_path, "r", encoding="utf-8"))
            print(f"  skip {tkr} ({name}) — cached {n_lines} lines")
            continue
        try:
            t = yf.Ticker(tkr)
            h = t.history(period="max", interval="1d", auto_adjust=False)
            if h.empty:
                print(f"  {tkr} ({name}): empty"); continue
            # write CSV in our standard format
            h = h.reset_index()
            h.columns = [c if c == "Date" else c.lower() for c in h.columns]
            h = h.rename(columns={"Date": "timestamp"})
            keep = ["timestamp", "open", "high", "low", "close", "volume"]
            h[keep].to_csv(out_path, index=False)
            n = len(h)
            first = str(h["timestamp"].iloc[0])[:10]
            last = str(h["timestamp"].iloc[-1])[:10]
            summary.append((tkr, name, n, first, last))
            print(f"  ✓ {tkr} ({name}): {n} bars, {first} → {last}")
            time.sleep(0.4)  # rate-limit friendly
        except Exception as e:
            print(f"  ✗ {tkr} ({name}): {type(e).__name__}: {e}")

    print(f"\n--- summary ---")
    print(f"  {len(summary)} new downloads → {OUT_DIR}")


if __name__ == "__main__":
    main()
