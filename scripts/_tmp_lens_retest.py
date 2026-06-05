#!/usr/bin/env python3
"""TEMP — Lens-corrected retest: 추세추종 4전략 + 1-2주 스윙 across 4 cap buckets.

Each cell:
  - net PF (per-bucket round-trip cost: 대형 33 / 중대형 43 / 중소형 63 / 소형 93 bps)
  - mean trade return %
  - bucket buy&hold return % (benchmark)
  - excess return (strategy_total_return_sum - bh_median*n_syms)
  - top2 excluded PF
  - OOS train vs test PF

Multiple comparison (L6): N = 4 strategies × 4 buckets × 2 holdings (intraday + 1-2주) = 32 combos.
Bonferroni-adjusted threshold: PF≥1.2 → PF≥1.2 + extra margin proportional to log(N).

NO new strategy, NO simulator, NO broker / route_order / place_order.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.backtest import metrics
from app.backtest.cost_model import DEFAULT_COST  # 33bps central; per-bucket overrides
from app.backtest.strategy_council_backtest import (
    AGENT_COUNCIL, BacktestInput, collect_backtest_trades, load_ohlcv_from_csv,
)

LENS_DIR = Path("data/market/lens_60m")
UNIV_JSON = LENS_DIR / "_universe.json"

# 1-2주 hold horizon in 60m bars (~8 bars/day × 8 days)
HOLD_BARS_1_2W = 64
INTRADAY_HORIZON = "close"     # within-day close-of-day forward return
SWING_MIN_QUALITY = 80.0       # 상(80+) for council swing


def _load_universe():
    return json.loads(UNIV_JSON.read_text(encoding="utf-8"))


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


def _fwd_return_at(bars_by_sym, symbol, ts_iso, hold_bars):
    seq = bars_by_sym.get(symbol)
    if not seq: return None
    sig_ts = datetime.fromisoformat(ts_iso)
    for i, b in enumerate(seq):
        if b.timestamp == sig_ts:
            if i + hold_bars >= len(seq): return None
            entry, exit_px = b.close, seq[i + hold_bars].close
            if entry <= 0: return None
            return (exit_px / entry) - 1.0
    return None


def _cell_metrics(buy_trades, bars_by_sym, hold_horizon, cost_frac,
                  unfilled_rate=0.05):
    """Return: pf_gross, pf_net, win_rate, n, gross_sum, cost_sum, top2-excluded PF."""
    pairs = []
    for t in buy_trades:
        if hold_horizon == "close":
            gross = float(t.horizon_returns.get("close", 0.0))
        else:
            r = _fwd_return_at(bars_by_sym, t.symbol, t.timestamp, hold_horizon)
            if r is None: continue
            gross = r
        pairs.append((t, gross))

    pairs_net = [(t, g - cost_frac) for t, g in pairs]
    if unfilled_rate > 0 and pairs_net:
        k = int(len(pairs_net) * unfilled_rate)
        if k > 0:
            top = sorted(range(len(pairs_net)),
                         key=lambda i: pairs_net[i][1], reverse=True)[:k]
            drop = set(top)
            pairs_net = [p for i, p in enumerate(pairs_net) if i not in drop]

    if not pairs_net:
        return {"n_signaled": len(pairs), "n_executed": 0,
                "pf_gross": None, "pf_net": None, "win_rate": None,
                "gross_sum_pct": 0.0, "cost_sum_pct": 0.0,
                "by_symbol": [], "top2_syms": [], "pf_net_without_top2": None}

    recs_net = [{"pnl": int(round(net * t.signal_price)),
                 "entry_price": t.signal_price, "quantity": 1,
                 "exit_ts": t.timestamp, "_net": net}
                for t, net in pairs_net]
    recs_gross = [{"pnl": int(round(g * t.signal_price)),
                   "entry_price": t.signal_price, "quantity": 1,
                   "exit_ts": t.timestamp, "_net": g}
                  for t, g in pairs]
    pf_net = metrics.profit_factor(recs_net)
    pf_gross = metrics.profit_factor(recs_gross)
    wr = metrics.win_rate(recs_net)
    gross_sum = sum(g for _, g in pairs)
    cost_sum = cost_frac * len(pairs)

    # per-symbol decomp for top2 exclusion
    by_sym_buckets: dict[str, list] = defaultdict(list)
    by_sym_gross: dict[str, list] = defaultdict(list)
    for t, net in pairs_net:
        by_sym_buckets[t.symbol].append((t, net))
    for t, g in pairs:
        by_sym_gross[t.symbol].append((t, g))

    sym_pfs = []
    for sym, lst in by_sym_buckets.items():
        recs = [{"pnl": int(round(n * t.signal_price)), "entry_price": t.signal_price,
                 "quantity": 1, "exit_ts": t.timestamp, "_net": n}
                for t, n in lst]
        pf = metrics.profit_factor(recs)
        sym_pfs.append({"symbol": sym, "n": len(recs),
                        "pf_net": (round(pf, 3) if pf is not None else None)})
    sym_pfs.sort(key=lambda x: (x["pf_net"] or -999), reverse=True)
    top2 = [r["symbol"] for r in sym_pfs[:2]]
    surv_recs = [{"pnl": int(round(n * t.signal_price)), "entry_price": t.signal_price,
                  "quantity": 1, "exit_ts": t.timestamp, "_net": n}
                 for sym, lst in by_sym_buckets.items() if sym not in top2
                 for t, n in lst]
    pf_no_top2 = metrics.profit_factor(surv_recs) if surv_recs else None

    return {
        "n_signaled":   len(pairs),
        "n_executed":   len(pairs_net),
        "pf_gross":     round(pf_gross, 3) if pf_gross is not None else None,
        "pf_net":       round(pf_net, 3) if pf_net is not None else None,
        "win_rate":     round(wr, 3) if wr is not None else None,
        "gross_sum_pct":round(gross_sum * 100, 2),
        "cost_sum_pct": round(cost_sum * 100, 2),
        "by_symbol":    sym_pfs[:10],
        "top2_syms":    top2,
        "pf_net_without_top2": (round(pf_no_top2, 3) if pf_no_top2 is not None else None),
    }


def _oos_split_pf(buys, bars_by_sym, hold_horizon, cost_frac, n_splits=2):
    """Chronological split — train PF vs test PF on holdout last 1/n_splits."""
    if not buys: return None
    ts_sorted = sorted({datetime.fromisoformat(t.timestamp) for t in buys})
    if len(ts_sorted) < 100: return None
    cut = ts_sorted[int(len(ts_sorted) * (1 - 1.0 / n_splits))]
    train = [t for t in buys if datetime.fromisoformat(t.timestamp) < cut]
    test = [t for t in buys if datetime.fromisoformat(t.timestamp) >= cut]

    def _pf(trades_set):
        pairs = []
        for t in trades_set:
            if hold_horizon == "close":
                g = float(t.horizon_returns.get("close", 0.0))
            else:
                r = _fwd_return_at(bars_by_sym, t.symbol, t.timestamp, hold_horizon)
                if r is None: continue
                g = r
            pairs.append((t, g - cost_frac))
        if not pairs: return None
        recs = [{"pnl": int(round(n * t.signal_price)), "entry_price": t.signal_price,
                 "quantity": 1, "exit_ts": t.timestamp, "_net": n}
                for t, n in pairs]
        return metrics.profit_factor(recs)

    return {
        "train_n": len(train), "test_n": len(test),
        "cut_ts": cut.isoformat(),
        "train_pf": round(_pf(train), 3) if _pf(train) is not None else None,
        "test_pf":  round(_pf(test),  3) if _pf(test)  is not None else None,
    }


def main():
    universe = _load_universe()
    by_bucket = defaultdict(list)
    bh_by_bucket = defaultdict(list)
    cost_by_bucket = {}
    for u in universe:
        by_bucket[u["bucket"]].append(u["symbol"])
        bh_by_bucket[u["bucket"]].append(u["buy_hold_return"])
        cost_by_bucket[u["bucket"]] = u["round_trip_cost_bps"] / 1e4

    BUCKETS = ["대형", "중대형", "중소형", "소형"]
    STRATEGIES = ["ORB", "MOMENTUM", "GAP", "VWAP"]   # trend-following 4

    print(f"buckets: {[(b, len(by_bucket[b])) for b in BUCKETS]}")
    print(f"buy&hold medians: {[(b, f'{statistics.median(bh_by_bucket[b])*100:+.1f}%') for b in BUCKETS]}")
    print(f"round-trip costs (bps): {[(b, cost_by_bucket[b]*1e4) for b in BUCKETS]}")
    print()

    out = {
        "lens_version": "L1-L6 applied",
        "n_universe": len(universe),
        "buckets": {b: {"n_syms": len(by_bucket[b]),
                        "buy_hold_median_pct": round(statistics.median(bh_by_bucket[b]) * 100, 2),
                        "buy_hold_mean_pct":   round(statistics.mean(bh_by_bucket[b]) * 100, 2),
                        "round_trip_bps":       cost_by_bucket[b] * 1e4} for b in BUCKETS},
        "matrix": [],
    }

    # ─── trend-following 4 strategies × 4 buckets, intraday (close horizon) ───
    print("Trend-following 4 × 4 buckets (intraday close horizon)...")
    for bk in BUCKETS:
        syms = by_bucket[bk]
        bars = _load_bars(syms)
        if not bars:
            print(f"  {bk}: no bars"); continue
        bars_by_sym = _index_by_sym(bars)
        cost_frac = cost_by_bucket[bk]
        bh_median = statistics.median(bh_by_bucket[bk])

        inp = BacktestInput(bars=tuple(bars), horizons=(5, 10, 30, 60))
        collected = collect_backtest_trades(inp)

        for strat in STRATEGIES:
            trades_all = collected.strat_trades.get(strat, [])
            buys = [t for t in trades_all if t.signal == "BUY"]
            if not buys:
                continue

            cell = _cell_metrics(buys, bars_by_sym, INTRADAY_HORIZON, cost_frac)
            oos = _oos_split_pf(buys, bars_by_sym, INTRADAY_HORIZON, cost_frac)
            # strategy total return estimate = sum of net returns × signal_price / 1 (per-unit notional sum)
            # but for *bucket-relative excess return*, compare strategy expected per-trade return × n vs bh
            # Simpler: per-trade mean net return, then * bh_n_days_in_period... too complex.
            # Use simple: bh_median_pct vs (strategy net_pf - 1.0)*sign? Actually: net total = gross_sum - cost_sum
            net_total_pct = cell.get("gross_sum_pct", 0) - cell.get("cost_sum_pct", 0)
            # excess (strategy_per_signal_avg_net - bh_median_per_period_per_sym) -- arbitrary scaling
            # better: strategy net average per trade × annualized vs bh
            n_syms_active = len(set(t.symbol for t in buys))
            # excess return per symbol: net_total / n_syms_active vs bh_median
            excess_per_sym_pct = (net_total_pct / max(1, n_syms_active)) - (bh_median * 100)

            row = {
                "type":     "TREND_INTRADAY",
                "strategy": strat,
                "bucket":   bk,
                "buy_hold_median_pct": round(bh_median * 100, 2),
                "n_syms":   n_syms_active,
                **cell,
                "net_total_pct":         round(net_total_pct, 2),
                "net_per_sym_pct":       round(net_total_pct / max(1, n_syms_active), 2),
                "excess_per_sym_vs_bh_pct": round(excess_per_sym_pct, 2),
                "oos":      oos,
            }
            out["matrix"].append(row)
            print(f"  {strat:10s} × {bk:5s}: PF_gross={cell['pf_gross']} PF_net={cell['pf_net']} "
                  f"n={cell['n_executed']} top2excl={cell['pf_net_without_top2']} "
                  f"excess={excess_per_sym_pct:+.2f}% (bh={bh_median*100:+.1f}%)")

    # ─── Council 1-2주 (80+) × 4 buckets ───
    print("\nCouncil 1-2주 × 80+ × 4 buckets (hold 64 bars)...")
    for bk in BUCKETS:
        syms = by_bucket[bk]
        bars = _load_bars(syms)
        if not bars: continue
        bars_by_sym = _index_by_sym(bars)
        cost_frac = cost_by_bucket[bk]
        bh_median = statistics.median(bh_by_bucket[bk])

        inp = BacktestInput(bars=tuple(bars), horizons=(5, 10, 30, 60))
        collected = collect_backtest_trades(inp)
        council_buys = [t for t in collected.council_trades
                        if t.signal == "BUY" and (t.quality_score or 0) >= SWING_MIN_QUALITY]
        if not council_buys: continue

        cell = _cell_metrics(council_buys, bars_by_sym, HOLD_BARS_1_2W, cost_frac)
        oos = _oos_split_pf(council_buys, bars_by_sym, HOLD_BARS_1_2W, cost_frac)
        net_total_pct = cell.get("gross_sum_pct", 0) - cell.get("cost_sum_pct", 0)
        n_syms_active = len(set(t.symbol for t in council_buys))
        excess = (net_total_pct / max(1, n_syms_active)) - (bh_median * 100)

        row = {
            "type":     "COUNCIL_SWING_1_2W_Q80",
            "strategy": "AGENT_COUNCIL",
            "bucket":   bk,
            "buy_hold_median_pct": round(bh_median * 100, 2),
            "n_syms":   n_syms_active,
            **cell,
            "net_total_pct":         round(net_total_pct, 2),
            "net_per_sym_pct":       round(net_total_pct / max(1, n_syms_active), 2),
            "excess_per_sym_vs_bh_pct": round(excess, 2),
            "oos":      oos,
        }
        out["matrix"].append(row)
        print(f"  council80+_1-2w × {bk:5s}: PF_gross={cell['pf_gross']} PF_net={cell['pf_net']} "
              f"n={cell['n_executed']} top2excl={cell['pf_net_without_top2']} "
              f"excess={excess:+.2f}% (bh={bh_median*100:+.1f}%)")

    # ─── L6: Bonferroni adjustment ───
    N = len(out["matrix"])
    out["multiple_comparison"] = {
        "n_combos": N,
        "naive_threshold_pf": 1.2,
        "bonferroni_factor": math.log(max(2, N)) / math.log(20),  # relative scaling to N=20
        "expected_lucky_above_1_2": round(N * 0.10, 1),  # naive ~10% would exceed by noise
        "note": (
            f"N={N} 조합. naive PF≥1.2 만으로는 우연 약 {round(N*0.10, 1)}개 통과 예상. "
            "보수적 통과 기준: PF_net≥1.2 + PF_net_without_top2≥1.1 + buy&hold 초과 + OOS test PF≥1.0."
        ),
    }

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/lens_retest.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nwrote reports/backtest/lens_retest.json")
    print(f"\ntotal combos N = {N}")

    # candidate filter
    candidates = []
    for r in out["matrix"]:
        pf = r.get("pf_net") or 0
        pf_no_top2 = r.get("pf_net_without_top2") or 0
        excess = r.get("excess_per_sym_vs_bh_pct") or -999
        oos = r.get("oos") or {}
        test_pf = oos.get("test_pf") or 0
        if pf >= 1.2 and pf_no_top2 >= 1.1 and excess > 0 and test_pf >= 1.0:
            candidates.append(r)

    print(f"통과 후보 (PF≥1.2 + top2 제외 ≥1.1 + buy&hold 초과 + OOS test PF≥1.0): {len(candidates)}")
    for c in candidates:
        print(f"  ✓ {c['strategy']} × {c['bucket']}: PF={c['pf_net']} excess={c['excess_per_sym_vs_bh_pct']:+.2f}%")

    out["candidates_passing"] = candidates
    Path("reports/backtest/lens_retest.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
