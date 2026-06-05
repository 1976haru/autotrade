#!/usr/bin/env python3
"""TEMP — Holding × Selectivity matrix scan (2026-05-30 swing study).

Pure forward-return analysis on top of existing infra:
  - signals: collect_backtest_trades (strategy_council_backtest, reused)
  - cost:    cost_model.round_trip_cost_fraction (31bps round-trip)
  - PF:      metrics.summarize (reused)

NO new strategy, NO simulator changes, NO broker / OrderExecutor / route_order.

Knobs:
  - holding horizon (in *bars*, cross-day OK; we override the intraday-only
    forward_returns from collect_backtest_trades by recomputing returns from
    the symbol's *full* bar sequence)
  - selectivity = min quality_score (0=all, 60=mid, 80=high)

Output: JSON {timeframe, group, results: [{holding, selectivity, ...}, ...]}.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.backtest import metrics
from app.backtest.cost_model import (
    DEFAULT_COST,
    UNFILLED_RATE,
    round_trip_cost_fraction,
)
from app.backtest.strategy_council_backtest import (
    AGENT_COUNCIL,
    BacktestInput,
    OHLCVBar,
    collect_backtest_trades,
    load_ohlcv_from_csv,
)


HOLDING_LABELS = {
    # label → forward-return horizon in *bars*
    # On 60m bars: ~8 bars per trading day (KRX 09:00–15:30 + label='right' resample edges).
    "당일":   "close",   # use existing intraday close-of-day return (handled separately)
    "1-2일":  8,         # ~1 trading day
    "3-5일":  32,        # ~4 trading days
    "1-2주":  64,        # ~8 trading days
}

SELECTIVITY = {
    "하(전체)":  0.0,
    "중(60+)":   60.0,
    "상(80+)":   80.0,
}

COST_FRACTION = round_trip_cost_fraction(DEFAULT_COST)   # = 0.0031 = 31bps


def _load_bars(input_dir: Path, symbols: list[str], suffix: str) -> list[OHLCVBar]:
    bars: list[OHLCVBar] = []
    for sym in symbols:
        path = input_dir / f"{sym}{suffix}.csv"
        if not path.exists():
            continue
        bars.extend(load_ohlcv_from_csv(str(path)))
    return bars


def _index_bars_by_symbol(bars):
    by_sym: dict[str, list] = defaultdict(list)
    for b in bars:
        by_sym[b.symbol].append(b)
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x.timestamp)
    return by_sym


def _forward_return_at(
    bars_by_sym, symbol: str, ts_iso: str, hold_bars: int,
) -> float | None:
    """Cross-day forward return = (close_{i+h} / close_i - 1).

    Returns None if not enough bars after signal.
    """
    seq = bars_by_sym.get(symbol)
    if not seq:
        return None
    # locate signal bar by timestamp
    from datetime import datetime
    sig_ts = datetime.fromisoformat(ts_iso)
    for i, b in enumerate(seq):
        if b.timestamp == sig_ts:
            if i + hold_bars >= len(seq):
                return None
            entry_px = b.close
            exit_px = seq[i + hold_bars].close
            if entry_px <= 0:
                return None
            return (exit_px / entry_px) - 1.0
    return None


def _cell_metrics(
    trades_buy, bars_by_sym, hold_horizon, min_quality: float,
    quantity: int = 1,
) -> dict:
    """Filter by quality, compute net returns, summarize."""
    filtered = [t for t in trades_buy if (t.quality_score or 0) >= min_quality]
    pairs = []
    for t in filtered:
        if hold_horizon == "close":
            # use the intraday close-of-day forward return already computed
            gross = float(t.horizon_returns.get("close", 0.0))
        else:
            r = _forward_return_at(bars_by_sym, t.symbol, t.timestamp, hold_horizon)
            if r is None:
                continue
            gross = r
        pairs.append((t, gross))

    # Apply 31bps cost + 5% unfilled (top net-return removal = adverse selection)
    pairs_net = [(t, gross - COST_FRACTION) for t, gross in pairs]
    if UNFILLED_RATE > 0 and pairs_net:
        k = int(len(pairs_net) * UNFILLED_RATE)
        if k > 0:
            top_idx = sorted(range(len(pairs_net)),
                             key=lambda i: pairs_net[i][1], reverse=True)[:k]
            drop = set(top_idx)
            pairs_net = [p for i, p in enumerate(pairs_net) if i not in drop]

    n_total = len(pairs)
    n_after = len(pairs_net)

    # build PF-compatible records
    gross_sum = sum(g for _, g in pairs)
    cost_total = COST_FRACTION * n_total

    recs_net = [
        {"pnl": int(round(net * t.signal_price * quantity)),
         "entry_price": t.signal_price, "quantity": quantity,
         "exit_ts": t.timestamp, "_net": net}
        for t, net in pairs_net
    ]
    recs_gross = [
        {"pnl": int(round(g * t.signal_price * quantity)),
         "entry_price": t.signal_price, "quantity": quantity,
         "exit_ts": t.timestamp, "_net": g}
        for t, g in pairs
    ]

    pf_net = metrics.profit_factor(recs_net) if recs_net else None
    pf_gross = metrics.profit_factor(recs_gross) if recs_gross else None
    wr = metrics.win_rate(recs_net) if recs_net else None
    exp = metrics.expectancy(recs_net) if recs_net else None

    return {
        "trades_signaled":  n_total,
        "trades_executed":  n_after,
        "cost_total_pct":   round(cost_total * 100, 4),   # sum of all cost frags
        "gross_return_pct": round(gross_sum * 100, 4),
        "cost_share_pct":   (round((cost_total / max(abs(gross_sum), 1e-9)) * 100, 2)
                             if gross_sum != 0 else None),
        "pf_gross":         (round(pf_gross, 4) if pf_gross is not None else None),
        "pf_net":           (round(pf_net, 4) if pf_net is not None else None),
        "win_rate_net":     (round(wr, 4) if wr is not None else None),
        "expectancy_net":   (round(exp, 4) if exp is not None else None),
    }


def run_matrix(
    input_dir: Path, symbols: list[str], suffix: str, group_label: str,
) -> dict:
    bars = _load_bars(input_dir, symbols, suffix)
    if not bars:
        return {"group": group_label, "error": "no bars loaded", "cells": []}
    bars_by_sym = _index_bars_by_symbol(bars)

    inp = BacktestInput(bars=tuple(bars), horizons=(5, 10, 30, 60))
    collected = collect_backtest_trades(inp)
    trades_council = list(collected.council_trades)   # ★ council_trades, not strat_trades[AGENT_COUNCIL]
    # restrict to BUY signals only (forward-return is BUY-flavored)
    buy_trades = [t for t in trades_council if t.signal == "BUY"]

    cells = []
    for hold_label, hold_horizon in HOLDING_LABELS.items():
        for sel_label, sel_thr in SELECTIVITY.items():
            m = _cell_metrics(buy_trades, bars_by_sym, hold_horizon, sel_thr)
            cells.append({
                "holding":       hold_label,
                "selectivity":   sel_label,
                "min_quality":   sel_thr,
                **m,
            })
    return {
        "group":             group_label,
        "symbol_count":      len(set(b.symbol for b in bars)),
        "bar_count":         len(bars),
        "council_buy_total": len(buy_trades),
        "cells":             cells,
    }


def walk_forward_retention(
    input_dir: Path, symbols: list[str], suffix: str,
    hold_horizon, min_quality: float, n_splits: int = 3,
) -> dict:
    """Split bars into n_splits chronological segments by date, compute PF per.

    Retention = min(per-segment PF) / overall_PF.   None if data too sparse.
    """
    bars = _load_bars(input_dir, symbols, suffix)
    if not bars:
        return {"retention": None, "reason": "no bars"}
    bars_by_sym = _index_bars_by_symbol(bars)

    from datetime import datetime
    bar_ts = sorted({b.timestamp for b in bars})
    if len(bar_ts) < n_splits * 50:
        return {"retention": None, "reason": "too few bars for split"}
    n_per = len(bar_ts) // n_splits
    seg_edges = [bar_ts[i * n_per] for i in range(n_splits)] + [bar_ts[-1]]

    inp = BacktestInput(bars=tuple(bars), horizons=(5, 10, 30, 60))
    collected = collect_backtest_trades(inp)
    council_buy = [t for t in collected.council_trades if t.signal == "BUY"]
    council_buy = [t for t in council_buy if (t.quality_score or 0) >= min_quality]

    def _pf(trades_seg):
        if not trades_seg:
            return None
        pairs = []
        for t in trades_seg:
            if hold_horizon == "close":
                gross = float(t.horizon_returns.get("close", 0.0))
            else:
                r = _forward_return_at(bars_by_sym, t.symbol, t.timestamp, hold_horizon)
                if r is None: continue
                gross = r
            pairs.append((t, gross - COST_FRACTION))
        if not pairs: return None
        recs = [{"pnl": int(round(n * t.signal_price)), "entry_price": t.signal_price,
                 "quantity": 1, "exit_ts": t.timestamp, "_net": n}
                for t, n in pairs]
        return metrics.profit_factor(recs)

    # overall (with selectivity)
    overall_pf = _pf(council_buy)

    # per-segment
    seg_pfs = []
    for i in range(n_splits):
        lo, hi = seg_edges[i], seg_edges[i + 1]
        seg_trades = [t for t in council_buy
                      if lo <= datetime.fromisoformat(t.timestamp) <= hi]
        seg_pfs.append(_pf(seg_trades))

    valid_pfs = [p for p in seg_pfs if p is not None]
    if overall_pf is None or not valid_pfs:
        return {"retention": None, "segments": seg_pfs, "overall_pf": overall_pf}
    min_seg = min(valid_pfs)
    retention = round(min_seg / overall_pf, 4) if overall_pf > 0 else None
    return {
        "retention":  retention,
        "overall_pf": round(overall_pf, 4) if overall_pf is not None else None,
        "segments":   [round(p, 4) if p is not None else None for p in seg_pfs],
        "min_seg_pf": round(min_seg, 4),
    }


# 3 disjoint groups from yesterday's 35-symbol study
SYMBOLS_ALL = ["000270","000660","005380","005930","006400","009540","010130","011200","012330","012450","015760","028300","032830","034730","035420","035720","042660","042700","051910","055550","066570","068270","069500","086520","086790","102110","105560","114800","122630","196170","229200","247540","259960","293490","352820"]
G1 = SYMBOLS_ALL[:12]
G2 = SYMBOLS_ALL[12:24]
G3 = SYMBOLS_ALL[24:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="60m")
    ap.add_argument("--input-dir", default="data/market/intraday_60m_resampled")
    ap.add_argument("--suffix", default="_60m")
    ap.add_argument("--output", required=True)
    ap.add_argument("--with-wf", action="store_true",
                    help="run walk-forward retention on cells with pf_net>=1.2")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"missing: {input_dir}"); return 2

    groups = [("G1", G1), ("G2", G2), ("G3", G3)]
    results = {
        "timeframe": args.timeframe,
        "input_dir": str(input_dir),
        "cost_fraction_bps": round(COST_FRACTION * 1e4, 2),
        "unfilled_rate": UNFILLED_RATE,
        "groups": [],
    }

    for glabel, syms in groups:
        print(f"  → group {glabel} ({len(syms)} syms) ...", flush=True)
        g = run_matrix(input_dir, syms, args.suffix, glabel)
        # mark candidates for WF
        if args.with_wf:
            for cell in g.get("cells", []):
                if cell.get("pf_net") and cell["pf_net"] >= 1.2:
                    wf = walk_forward_retention(
                        input_dir, syms, args.suffix,
                        HOLDING_LABELS[cell["holding"]], cell["min_quality"],
                    )
                    cell["walk_forward"] = wf
        results["groups"].append(g)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    print(f"  wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
