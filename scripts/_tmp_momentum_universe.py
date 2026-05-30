#!/usr/bin/env python3
"""TEMP — momentum verify: widen KR large/mid-cap universe via yfinance.

Read-only market data download. Writes data/market/yf_multiyear/{symbol}.csv
(gitignored). Skips already-cached symbols. NO trading, NO order paths.

NOTE on survival bias: this is a *current* large/mid-cap list (KOSPI100-ish).
Downloading more current survivors widens the sample but does NOT remove
survivorship bias — see momentum_verify report for the honest limitation.
"""
from __future__ import annotations
import sys, time
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed"); sys.exit(2)

OUT = Path("data/market/yf_multiyear")
OUT.mkdir(parents=True, exist_ok=True)

# Broad KOSPI large/mid-cap set (numeric codes -> .KS). Curated, ~110 names.
CODES = [
    "005930","000660","005380","000270","035420","035720","051910","005490",
    "055550","068270","006400","012330","028260","105560","015760","034730",
    "003670","096770","017670","030200","033780","003550","066570","009150",
    "011200","086790","316140","024110","138040","032830","000810","010130",
    "009830","011170","010950","004020","005940","078930","267250","010140",
    "021240","161390","000720","006800","029780","139480","023530","069960",
    "008770","004990","002790","000100","128940","326030","207940","068760",
    "302440","091990","196170","247540","086520","028300","058470","067310",
    "036570","251270","259960","112040","263750","293490","095660","041510",
    "035900","067160","145020","214150","240810","357780","403870","000990",
    "001440","034220","064350","042660","009540","010620","329180","042670",
    "267270","006360","000880","079550","047810","012450","272210","088350",
    "032640","090430","051900","002380","271560","280360","004370","097950",
    "007310","004990","000080","033920","001680","006280","005300","003490",
    "020150","011070","093370","298050","011780","285130","014680","120110",
    "069500",  # KODEX 200 ETF (benchmark proxy)
]

def main():
    seen=set(); ok=0; fail=0; skip=0
    for code in CODES:
        if code in seen: continue
        seen.add(code)
        out = OUT / f"{code}.csv"
        if out.exists() and out.stat().st_size > 1000:
            skip += 1; continue
        tkr = f"{code}.KS"
        try:
            h = yf.Ticker(tkr).history(period="max", interval="1d", auto_adjust=False)
            if h.empty or len(h) < 260:
                print(f"  {tkr}: empty/short ({0 if h.empty else len(h)})"); fail+=1
                time.sleep(0.4); continue
            h = h.reset_index()
            h.columns = [c if c == "Date" else c.lower() for c in h.columns]
            h = h.rename(columns={"Date": "timestamp"})
            h[["timestamp","open","high","low","close","volume"]].to_csv(out, index=False)
            ok += 1
            print(f"  OK {code}: {len(h)} rows {str(h['timestamp'].iloc[0])[:10]}..{str(h['timestamp'].iloc[-1])[:10]}")
        except Exception as e:
            fail += 1; print(f"  {tkr}: ERROR {e}")
        time.sleep(0.4)
    print(f"DONE ok={ok} fail={fail} skip={skip} total_cached={len(list(OUT.glob('*.csv')))}")

if __name__ == "__main__":
    main()
