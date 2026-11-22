#!/usr/bin/env python3
"""TEMP — Council×대형 4중 결정적 검증 (F1 forward / F2 regime / F3 risk / F4 18-sym).

★ 판정 기준: PF 절대값 아니라 ★buy&hold 초과수익 으로만.

NO new strategy, NO simulator, NO broker / route_order / place_order.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.backtest import metrics
from app.backtest.strategy_council_backtest import (
    BacktestInput, collect_backtest_trades, load_ohlcv_from_csv,
)

# Constants (per lens_rebuild_20260530 results)
LENS_DIR = Path("data/market/lens_60m")
BIG_20 = ['005930', '000660', '122630', '069500', '005380', '034020', '079550',
          '010120', '035420', '229200', '042660', '114800', '102110', '012450',
          '080220', '006400', '042700', '086520', '196170', '267260']
TOP2_OUTLIER = ['005930', '102110']
BIG_18 = [s for s in BIG_20 if s not in TOP2_OUTLIER]

HOLD_BARS = 64        # 1-2주
MIN_QUALITY = 80.0
COST_BPS_BIG = 33.0   # round-trip for 대형 bucket (5bps slip side)
COST_FRACTION = COST_BPS_BIG / 1e4
UNFILLED_RATE = 0.05


def _load_bars(symbols):
    bars = []
    for s in symbols:
        p = LENS_DIR / f"{s}_60m.csv"
        if p.exists():
            bars.extend(load_ohlcv_from_csv(str(p)))
    return bars


def _index_by_sym(bars):
    by_sym = defaultdict(list)
    for b in bars:
        by_sym[b.symbol].append(b)
    for s in by_sym:
        by_sym[s].sort(key=lambda x: x.timestamp)
    return by_sym


def _fwd_return(bars_by_sym, sym, ts_iso, hold_bars):
    seq = bars_by_sym.get(sym)
    if not seq: return None
    sig_ts = datetime.fromisoformat(ts_iso)
    for i, b in enumerate(seq):
        if b.timestamp == sig_ts:
            if i + hold_bars >= len(seq): return None
            entry, ext = b.close, seq[i + hold_bars].close
            if entry <= 0: return None
            return (ext / entry) - 1.0
    return None


def _bh_in_period(bars_by_sym, symbols, start_ts, end_ts):
    """Per-symbol buy&hold over given period. Returns {sym: bh_return}."""
    out = {}
    for sym in symbols:
        seq = [b for b in bars_by_sym.get(sym, [])
               if start_ts <= b.timestamp <= end_ts]
        if len(seq) < 2: continue
        if seq[0].close <= 0: continue
        out[sym] = seq[-1].close / seq[0].close - 1.0
    return out


def _strategy_period_stats(buy_trades, bars_by_sym, hold_bars, cost_frac,
                           start_ts=None, end_ts=None):
    """Per-period: returns dict with pf, win, n, per-sym net total returns, equity curve."""
    pairs = []
    for t in buy_trades:
        ts = datetime.fromisoformat(t.timestamp)
        if start_ts and ts < start_ts: continue
        if end_ts and ts > end_ts: continue
        r = _fwd_return(bars_by_sym, t.symbol, t.timestamp, hold_bars)
        if r is None: continue
        pairs.append((t, ts, r))

    # apply adverse selection
    pairs_net = [(t, ts, g - cost_frac) for t, ts, g in pairs]
    if UNFILLED_RATE > 0 and pairs_net:
        k = int(len(pairs_net) * UNFILLED_RATE)
        if k > 0:
            top = sorted(range(len(pairs_net)),
                         key=lambda i: pairs_net[i][2], reverse=True)[:k]
            drop = set(top)
            pairs_net = [p for i, p in enumerate(pairs_net) if i not in drop]

    if not pairs_net:
        return None

    # PF / win
    recs = [{"pnl": int(round(n * t.signal_price)), "entry_price": t.signal_price,
             "quantity": 1, "exit_ts": t.timestamp, "_net": n}
            for t, ts, n in pairs_net]
    pf = metrics.profit_factor(recs)
    wr = metrics.win_rate(recs)

    # per-symbol net total return
    per_sym_returns: dict[str, list[float]] = defaultdict(list)
    for t, ts, n in pairs_net:
        per_sym_returns[t.symbol].append(n)

    # sum/avg per symbol
    per_sym_summary = {}
    for sym, rets in per_sym_returns.items():
        per_sym_summary[sym] = {
            "n_trades":  len(rets),
            "net_sum":   sum(rets),
            "net_mean":  statistics.mean(rets),
        }

    # equity curve — chronological, single-position assumption
    sorted_pairs = sorted(pairs_net, key=lambda x: x[1])
    equity = 1.0
    curve = [(sorted_pairs[0][1] if sorted_pairs else None, 1.0)]
    peak = 1.0; mdd_pct = 0.0
    for _, ts, n in sorted_pairs:
        equity *= (1 + n)
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100
            mdd_pct = max(mdd_pct, dd)
        curve.append((ts, equity))

    # daily returns from equity curve for sharpe
    daily_returns = []
    for i in range(1, len(curve)):
        if curve[i-1][1] > 0:
            daily_returns.append((curve[i][1] / curve[i-1][1]) - 1.0)
    sharpe = None
    if len(daily_returns) >= 5:
        m = statistics.mean(daily_returns)
        s = statistics.stdev(daily_returns)
        if s > 0:
            sharpe = round(m / s * math.sqrt(252), 3)  # annualized rough

    return {
        "n_trades":         len(pairs_net),
        "pf":               round(pf, 4) if pf is not None else None,
        "win_rate":         round(wr, 4) if wr is not None else None,
        "per_sym":          per_sym_summary,
        "equity_final":     round(equity, 4),
        "equity_return_pct": round((equity - 1.0) * 100, 2),
        "mdd_pct":          round(mdd_pct, 2),
        "sharpe_annualized": sharpe,
        "_period_start":    str(start_ts) if start_ts else None,
        "_period_end":      str(end_ts) if end_ts else None,
        "_n_pairs_signaled": len(pairs),
    }


def compute_excess(strategy_stats, bh_per_sym):
    """Excess return = median(strategy net_sum per symbol) − median(bh per symbol).

    Both metrics on same per-symbol scale.
    Also report the average for reference.
    """
    if not strategy_stats or not bh_per_sym: return None
    syms_active = set(strategy_stats["per_sym"].keys()) & set(bh_per_sym.keys())
    if not syms_active: return None
    strat_per_sym = [strategy_stats["per_sym"][s]["net_sum"] for s in syms_active]
    bh_per_sym_list = [bh_per_sym[s] for s in syms_active]
    return {
        "n_syms_active":          len(syms_active),
        "strat_per_sym_median":   round(statistics.median(strat_per_sym) * 100, 2),
        "strat_per_sym_mean":     round(statistics.mean(strat_per_sym) * 100, 2),
        "bh_per_sym_median":      round(statistics.median(bh_per_sym_list) * 100, 2),
        "bh_per_sym_mean":        round(statistics.mean(bh_per_sym_list) * 100, 2),
        "excess_median":          round((statistics.median(strat_per_sym) - statistics.median(bh_per_sym_list)) * 100, 2),
        "excess_mean":            round((statistics.mean(strat_per_sym) - statistics.mean(bh_per_sym_list)) * 100, 2),
    }


def _kospi_proxy_by_month(bars_by_sym, big20):
    """Use 대형 20 close-to-close *monthly* returns as KOSPI proxy.

    Returns: {(year, month): mean_monthly_return_across_20syms}
    """
    monthly_per_sym = defaultdict(list)
    for sym in big20:
        seq = bars_by_sym.get(sym, [])
        # group by month
        by_month = defaultdict(list)
        for b in seq:
            ym = (b.timestamp.year, b.timestamp.month)
            by_month[ym].append(b)
        prev_close = None
        for ym in sorted(by_month.keys()):
            month_bars = by_month[ym]
            if prev_close is not None and prev_close > 0:
                ret = month_bars[-1].close / prev_close - 1.0
                monthly_per_sym[ym].append(ret)
            prev_close = month_bars[-1].close
    return {ym: statistics.mean(rs) for ym, rs in monthly_per_sym.items() if rs}


def main():
    print("Loading 대형 20 bars...", flush=True)
    bars = _load_bars(BIG_20)
    bars_by_sym = _index_by_sym(bars)
    print(f"  loaded {len(bars)} bars across {len(bars_by_sym)} syms")

    all_ts = sorted({b.timestamp for sym_bars in bars_by_sym.values() for b in sym_bars})
    ts_min, ts_max = all_ts[0], all_ts[-1]
    print(f"  period: {ts_min} → {ts_max}")

    # ── Council BUY signals ──
    print("\nCollecting Council BUY signals (quality ≥ 80)...", flush=True)
    inp = BacktestInput(bars=tuple(bars), horizons=(5, 10, 30, 60))
    collected = collect_backtest_trades(inp)
    council_buys = [t for t in collected.council_trades
                    if t.signal == "BUY" and (t.quality_score or 0) >= MIN_QUALITY]
    print(f"  council BUYs: {len(council_buys)}")

    out = {
        "context": {
            "universe":      BIG_20,
            "n_universe":    len(BIG_20),
            "hold_bars":     HOLD_BARS,
            "min_quality":   MIN_QUALITY,
            "cost_bps":      COST_BPS_BIG,
            "period":        f"{ts_min} → {ts_max}",
        },
    }

    # ── F1a: full in-sample (baseline for context) ──
    print("\nF1 — full period strategy stats (baseline):", flush=True)
    full = _strategy_period_stats(council_buys, bars_by_sym, HOLD_BARS, COST_FRACTION)
    bh_full = _bh_in_period(bars_by_sym, BIG_20, ts_min, ts_max)
    f_excess = compute_excess(full, bh_full)
    out["f1_baseline_full"] = {**(full or {}), "excess": f_excess}
    print(f"  full: PF={full['pf']} n={full['n_trades']} equity={full['equity_final']} MDD={full['mdd_pct']}% Sharpe={full['sharpe_annualized']}")
    print(f"  excess: strat_median={f_excess['strat_per_sym_median']}% vs bh_median={f_excess['bh_per_sym_median']}% → +{f_excess['excess_median']:+.2f}%pt")

    # ── F1b: last 20% holdout (same as lens chrono, but with EXCESS now) ──
    cut_20 = all_ts[int(len(all_ts) * 0.80)]
    print(f"\nF1b — last 20% holdout (cut={cut_20})", flush=True)
    h20 = _strategy_period_stats(council_buys, bars_by_sym, HOLD_BARS, COST_FRACTION,
                                 start_ts=cut_20)
    bh_h20 = _bh_in_period(bars_by_sym, BIG_20, cut_20, ts_max)
    e_h20 = compute_excess(h20, bh_h20)
    out["f1b_holdout_last20pct"] = {**(h20 or {}), "excess": e_h20,
                                    "cut_ts": str(cut_20)}
    if h20: print(f"  PF={h20['pf']} n={h20['n_trades']} equity={h20['equity_final']} MDD={h20['mdd_pct']}%")
    if e_h20: print(f"  excess vs bh: strat={e_h20['strat_per_sym_median']}% vs bh={e_h20['bh_per_sym_median']}% → {e_h20['excess_median']:+.2f}%pt")

    # ── F1c: last 10% holdout (stricter, ~5 weeks) ──
    cut_10 = all_ts[int(len(all_ts) * 0.90)]
    print(f"\nF1c — last 10% holdout (stricter, cut={cut_10})", flush=True)
    h10 = _strategy_period_stats(council_buys, bars_by_sym, HOLD_BARS, COST_FRACTION,
                                 start_ts=cut_10)
    bh_h10 = _bh_in_period(bars_by_sym, BIG_20, cut_10, ts_max)
    e_h10 = compute_excess(h10, bh_h10)
    out["f1c_holdout_last10pct"] = {**(h10 or {}), "excess": e_h10,
                                    "cut_ts": str(cut_10)}
    if h10: print(f"  PF={h10['pf']} n={h10['n_trades']} equity={h10['equity_final']} MDD={h10['mdd_pct']}%")
    if e_h10: print(f"  excess: {e_h10['excess_median']:+.2f}%pt (strat={e_h10['strat_per_sym_median']}% bh={e_h10['bh_per_sym_median']}%)")

    # ── F2: regime decomposition by month ──
    print("\nF2 — monthly regime decomposition (KOSPI proxy = 대형 20 mean monthly)", flush=True)
    monthly_kospi = _kospi_proxy_by_month(bars_by_sym, BIG_20)
    months_sorted = sorted(monthly_kospi.keys())
    regime_buckets = {"UP": [], "FLAT": [], "DOWN": []}
    for ym in months_sorted:
        r = monthly_kospi[ym]
        if r > 0.03:    regime_buckets["UP"].append(ym)
        elif r < -0.03: regime_buckets["DOWN"].append(ym)
        else:           regime_buckets["FLAT"].append(ym)

    f2_results = {"kospi_proxy_monthly": {f"{y}-{m:02d}": round(r * 100, 2)
                                           for (y, m), r in monthly_kospi.items()}}
    for regime, months in regime_buckets.items():
        if not months:
            f2_results[regime] = {"months": [], "note": "no months in this regime"}
            continue
        # Build mask of trades+bh in those months
        in_regime = lambda dt: (dt.year, dt.month) in set(months)
        sub_trades = [t for t in council_buys
                      if in_regime(datetime.fromisoformat(t.timestamp))]
        # period bounds
        per_starts = [datetime(y, m, 1) for (y, m) in months]
        per_ends = [datetime(y, m, 28) + timedelta(days=6) for (y, m) in months]
        # collect bars within months
        # strat stats
        if not sub_trades:
            f2_results[regime] = {"months": months, "n_trades": 0}
            continue
        stats = _strategy_period_stats(sub_trades, bars_by_sym, HOLD_BARS, COST_FRACTION)
        # bh = sum of monthly KOSPI proxy per regime
        bh_total_period = sum(monthly_kospi[ym] for ym in months) * 100
        excess = None
        if stats and stats.get("per_sym"):
            strat_med = statistics.median([s["net_sum"] for s in stats["per_sym"].values()]) * 100
            excess = round(strat_med - bh_total_period, 2)
        f2_results[regime] = {
            "months":          [f"{y}-{m:02d}" for (y, m) in months],
            "n_months":        len(months),
            "n_trades":        stats["n_trades"] if stats else 0,
            "pf":              stats["pf"] if stats else None,
            "strat_per_sym_median_pct": (statistics.median([s["net_sum"] for s in stats["per_sym"].values()]) * 100
                                          if stats and stats.get("per_sym") else None),
            "bh_proxy_sum_pct": round(bh_total_period, 2),
            "excess_median_pct": excess,
            "kospi_proxy_avg_monthly_pct": round(statistics.mean([monthly_kospi[ym] for ym in months]) * 100, 2),
        }
        print(f"  {regime} months={len(months)} n_trades={stats['n_trades'] if stats else 0} "
              f"strat_med={f2_results[regime]['strat_per_sym_median_pct']:.1f}% "
              f"bh_proxy={bh_total_period:.1f}% excess={excess}%pt")

    out["f2_regime_decomposition"] = f2_results

    # ── F3: risk-adjusted vs buy&hold ──
    print("\nF3 — risk-adjusted comparison (MDD, Sharpe, vol vs buy&hold)", flush=True)
    # buy&hold equity curve: mean of 20 syms, compounded month by month using monthly_kospi
    bh_equity = 1.0; bh_peak = 1.0; bh_mdd = 0.0
    bh_monthly = []
    for ym in months_sorted:
        r = monthly_kospi[ym]
        bh_equity *= (1 + r)
        bh_peak = max(bh_peak, bh_equity)
        bh_mdd = max(bh_mdd, (bh_peak - bh_equity) / bh_peak * 100)
        bh_monthly.append(r)
    bh_sharpe = None
    if len(bh_monthly) >= 4:
        m = statistics.mean(bh_monthly); s = statistics.stdev(bh_monthly)
        if s > 0:
            bh_sharpe = round(m / s * math.sqrt(12), 3)  # annualized monthly

    f3 = {
        "strategy_full": {
            "equity_final":      full["equity_final"],
            "return_pct":        full["equity_return_pct"],
            "mdd_pct":           full["mdd_pct"],
            "sharpe":            full["sharpe_annualized"],
        },
        "buy_hold_proxy": {
            "equity_final":      round(bh_equity, 4),
            "return_pct":        round((bh_equity - 1) * 100, 2),
            "mdd_pct":           round(bh_mdd, 2),
            "sharpe":            bh_sharpe,
            "_method":           "Mean of 대형 20 monthly returns, compounded",
        },
        "mdd_better_than_bh":    full["mdd_pct"] < bh_mdd,
        "sharpe_better_than_bh": (full["sharpe_annualized"] or 0) > (bh_sharpe or 0)
                                  if (full["sharpe_annualized"] and bh_sharpe) else None,
    }
    out["f3_risk_adjusted"] = f3
    print(f"  strategy: equity={f3['strategy_full']['equity_final']} return={f3['strategy_full']['return_pct']}% MDD={f3['strategy_full']['mdd_pct']}% sharpe={f3['strategy_full']['sharpe']}")
    print(f"  buy&hold: equity={f3['buy_hold_proxy']['equity_final']} return={f3['buy_hold_proxy']['return_pct']}% MDD={f3['buy_hold_proxy']['mdd_pct']}% sharpe={f3['buy_hold_proxy']['sharpe']}")

    # ── F4: 18-symbol robust test (exclude 005930, 102110) ──
    print(f"\nF4 — 18-symbol robust (exclude top2: {TOP2_OUTLIER})", flush=True)
    bars18 = _load_bars(BIG_18)
    bars18_by_sym = _index_by_sym(bars18)
    inp18 = BacktestInput(bars=tuple(bars18), horizons=(5, 10, 30, 60))
    collected18 = collect_backtest_trades(inp18)
    council18 = [t for t in collected18.council_trades
                 if t.signal == "BUY" and (t.quality_score or 0) >= MIN_QUALITY]
    stats18 = _strategy_period_stats(council18, bars18_by_sym, HOLD_BARS, COST_FRACTION)
    bh18 = _bh_in_period(bars18_by_sym, BIG_18, ts_min, ts_max)
    e18 = compute_excess(stats18, bh18)
    out["f4_top2_excluded_18"] = {
        "symbols":       BIG_18,
        "n_symbols":     len(BIG_18),
        **(stats18 or {}),
        "excess":        e18,
    }
    print(f"  PF={stats18['pf']} n={stats18['n_trades']} equity={stats18['equity_final']} MDD={stats18['mdd_pct']}% Sharpe={stats18['sharpe_annualized']}")
    print(f"  excess: strat={e18['strat_per_sym_median']}% vs bh={e18['bh_per_sym_median']}% → {e18['excess_median']:+.2f}%pt")

    # ── Verdict ──
    print("\n=== FINAL VERDICT ===")
    f1b_pass = (out["f1b_holdout_last20pct"].get("excess") or {}).get("excess_median", -999) > 0
    f1c_pass = (out["f1c_holdout_last10pct"].get("excess") or {}).get("excess_median", -999) > 0
    f4_pass  = (out["f4_top2_excluded_18"].get("excess") or {}).get("excess_median", -999) > 0
    f3_mdd_pass = f3["mdd_better_than_bh"]
    pass_count = sum([f1b_pass, f1c_pass, f4_pass, f3_mdd_pass])
    out["verdict"] = {
        "f1b_excess_positive_last20":  f1b_pass,
        "f1c_excess_positive_last10":  f1c_pass,
        "f3_mdd_less_than_bh":         f3_mdd_pass,
        "f4_18sym_excess_positive":    f4_pass,
        "pass_count":                  pass_count,
        "decision":                    ("TRUE_ALPHA_CONFIRMED" if pass_count >= 4
                                        else ("PARTIAL_HOLD" if pass_count >= 2
                                              else "MOSTLY_BETA_REJECT")),
    }
    print(f"  F1b last-20% excess > 0?: {f1b_pass}")
    print(f"  F1c last-10% excess > 0?: {f1c_pass}")
    print(f"  F3  MDD < buy&hold MDD?:  {f3_mdd_pass}")
    print(f"  F4  18-sym excess > 0?:   {f4_pass}")
    print(f"  pass_count: {pass_count}/4")
    print(f"  decision: {out['verdict']['decision']}")

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/council_decisive.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nwrote reports/backtest/council_decisive.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
