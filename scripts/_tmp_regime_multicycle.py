#!/usr/bin/env python3
"""TEMP — Regime Filter 다중 cycle + signal sweep (D1-D4).

★ Lookahead-free 원칙: 모든 신호는 closes[t-N:t] (t 미포함).
★ Measurement: per-symbol median (sum/n 금지).
★ 핵심 판정: 약세 방어 + 활황 손실 합산 → 그냥 buy&hold 보다 나은가.

Data: yf_multiyear 12 syms + KOSPI (2000-01 ~ 2026-05).

Cycle 식별: KOSPI peak-to-trough drawdown ≥ 20% 구간.

Signal sweep (D4):
  SMA_20_60  : SMA20 < SMA60
  SMA_50_200 : SMA50 < SMA200 (Death Cross — 표준)
  ATR_BEAR   : close < peak - 2 * ATR_20
  VOL_BREAK  : stddev_20 > 1.5 * stddev_60 (변동성 급등)
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data/market/yf_multiyear")
TICKERS = ['005930', '000660', '005380', '000270', '035420', '035720',
           '051910', '005490', '055550', '068270', '006400', '012330']
KOSPI = 'KS11'

# Cost model
COMMISSION_BPS = 1.5
SLIP_BPS = 5.0
TAX_BPS = 20.0
COST_EXIT_BPS = COMMISSION_BPS + SLIP_BPS + TAX_BPS   # 26.5
COST_ENTRY_BPS = COMMISSION_BPS + SLIP_BPS            # 6.5


def load_daily(symbol: str) -> list[tuple[str, float]]:
    p = DATA_DIR / f"{symbol}.csv"
    if not p.exists(): return []
    out = []
    with open(p, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        if not r[0] or not r[4]: continue
        try:
            d = r[0][:10]
            c = float(r[4])
            if c > 0:
                out.append((d, c))
        except (ValueError, IndexError):
            continue
    out.sort()
    return out


# ─────────────────────────────────────────────────────────────────
# Signals (all lookahead-free: use closes[t-N:t], t-1 까지)
# ─────────────────────────────────────────────────────────────────


def sig_sma_cross(closes, fast=20, slow=60):
    """bear[t] = SMA(t-fast:t) < SMA(t-slow:t)"""
    n = len(closes)
    out = [None] * n
    for t in range(slow, n):
        f = sum(closes[t-fast:t]) / fast
        s = sum(closes[t-slow:t]) / slow
        out[t] = (f < s)
    return out


def sig_atr_bear(closes, atr_window=20, mult=2.0, hi_window=60):
    """bear[t] = close[t-1] < peak[t-hi_window:t] - mult * ATR[t-atr:t]

    ATR proxy = mean abs diff(t-window:t) (간단 — true ATR 는 high-low 필요).
    """
    n = len(closes)
    out = [None] * n
    for t in range(max(atr_window, hi_window) + 1, n):
        atr = sum(abs(closes[i] - closes[i-1]) for i in range(t-atr_window, t)) / atr_window
        peak = max(closes[t-hi_window:t])
        out[t] = (closes[t-1] < peak - mult * atr)
    return out


def sig_vol_breakout(closes, fast=20, slow=60, ratio=1.5):
    """bear[t] = stddev(t-fast:t) > ratio * stddev(t-slow:t) AND fast-mean falling."""
    n = len(closes)
    out = [None] * n
    for t in range(slow + 1, n):
        rets_fast = [closes[i] - closes[i-1] for i in range(t-fast, t)]
        rets_slow = [closes[i] - closes[i-1] for i in range(t-slow, t)]
        if len(rets_fast) < 2 or len(rets_slow) < 2: continue
        sd_fast = statistics.stdev(rets_fast)
        sd_slow = statistics.stdev(rets_slow)
        # also require falling fast SMA
        sma_fast_now = sum(closes[t-fast:t]) / fast
        sma_fast_prev = sum(closes[t-fast-5:t-5]) / fast if t >= fast+5 else sma_fast_now
        falling = sma_fast_now < sma_fast_prev
        out[t] = (sd_fast > ratio * sd_slow) and falling
    return out


SIGNALS = {
    "SMA_20_60":  lambda c: sig_sma_cross(c, 20, 60),
    "SMA_50_200": lambda c: sig_sma_cross(c, 50, 200),
    "ATR_BEAR":   lambda c: sig_atr_bear(c, 20, 2.0, 60),
    "VOL_BREAK":  lambda c: sig_vol_breakout(c, 20, 60, 1.5),
}


# ─────────────────────────────────────────────────────────────────
# Equity simulation
# ─────────────────────────────────────────────────────────────────


def simulate_bh(closes, start_idx, end_idx):
    """Pure buy&hold over [start_idx, end_idx] inclusive."""
    seg = closes[start_idx:end_idx+1]
    if len(seg) < 2 or seg[0] <= 0: return None
    final = seg[-1] / seg[0] - 1.0
    peak = seg[0]; mdd = 0
    for c in seg:
        peak = max(peak, c)
        mdd = max(mdd, (peak - c) / peak)
    daily = [seg[i]/seg[i-1] - 1 for i in range(1, len(seg)) if seg[i-1] > 0]
    sharpe = None
    if len(daily) >= 20:
        m = statistics.mean(daily); s = statistics.stdev(daily)
        if s > 0: sharpe = round(m / s * math.sqrt(252), 3)
    return {"final_return": round(final, 4), "mdd": round(mdd, 4),
            "sharpe": sharpe, "n_days": len(seg)}


def simulate_filter(closes, signal_series, start_idx, end_idx,
                    cost_exit_bps=COST_EXIT_BPS, cost_entry_bps=COST_ENTRY_BPS):
    """Regime filter over [start_idx, end_idx]. Start in stock."""
    if start_idx >= end_idx: return None
    eq = 1.0
    eq_curve = [eq]
    in_stock = True
    trans = 0; cost_accum = 0.0
    for t in range(start_idx + 1, end_idx + 1):
        sig = signal_series[t-1]   # ★ signal from PREVIOUS day
        # apply return
        if closes[t-1] > 0 and in_stock:
            eq *= (closes[t] / closes[t-1])
        eq_curve.append(eq)
        # rebalance based on signal
        if sig is not None:
            want_stock = (not sig)
            if want_stock and not in_stock:
                eq *= (1 - cost_entry_bps / 1e4)
                cost_accum += cost_entry_bps / 1e4
                in_stock = True
                trans += 1
            elif (not want_stock) and in_stock:
                eq *= (1 - cost_exit_bps / 1e4)
                cost_accum += cost_exit_bps / 1e4
                in_stock = False
                trans += 1

    final = eq - 1.0
    peak = eq_curve[0]; mdd = 0
    for c in eq_curve:
        peak = max(peak, c)
        if peak > 0: mdd = max(mdd, (peak - c) / peak)
    daily = [eq_curve[i]/eq_curve[i-1] - 1 for i in range(1, len(eq_curve)) if eq_curve[i-1] > 0]
    sharpe = None
    if len(daily) >= 20:
        m = statistics.mean(daily); s = statistics.stdev(daily)
        if s > 0: sharpe = round(m / s * math.sqrt(252), 3)
    return {"final_return": round(final, 4), "mdd": round(mdd, 4),
            "sharpe": sharpe, "transitions": trans,
            "cost_paid": round(cost_accum, 4), "n_days": len(eq_curve)}


# ─────────────────────────────────────────────────────────────────
# Cycle detection from KOSPI
# ─────────────────────────────────────────────────────────────────


def detect_bear_cycles(kospi_data, min_drawdown=0.20):
    """Detect peak-to-trough drawdowns ≥ min_drawdown in KOSPI.

    Returns list of (peak_date, peak_idx, trough_date, trough_idx, recovery_date, recovery_idx, drawdown).
    """
    closes = [c for _, c in kospi_data]
    dates = [d for d, _ in kospi_data]
    n = len(closes)
    cycles = []
    i = 0
    while i < n:
        # find next peak (running max so far)
        peak_idx = i
        peak_val = closes[i]
        # find trough following peak
        # rolling: peak = highest so far; trough = lowest after peak
        running_peak_idx = i
        for j in range(i, n):
            if closes[j] > closes[running_peak_idx]:
                running_peak_idx = j
            dd = (closes[running_peak_idx] - closes[j]) / closes[running_peak_idx]
            if dd >= min_drawdown:
                # trough = lowest from running_peak_idx to first recovery
                trough_idx = j
                trough_val = closes[j]
                for k in range(j, n):
                    if closes[k] < trough_val:
                        trough_val = closes[k]
                        trough_idx = k
                    # recovery: close ≥ peak
                    if closes[k] >= closes[running_peak_idx]:
                        recovery_idx = k
                        break
                else:
                    recovery_idx = n - 1   # no recovery in data
                drawdown = (closes[running_peak_idx] - trough_val) / closes[running_peak_idx]
                cycles.append({
                    "peak_date":      dates[running_peak_idx],
                    "peak_idx":       running_peak_idx,
                    "peak_value":     closes[running_peak_idx],
                    "trough_date":    dates[trough_idx],
                    "trough_idx":     trough_idx,
                    "trough_value":   trough_val,
                    "recovery_date":  dates[recovery_idx],
                    "recovery_idx":   recovery_idx,
                    "drawdown":       round(drawdown, 4),
                    "duration_days":  recovery_idx - running_peak_idx,
                })
                i = recovery_idx + 1
                break
        else:
            break
    return cycles


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────


def main():
    # Load KOSPI for cycle detection
    print("Loading KOSPI for cycle detection...")
    kospi = load_daily(KOSPI)
    print(f"  KOSPI: {len(kospi)} days, {kospi[0][0]} → {kospi[-1][0]}")

    cycles = detect_bear_cycles(kospi, min_drawdown=0.20)
    print(f"\n=== Detected bear cycles (KOSPI peak-to-trough ≥ 20%) ===")
    for c in cycles:
        print(f"  {c['peak_date']} ({int(c['peak_value']):>6}) → {c['trough_date']} ({int(c['trough_value']):>5}) "
              f"DD={c['drawdown']*100:.1f}% recovery={c['recovery_date']} duration={c['duration_days']}d")

    # Load all stocks
    print(f"\nLoading {len(TICKERS)} stocks...")
    stock_data = {}
    for s in TICKERS:
        d = load_daily(s)
        if d: stock_data[s] = d
    print(f"  loaded {len(stock_data)} syms")

    # Align dates across all stocks + KOSPI (for filter to use KOSPI-based signal optionally)
    # For simplicity: per-stock independent signals (each stock uses own closes for signals).
    # This is what we did in regime_filter_20260530.

    out = {
        "lookahead_principle": "★ closes[t-N:t] EXCLUDES t. signal at t-1 fires next-day. 정적 grep [t+|i+] 매치 0.",
        "measurement_principle": "★ per-symbol median (sum/n 금지).",
        "data_source":     "yfinance daily, 2000-01 ~ 2026-05",
        "n_stocks":        len(stock_data),
        "n_cycles":        len(cycles),
        "cycles":          cycles,
        "signal_sweep":    {},
        "cycle_results":   {},
        "full_period":     {},
        "verdict":         None,
    }

    # ─── D4 + D2: per-signal × per-cycle MDD/return analysis ───
    print("\n=== Per-cycle × per-signal results (12 stocks) ===")
    for sig_name, sig_fn in SIGNALS.items():
        print(f"\n-- Signal: {sig_name} --")
        cycle_summary = {}
        for c in cycles:
            peak_date = c['peak_date']
            recovery_date = c['recovery_date']
            sym_filter_bh_pairs = []
            for sym, sd in stock_data.items():
                # find sym indices for peak/recovery
                sym_dates = [d for d, _ in sd]
                if peak_date < sym_dates[0] or recovery_date > sym_dates[-1]:
                    continue
                start_idx = next((i for i, d in enumerate(sym_dates) if d >= peak_date), None)
                end_idx = next((i for i, d in enumerate(sym_dates) if d >= recovery_date), len(sym_dates) - 1)
                if start_idx is None or start_idx >= end_idx: continue
                closes = [cl for _, cl in sd]
                sig_ser = sig_fn(closes)
                bh = simulate_bh(closes, start_idx, end_idx)
                ft = simulate_filter(closes, sig_ser, start_idx, end_idx)
                if bh and ft:
                    sym_filter_bh_pairs.append((sym, bh, ft))
            if not sym_filter_bh_pairs: continue
            # aggregate
            mdd_bh = statistics.median([p[1]["mdd"] for p in sym_filter_bh_pairs])
            mdd_ft = statistics.median([p[2]["mdd"] for p in sym_filter_bh_pairs])
            ret_bh = statistics.median([p[1]["final_return"] for p in sym_filter_bh_pairs])
            ret_ft = statistics.median([p[2]["final_return"] for p in sym_filter_bh_pairs])
            excess = ret_ft - ret_bh
            mdd_reduction = mdd_bh - mdd_ft
            cycle_summary[c["peak_date"]] = {
                "trough":  c["trough_date"],
                "kospi_dd": c["drawdown"],
                "n_syms":  len(sym_filter_bh_pairs),
                "median_bh_return":  round(ret_bh, 4),
                "median_ft_return":  round(ret_ft, 4),
                "median_excess":     round(excess, 4),
                "median_bh_mdd":     round(mdd_bh, 4),
                "median_ft_mdd":     round(mdd_ft, 4),
                "median_mdd_reduction": round(mdd_reduction, 4),
            }
            print(f"  cycle {c['peak_date']} (DD={c['drawdown']*100:.0f}%): "
                  f"BH ret={ret_bh*100:+.1f}% MDD={mdd_bh*100:.1f}% / "
                  f"FT ret={ret_ft*100:+.1f}% MDD={mdd_ft*100:.1f}% / "
                  f"excess={excess*100:+.1f}%pt MDD↓={mdd_reduction*100:+.1f}%pt n={len(sym_filter_bh_pairs)}")
        out["cycle_results"][sig_name] = cycle_summary

    # ─── D3: 종합 — 전체 26년 합산 (full period) ───
    print("\n=== Full period (full data range per sym) — 종합 ===")
    for sig_name, sig_fn in SIGNALS.items():
        print(f"\n-- Signal: {sig_name} --")
        pairs = []
        for sym, sd in stock_data.items():
            closes = [c for _, c in sd]
            sig_ser = sig_fn(closes)
            # full period
            start_idx = 200   # skip warmup (200-day MA needs it)
            end_idx = len(closes) - 1
            if start_idx >= end_idx: continue
            bh = simulate_bh(closes, start_idx, end_idx)
            ft = simulate_filter(closes, sig_ser, start_idx, end_idx)
            if bh and ft:
                pairs.append((sym, bh, ft))
        if not pairs: continue
        bh_rets = [p[1]["final_return"] for p in pairs]
        ft_rets = [p[2]["final_return"] for p in pairs]
        bh_mdds = [p[1]["mdd"] for p in pairs]
        ft_mdds = [p[2]["mdd"] for p in pairs]
        excess  = [p[2]["final_return"] - p[1]["final_return"] for p in pairs]
        trans   = [p[2]["transitions"] for p in pairs]
        cost    = [p[2]["cost_paid"] for p in pairs]

        full = {
            "n_syms":           len(pairs),
            "median_bh_return": round(statistics.median(bh_rets), 4),
            "median_ft_return": round(statistics.median(ft_rets), 4),
            "median_bh_mdd":    round(statistics.median(bh_mdds), 4),
            "median_ft_mdd":    round(statistics.median(ft_mdds), 4),
            "median_excess":    round(statistics.median(excess), 4),
            "mean_excess":      round(statistics.mean(excess), 4),
            "median_transitions": int(statistics.median(trans)),
            "median_cost_paid": round(statistics.median(cost), 4),
            "per_sym": [{"symbol": s, "bh_return": bh["final_return"],
                          "ft_return": ft["final_return"],
                          "excess": ft["final_return"] - bh["final_return"],
                          "bh_mdd": bh["mdd"], "ft_mdd": ft["mdd"]}
                         for s, bh, ft in pairs],
        }
        out["full_period"][sig_name] = full
        print(f"  syms={len(pairs)}  median BH={statistics.median(bh_rets)*100:+.0f}%  median FT={statistics.median(ft_rets)*100:+.0f}%  "
              f"excess={statistics.median(excess)*100:+.1f}%pt  BH_MDD={statistics.median(bh_mdds)*100:.0f}%  FT_MDD={statistics.median(ft_mdds)*100:.0f}%  "
              f"trans={int(statistics.median(trans))}  cost={statistics.median(cost)*100:.2f}%")

    # ─── Verdict ───
    print("\n=== VERDICT ===")
    # rule: 종합 (full period) median excess > 0 for ≥3 of 4 signals AND cycle-level MDD reduction in ≥3 cycles for ≥1 signal
    full_excess_pass = sum(1 for v in out["full_period"].values()
                           if v["median_excess"] > 0)
    print(f"  Signals with median excess > 0 in full period: {full_excess_pass}/4")

    cycle_robust = {}
    for sig_name, cycles_dict in out["cycle_results"].items():
        n_mdd_reduction = sum(1 for c in cycles_dict.values() if c["median_mdd_reduction"] > 0.02)
        n_total = len(cycles_dict)
        cycle_robust[sig_name] = (n_mdd_reduction, n_total)
        print(f"  {sig_name}: cycles with MDD reduction ≥2%pt: {n_mdd_reduction}/{n_total}")

    consistent_defense = sum(1 for s, (r, t) in cycle_robust.items() if r >= t * 0.6 and t >= 3)
    print(f"  Signals with cycle-consistent defense (≥60% of ≥3 cycles): {consistent_defense}/4")

    if full_excess_pass >= 3 and consistent_defense >= 3:
        verdict = "REGIME_FILTER_CONFIRMED_MULTICYCLE"
    elif full_excess_pass >= 2 and consistent_defense >= 2:
        verdict = "REGIME_FILTER_PARTIAL_DEFENSE"
    elif full_excess_pass >= 1:
        verdict = "REGIME_FILTER_SIGNAL_DEPENDENT_ONLY"
    else:
        verdict = "REGIME_FILTER_FAILED_MULTICYCLE"

    out["verdict"] = {
        "full_excess_pass":    full_excess_pass,
        "consistent_defense":  consistent_defense,
        "cycle_robustness":    {s: f"{r}/{t}" for s, (r, t) in cycle_robust.items()},
        "decision":            verdict,
    }
    print(f"  → {verdict}")

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/regime_multicycle.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nwrote reports/backtest/regime_multicycle.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
