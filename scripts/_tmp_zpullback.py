#!/usr/bin/env python3
"""TEMP — 단순 z-score 눌림목 단독 전략 검증 (Z1-Z7).

★ Lookahead-free: rolling z[t] uses closes[t-N:t] (t 미포함).
★ Measurement: per-symbol median 초과수익 (vs BH).
★ 3 benchmarks: BH, 균등보유(EW portfolio), 단순 z 눌림목.

NO new strategy, NO simulator, NO broker / route_order / place_order.
"""
from __future__ import annotations

import csv, json, math, statistics, sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path("data/market/yf_multiyear")
LENS_UNIVERSE = Path("data/market/lens_60m/_universe.json")
KOSPI = "KS11"

COMMISSION_BPS = 1.5
SLIP_BPS_BY_BUCKET = {"대형": 5.0, "중대형": 10.0, "중소형": 20.0, "소형": 35.0, "UNK": 15.0}
TAX_BPS = 20.0

# Strategy sweep
ENTRY_Z_VALUES = [-1.5, -2.0, -2.5]
LOOKBACKS = [20, 60, 120]
EXIT_Z = 0.0   # convergence to mean
STOP_Z = -4.0  # |z| ≥ 4 → 발산 stop
TIME_STOP_DAYS = 30


def load_daily(symbol: str):
    p = DATA_DIR / f"{symbol}.csv"
    if not p.exists(): return []
    out = []
    with open(p, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        if not r[0] or not r[4]: continue
        try:
            out.append((r[0][:10], float(r[4])))
        except (ValueError, IndexError): continue
    out.sort()
    return out


def cap_bucket(symbol: str, lens_universe: dict[str, str]) -> str:
    return lens_universe.get(symbol, "UNK")


def rolling_z(closes, window):
    n = len(closes); out = [None] * n
    for t in range(window, n):
        win = closes[t-window:t]   # ★ excludes t
        mean = sum(win) / window
        var = sum((x - mean) ** 2 for x in win) / (window - 1)
        sd = math.sqrt(var) if var > 0 else 0
        if sd > 0:
            out[t] = (closes[t-1] - mean) / sd   # ★ uses closes[t-1]
    return out


def simulate_zpullback(closes, dates, entry_z=-2.0, lookback=60, bucket="대형",
                       slice_lo=0, slice_hi=None):
    """Single-symbol z pullback: z ≤ entry_z 매수 → z ≥ 0 청산 → stop |z| ≤ -4.

    cost = COMMISSION*2 + slip(bucket)*2 + TAX (sell only) — one-sided roundtrip.
    """
    if slice_hi is None: slice_hi = len(closes)
    sub_close = closes[slice_lo:slice_hi]
    sub_date  = dates[slice_lo:slice_hi]
    if len(sub_close) < lookback + 30: return None

    cost_bps = COMMISSION_BPS * 2 + SLIP_BPS_BY_BUCKET.get(bucket, 15.0) * 2 + TAX_BPS
    cost_frac = cost_bps / 1e4

    z = rolling_z(sub_close, lookback)
    n = len(sub_close)
    trades = []
    position = None

    for t in range(lookback + 1, n):
        zt = z[t]
        if zt is None: continue
        if position is not None:
            held = t - position["entry_t"]
            # exit on convergence
            if zt >= EXIT_Z:
                pnl = (sub_close[t] / position["entry_close"] - 1 - cost_frac) * 100
                trades.append({"entry_date": position["entry_date"],
                               "exit_date": sub_date[t],
                               "held_days": held, "entry_z": position["entry_z"],
                               "exit_reason": "CONVERGE",
                               "pnl_pct": round(pnl, 4)})
                position = None
            elif zt <= STOP_Z:
                pnl = (sub_close[t] / position["entry_close"] - 1 - cost_frac) * 100
                trades.append({"entry_date": position["entry_date"],
                               "exit_date": sub_date[t],
                               "held_days": held, "entry_z": position["entry_z"],
                               "exit_reason": "STOP",
                               "pnl_pct": round(pnl, 4)})
                position = None
            elif held >= TIME_STOP_DAYS:
                pnl = (sub_close[t] / position["entry_close"] - 1 - cost_frac) * 100
                trades.append({"entry_date": position["entry_date"],
                               "exit_date": sub_date[t],
                               "held_days": held, "entry_z": position["entry_z"],
                               "exit_reason": "TIME",
                               "pnl_pct": round(pnl, 4)})
                position = None
        # entry
        if position is None and zt is not None and zt <= entry_z:
            position = {"entry_t": t, "entry_date": sub_date[t],
                        "entry_z": round(zt, 3), "entry_close": sub_close[t]}

    return trades


def metrics(trades):
    if not trades: return None
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) < 0 else None
    cum = 0; peak = 0; mdd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum); mdd = max(mdd, peak - cum)
    sharpe = None
    if len(pnls) >= 10:
        m = sum(pnls)/len(pnls); s = statistics.stdev(pnls) if len(pnls)>=2 else 0
        if s > 0: sharpe = round(m / s * math.sqrt(50), 3)
    calmar = round(cum / mdd, 3) if mdd > 0 else None
    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins)/len(pnls), 3),
        "pf": round(pf, 4) if pf else None,
        "expectancy_pct": round(sum(pnls)/len(pnls), 3),
        "sum_pct": round(cum, 2),
        "mdd_pct": round(mdd, 2),
        "biggest_loss": round(min(pnls), 2),
        "biggest_win": round(max(pnls), 2),
        "stop_count": sum(1 for t in trades if t.get("exit_reason") == "STOP"),
        "sharpe_proxy": sharpe,
        "calmar": calmar,
    }


def bh_in_period(closes, slice_lo=0, slice_hi=None):
    if slice_hi is None: slice_hi = len(closes)
    seg = closes[slice_lo:slice_hi]
    if len(seg) < 2 or seg[0] <= 0: return None
    return (seg[-1] / seg[0] - 1) * 100


def kospi_corr_trades(trades, kospi_data):
    if not trades or not kospi_data: return None
    kd = dict(kospi_data); ksort=sorted(kospi_data); kdates=[x[0] for x in ksort]; kvals=[x[1] for x in ksort]
    daily = {}
    for t in trades:
        d = t["exit_date"]
        daily[d] = daily.get(d, 0) + t["pnl_pct"]
    pair_rets = []; kospi_rets = []
    for d, v in daily.items():
        if d not in kd: continue
        idx = kdates.index(d) if d in kdates else None
        if idx is None or idx == 0: continue
        kr = (kvals[idx]/kvals[idx-1] - 1) * 100
        pair_rets.append(v); kospi_rets.append(kr)
    if len(pair_rets) < 10: return None
    n = len(pair_rets)
    mp = sum(pair_rets)/n; mk = sum(kospi_rets)/n
    cov = sum((p-mp)*(k-mk) for p, k in zip(pair_rets, kospi_rets)) / (n-1)
    sp = math.sqrt(sum((p-mp)**2 for p in pair_rets)/(n-1))
    sk = math.sqrt(sum((k-mk)**2 for k in kospi_rets)/(n-1))
    return round(cov/(sp*sk), 3) if sp > 0 and sk > 0 else None


def main():
    # load lens cap buckets
    lens = {}
    if LENS_UNIVERSE.exists():
        for x in json.load(open(LENS_UNIVERSE, encoding="utf-8")):
            lens[x["symbol"]] = x["bucket"]

    kospi = load_daily(KOSPI)
    print(f"KOSPI: {len(kospi)} days")

    syms = sorted([p.stem for p in DATA_DIR.glob("*.csv") if p.stem != "KS11"])
    print(f"Symbols: {len(syms)}")
    print(f"With cap bucket: {sum(1 for s in syms if s in lens)}")

    # bucket assignment
    bucket_count = defaultdict(int)
    for s in syms: bucket_count[lens.get(s, "UNK")] += 1
    print(f"Buckets: {dict(bucket_count)}")

    out = {
        "structure": {
            "entry_z_values": ENTRY_Z_VALUES,
            "lookbacks":      LOOKBACKS,
            "exit_z":         EXIT_Z,
            "stop_z":         STOP_Z,
            "time_stop_days": TIME_STOP_DAYS,
        },
        "lookahead_principle": "★ rolling z[t] uses closes[t-N:t] (t 미포함).",
        "n_symbols":          len(syms),
        "n_param_combos":     len(ENTRY_Z_VALUES) * len(LOOKBACKS),
        "per_symbol_param":   [],
    }

    print(f"\nRunning sim: {len(syms)} syms × {len(ENTRY_Z_VALUES) * len(LOOKBACKS)} params...")
    for s in syms:
        d = load_daily(s)
        if len(d) < 300: continue
        closes = [x[1] for x in d]; dates = [x[0] for x in d]
        bucket = cap_bucket(s, lens)
        bh_full = bh_in_period(closes)
        mid = len(closes) // 2
        bh_in = bh_in_period(closes, 0, mid)
        bh_oos = bh_in_period(closes, mid)

        for entry_z in ENTRY_Z_VALUES:
            for lookback in LOOKBACKS:
                # full
                trades_full = simulate_zpullback(closes, dates, entry_z, lookback, bucket)
                m_full = metrics(trades_full)
                # in
                trades_in = simulate_zpullback(closes, dates, entry_z, lookback, bucket, 0, mid)
                m_in = metrics(trades_in)
                # oos
                trades_oos = simulate_zpullback(closes, dates, entry_z, lookback, bucket, mid)
                m_oos = metrics(trades_oos)
                kospi_c = kospi_corr_trades(trades_full or [], kospi)

                excess_full = (m_full["sum_pct"] - bh_full) if (m_full and bh_full is not None) else None
                excess_oos = (m_oos["sum_pct"] - bh_oos) if (m_oos and bh_oos is not None) else None

                out["per_symbol_param"].append({
                    "symbol":  s,
                    "bucket":  bucket,
                    "entry_z": entry_z,
                    "lookback": lookback,
                    "n_days":  len(closes),
                    "bh_full_pct":  bh_full,
                    "bh_oos_pct":   bh_oos,
                    "strat_full_pct":  m_full["sum_pct"] if m_full else None,
                    "strat_oos_pct":   m_oos["sum_pct"] if m_oos else None,
                    "strat_full_pf":   m_full["pf"] if m_full else None,
                    "strat_oos_pf":    m_oos["pf"] if m_oos else None,
                    "n_trades_full":   m_full["n_trades"] if m_full else 0,
                    "n_trades_oos":    m_oos["n_trades"] if m_oos else 0,
                    "mdd_pct":         m_full["mdd_pct"] if m_full else None,
                    "biggest_loss":    m_full["biggest_loss"] if m_full else None,
                    "sharpe":          m_full["sharpe_proxy"] if m_full else None,
                    "calmar":          m_full["calmar"] if m_full else None,
                    "kospi_corr":      kospi_c,
                    "excess_full_pct": round(excess_full, 2) if excess_full is not None else None,
                    "excess_oos_pct":  round(excess_oos, 2) if excess_oos is not None else None,
                })

    print(f"  done. total measurements: {len(out['per_symbol_param'])}")

    # ── aggregate by (bucket × param) ──
    rows = out["per_symbol_param"]
    print(f"\n=== Z3 — 3 벤치마크 비교 (전체 sym + 모든 파라미터 평균) ===")
    bh_full_pcts = [r["bh_full_pct"] for r in rows if r["bh_full_pct"] is not None]
    strat_full_pcts = [r["strat_full_pct"] for r in rows if r["strat_full_pct"] is not None]
    print(f"  buy&hold sum (median per sym×param): {statistics.median(bh_full_pcts):+.1f}%")
    print(f"  z pullback sum (median): {statistics.median(strat_full_pcts):+.1f}%")
    excesses = [r["excess_full_pct"] for r in rows if r["excess_full_pct"] is not None]
    excesses_oos = [r["excess_oos_pct"] for r in rows if r["excess_oos_pct"] is not None]
    print(f"  excess full (strat - BH, median per sym×param): {statistics.median(excesses):+.1f}%pt")
    print(f"  excess full > 0 count: {sum(1 for e in excesses if e>0)}/{len(excesses)} ({sum(1 for e in excesses if e>0)/len(excesses)*100:.1f}%)")
    print(f"  excess OOS (median): {statistics.median(excesses_oos):+.1f}%pt")
    print(f"  excess OOS > 0 count: {sum(1 for e in excesses_oos if e>0)}/{len(excesses_oos)} ({sum(1 for e in excesses_oos if e>0)/len(excesses_oos)*100:.1f}%)")

    # ── per-bucket aggregation ──
    print(f"\n=== Z7 — 시총 층화 (각 bucket × 파라미터 평균) ===")
    by_bp = defaultdict(list)
    for r in rows:
        if r["excess_oos_pct"] is None: continue
        by_bp[(r["bucket"], r["entry_z"], r["lookback"])].append(r["excess_oos_pct"])

    cells = []
    for (b, ez, lb), exs in sorted(by_bp.items()):
        if not exs: continue
        cells.append({
            "bucket": b, "entry_z": ez, "lookback": lb,
            "n_syms": len(exs),
            "median_excess_oos": round(statistics.median(exs), 2),
            "mean_excess_oos":   round(sum(exs)/len(exs), 2),
            "positive_count":    sum(1 for e in exs if e > 0),
        })
    out["bucket_param_aggregate"] = cells
    # show top cells
    print(f"  {'bucket':6s} {'z':5s} {'lb':>3s} {'n':>3s} {'med_exc_oos':>12s} {'pos':>5s}")
    for c in sorted(cells, key=lambda x: x["median_excess_oos"], reverse=True)[:15]:
        print(f"  {c['bucket']:6s} {c['entry_z']:>5} {c['lookback']:>3} {c['n_syms']:>3} {c['median_excess_oos']:>12.2f} {c['positive_count']:>3}/{c['n_syms']:<3}")

    # ── OOS PF≥1.2 통과율 + 우연 보정 (Z5) ──
    pf_pass_full = sum(1 for r in rows if r.get("strat_full_pf") and r["strat_full_pf"] >= 1.2)
    pf_pass_oos = sum(1 for r in rows if r.get("strat_oos_pf") and r["strat_oos_pf"] >= 1.2)
    n_total = len(rows)
    out["pass_rates"] = {
        "n_measurements_total":  n_total,
        "pf_full_pass":          pf_pass_full,
        "pf_full_pass_rate":     round(pf_pass_full/n_total, 3) if n_total else 0,
        "pf_oos_pass":           pf_pass_oos,
        "pf_oos_pass_rate":      round(pf_pass_oos/n_total, 3) if n_total else 0,
        "excess_full_positive":  sum(1 for e in excesses if e > 0),
        "excess_oos_positive":   sum(1 for e in excesses_oos if e > 0),
        "expected_random_pct":   35,
    }
    print(f"\n=== Z5 — 다중비교 (N={n_total} 측정) ===")
    print(f"  PF full ≥ 1.2: {pf_pass_full}/{n_total} ({pf_pass_full/n_total*100:.1f}%, random ~35%)")
    print(f"  PF OOS  ≥ 1.2: {pf_pass_oos}/{n_total} ({pf_pass_oos/n_total*100:.1f}%, random ~35%)")
    print(f"  excess full > 0: {sum(1 for e in excesses if e>0)}/{len(excesses)} ({sum(1 for e in excesses if e>0)/len(excesses)*100:.1f}%, random ~50%)")
    print(f"  excess OOS > 0: {sum(1 for e in excesses_oos if e>0)}/{len(excesses_oos)} ({sum(1 for e in excesses_oos if e>0)/len(excesses_oos)*100:.1f}%, random ~50%)")

    # ── Z6 — market neutral 분포 ──
    kospi_corrs = [r["kospi_corr"] for r in rows if r["kospi_corr"] is not None]
    if kospi_corrs:
        neutral_count = sum(1 for c in kospi_corrs if abs(c) < 0.2)
        out["market_neutrality"] = {
            "n_with_corr":   len(kospi_corrs),
            "median_corr":   round(statistics.median(kospi_corrs), 3),
            "neutral_count": neutral_count,
            "neutral_rate":  round(neutral_count/len(kospi_corrs), 3),
        }
        print(f"\n=== Z6 — market-neutral ===")
        print(f"  median KOSPI corr: {statistics.median(kospi_corrs):+.3f}")
        print(f"  |corr|<0.2: {neutral_count}/{len(kospi_corrs)} ({neutral_count/len(kospi_corrs)*100:.0f}%)")

    # ── Verdict ──
    print(f"\n=== VERDICT ===")
    excess_oos_pos_rate = sum(1 for e in excesses_oos if e > 0) / max(1, len(excesses_oos))
    print(f"  excess OOS > 0 rate: {excess_oos_pos_rate*100:.1f}% (random ~50%)")
    if excess_oos_pos_rate >= 0.6:
        verdict = "Z_PULLBACK_BEATS_BH_FREQUENTLY"
    elif excess_oos_pos_rate >= 0.5:
        verdict = "Z_PULLBACK_MARGINAL_VS_BH"
    else:
        verdict = "Z_PULLBACK_FAILS_BEAT_BH"
    out["verdict"] = verdict
    print(f"  → {verdict}")

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/zpullback.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nwrote reports/backtest/zpullback.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
