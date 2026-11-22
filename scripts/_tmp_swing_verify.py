#!/usr/bin/env python3
"""TEMP — 1-2주 × 상(80+) 조합 정밀 검증 (V1 per-symbol / V2 우량주 / V3 forward holdout).

- 33bps round-trip (V0 — cost_model.py 갱신 후)
- in-sample (~12개월 robust_intraday_5m → 60m resample)
- forward holdout: intraday_5m_forward_extra (별도 디렉토리, in-sample 미사용)
  또는 intraday_5m_1y_old (대안)

NO new strategy, NO simulator, NO broker / OrderExecutor / route_order.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.backtest import metrics
from app.backtest.cost_model import (
    DEFAULT_COST, UNFILLED_RATE, round_trip_cost_fraction,
)
from app.backtest.strategy_council_backtest import (
    BacktestInput, collect_backtest_trades, load_ohlcv_from_csv,
)

COST_FRACTION = round_trip_cost_fraction(DEFAULT_COST)   # 33bps → 0.0033

# Same 35 symbols as prior swing matrix
SYMBOLS_ALL = ["000270","000660","005380","005930","006400","009540","010130","011200","012330","012450","015760","028300","032830","034730","035420","035720","042660","042700","051910","055550","066570","068270","069500","086520","086790","102110","105560","114800","122630","196170","229200","247540","259960","293490","352820"]

# "우량주" = 시총 대형 + 고유동성 (한국 KOSPI 대형주 카탈로그 — 일반 기준)
# G1/G2 의 대형주 11개 (반도체 / 차 / 화학 / 금융 / 통신 등 메이저 sector). G3 제외 + theme/소형 제외.
BLUE_CHIPS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "000270",  # 기아
    "035420",  # NAVER
    "035720",  # 카카오
    "051910",  # LG화학
    "055550",  # 신한지주
    "068270",  # 셀트리온
    "006400",  # 삼성SDI
    "012330",  # 현대모비스
]

HOLD_HORIZON_BARS = 64    # ~8 trading days = 1-2주 (60m bars, ~8 bars/day)
MIN_QUALITY = 80.0        # 상(80+) 선별


def _load_bars(input_dir: Path, symbols: list[str], suffix: str):
    bars = []
    for sym in symbols:
        path = input_dir / f"{sym}{suffix}.csv"
        if path.exists():
            bars.extend(load_ohlcv_from_csv(str(path)))
    return bars


def _index_by_sym(bars):
    by_sym = defaultdict(list)
    for b in bars:
        by_sym[b.symbol].append(b)
    for s in by_sym:
        by_sym[s].sort(key=lambda x: x.timestamp)
    return by_sym


def _forward_return_at(bars_by_sym, symbol: str, ts_iso: str, hold_bars: int):
    seq = bars_by_sym.get(symbol)
    if not seq:
        return None
    sig_ts = datetime.fromisoformat(ts_iso)
    for i, b in enumerate(seq):
        if b.timestamp == sig_ts:
            if i + hold_bars >= len(seq):
                return None
            entry = b.close
            exit_px = seq[i + hold_bars].close
            if entry <= 0:
                return None
            return (exit_px / entry) - 1.0
    return None


def _pf_from_trades(trades, bars_by_sym, hold_bars: int):
    """Apply 33bps cost + 5% adverse selection, return PF + win_rate + n + cost_total_frac."""
    pairs = []
    for t in trades:
        r = _forward_return_at(bars_by_sym, t.symbol, t.timestamp, hold_bars)
        if r is None:
            continue
        pairs.append((t, r))

    pairs_net = [(t, g - COST_FRACTION) for t, g in pairs]
    if UNFILLED_RATE > 0 and pairs_net:
        k = int(len(pairs_net) * UNFILLED_RATE)
        if k > 0:
            top = sorted(range(len(pairs_net)),
                         key=lambda i: pairs_net[i][1], reverse=True)[:k]
            drop = set(top)
            pairs_net = [p for i, p in enumerate(pairs_net) if i not in drop]

    if not pairs_net:
        return {"n_signaled": len(pairs), "n_executed": 0,
                "pf_net": None, "win_rate": None, "expectancy": None,
                "gross_sum_pct": 0.0, "cost_total_pct": 0.0}

    recs = [{"pnl": int(round(net * t.signal_price)),
             "entry_price": t.signal_price, "quantity": 1,
             "exit_ts": t.timestamp, "_net": net}
            for t, net in pairs_net]
    pf = metrics.profit_factor(recs)
    wr = metrics.win_rate(recs)
    exp = metrics.expectancy(recs)
    gross_sum = sum(g for _, g in pairs)
    cost_total = COST_FRACTION * len(pairs)
    return {
        "n_signaled":   len(pairs),
        "n_executed":   len(pairs_net),
        "pf_net":       round(pf, 4) if pf is not None else None,
        "win_rate":     round(wr, 4) if wr is not None else None,
        "expectancy":   round(exp, 2) if exp is not None else None,
        "gross_sum_pct":round(gross_sum * 100, 2),
        "cost_total_pct": round(cost_total * 100, 2),
    }


def _collect_council_buy(bars, min_quality: float):
    inp = BacktestInput(bars=tuple(bars), horizons=(5, 10, 30, 60))
    collected = collect_backtest_trades(inp)
    council = list(collected.council_trades)
    return [t for t in council
            if t.signal == "BUY" and (t.quality_score or 0) >= min_quality]


# ────────────────────────────────────────────────────────────────────
# V1 — per-symbol decomposition (1-2주 × 80+)
# ────────────────────────────────────────────────────────────────────


def run_v1_per_symbol(input_dir: Path, symbols: list[str], suffix: str,
                     group_label: str):
    bars = _load_bars(input_dir, symbols, suffix)
    bars_by_sym = _index_by_sym(bars)
    buys = _collect_council_buy(bars, MIN_QUALITY)

    # PF per symbol (using global cost overlay)
    by_sym_pf = []
    for sym in symbols:
        sym_trades = [t for t in buys if t.symbol == sym]
        if not sym_trades:
            by_sym_pf.append({"symbol": sym, "n": 0, "pf_net": None,
                              "win_rate": None, "gross_sum_pct": 0.0})
            continue
        m = _pf_from_trades(sym_trades, bars_by_sym, HOLD_HORIZON_BARS)
        by_sym_pf.append({"symbol": sym, **m})

    # overall PF (all symbols in group)
    overall = _pf_from_trades(buys, bars_by_sym, HOLD_HORIZON_BARS)

    # rank by PF (drop None); top-2 exclusion
    ranked = sorted([p for p in by_sym_pf if p.get("pf_net") is not None],
                    key=lambda p: p["pf_net"], reverse=True)
    top2 = [p["symbol"] for p in ranked[:2]]
    survivors_trades = [t for t in buys if t.symbol not in set(top2)]
    pf_without_top2 = (_pf_from_trades(survivors_trades, bars_by_sym, HOLD_HORIZON_BARS)
                       if survivors_trades else None)

    return {
        "group":            group_label,
        "n_symbols":        len(symbols),
        "n_council_buys":   len(buys),
        "overall":          overall,
        "by_symbol":        by_sym_pf,
        "ranked_top5":      [{"symbol": r["symbol"],
                               "pf_net": r["pf_net"],
                               "n": r.get("n_executed", r.get("n", 0))}
                              for r in ranked[:5]],
        "top2_excluded":    top2,
        "pf_without_top2":  pf_without_top2,
    }


# ────────────────────────────────────────────────────────────────────
# V2 — blue-chip only (G3 류 제외)
# ────────────────────────────────────────────────────────────────────


def run_v2_blue_chip(input_dir: Path, suffix: str):
    """우량주 11개만으로 1-2주 × 80+ 재측정."""
    bars = _load_bars(input_dir, BLUE_CHIPS, suffix)
    bars_by_sym = _index_by_sym(bars)
    buys = _collect_council_buy(bars, MIN_QUALITY)
    overall = _pf_from_trades(buys, bars_by_sym, HOLD_HORIZON_BARS)
    # by symbol
    per = []
    for sym in BLUE_CHIPS:
        st = [t for t in buys if t.symbol == sym]
        if not st:
            per.append({"symbol": sym, "n": 0, "pf_net": None}); continue
        m = _pf_from_trades(st, bars_by_sym, HOLD_HORIZON_BARS)
        per.append({"symbol": sym, **m})
    return {
        "universe":       "BLUE_CHIPS",
        "symbols":        BLUE_CHIPS,
        "n_symbols":      len(BLUE_CHIPS),
        "n_council_buys": len(buys),
        "overall":        overall,
        "by_symbol":      per,
    }


# ────────────────────────────────────────────────────────────────────
# V3 — forward holdout (separate time window, locked rule)
# ────────────────────────────────────────────────────────────────────


def run_v3_chronological_holdout(in_sample_dir: Path, in_sample_suffix: str,
                                 symbols: list[str], holdout_pct: float = 0.20):
    """V3 진짜 OOS — 동일 디렉토리에서 시간 분할.

    데이터: robust_intraday_5m → 60m (2025-05 ~ 2026-05 약 1년)
    Split: 시간 정렬 후 처음 (1-holdout_pct) = in-sample, 마지막 holdout_pct = holdout.
    Holdout 시작점 = 룰 발견 시점과 무관한 *새 기간*.
    """
    bars = _load_bars(in_sample_dir, symbols, in_sample_suffix)
    if not bars:
        return {"error": "no bars"}
    all_ts = sorted({b.timestamp for b in bars})
    cut_idx = int(len(all_ts) * (1.0 - holdout_pct))
    cut_ts = all_ts[cut_idx]

    in_bars = [b for b in bars if b.timestamp < cut_ts]
    h_bars = [b for b in bars if b.timestamp >= cut_ts]

    # in-sample
    by_sym_in = _index_by_sym(in_bars)
    buys_in = _collect_council_buy(in_bars, MIN_QUALITY)
    in_res = _pf_from_trades(buys_in, by_sym_in, HOLD_HORIZON_BARS)

    # holdout
    by_sym_h = _index_by_sym(h_bars)
    buys_h = _collect_council_buy(h_bars, MIN_QUALITY)
    h_res = _pf_from_trades(buys_h, by_sym_h, HOLD_HORIZON_BARS)

    # decay within holdout (3-segment)
    h_ts_sorted = sorted({b.timestamp for b in h_bars})
    decay = []
    if len(h_ts_sorted) >= 90:
        per_n = len(h_ts_sorted) // 3
        edges = [h_ts_sorted[i * per_n] for i in range(3)] + [h_ts_sorted[-1]]
        for i in range(3):
            lo, hi = edges[i], edges[i + 1]
            seg_trades = [t for t in buys_h
                          if lo <= datetime.fromisoformat(t.timestamp) <= hi]
            if not seg_trades:
                decay.append({"segment": i, "pf_net": None, "n": 0})
                continue
            sm = _pf_from_trades(seg_trades, by_sym_h, HOLD_HORIZON_BARS)
            decay.append({"segment": i, "pf_net": sm["pf_net"],
                          "n": sm["n_executed"]})

    return {
        "split_cut_ts":    cut_ts.isoformat(),
        "holdout_pct":     holdout_pct,
        "in_sample_period":  f"{in_bars[0].timestamp.date()} → {in_bars[-1].timestamp.date()}",
        "holdout_period":    f"{h_bars[0].timestamp.date()} → {h_bars[-1].timestamp.date()}",
        "in_sample_bars":  len(in_bars),
        "holdout_bars":    len(h_bars),
        "in_sample":       {"n_council_buys": len(buys_in), **in_res},
        "holdout":         {"n_council_buys": len(buys_h), **h_res},
        "decay_segments_holdout": decay,
        "decay_verdict":   _decay_verdict(decay, in_res.get("pf_net")),
    }


def _decay_verdict(decay, in_sample_pf):
    """Detect alpha decay: PF descending across segments + ends below in-sample."""
    pfs = [d["pf_net"] for d in decay if d.get("pf_net") is not None]
    if len(pfs) < 2 or in_sample_pf is None:
        return "INSUFFICIENT"
    descending = all(pfs[i] >= pfs[i + 1] for i in range(len(pfs) - 1))
    last_below_in_sample = pfs[-1] < in_sample_pf * 0.7
    if descending and last_below_in_sample:
        return "ALPHA_DECAY_LIKELY"
    if pfs[-1] < 1.0:
        return "LAST_SEGMENT_UNPROFITABLE"
    return "STABLE_OR_NOISY"


def run_v3_external_holdout(in_sample_dir: Path, in_sample_suffix: str,
                            holdout_dir: Path, holdout_suffix: str,
                            symbols: list[str], decay_segments: int = 3):
    """
    in-sample = main robust dataset (2025-05~2026-05)
    holdout   = separate dataset (intraday_5m_forward_extra / 1y_old)
    Note: 둘 다 robust 와 시간 *겹침* (정직히 보고).
    """
    out = {"holdout_dir": str(holdout_dir),
           "holdout_suffix": holdout_suffix,
           "n_symbols_universe": len(symbols),
           "symbols_loaded": [], "result": None,
           "in_sample_compare": None,
           "decay_segments": None,
           "_caveat": "external dataset 은 in-sample 과 시간 *겹침* 가능. V3a chronological split 이 더 신뢰."}

    # holdout
    bars_h = _load_bars(holdout_dir, symbols, holdout_suffix)
    loaded_syms = sorted(set(b.symbol for b in bars_h))
    out["symbols_loaded"] = loaded_syms
    out["n_symbols_loaded"] = len(loaded_syms)
    out["n_bars_holdout"] = len(bars_h)
    if loaded_syms:
        bars_by_sym = _index_by_sym(bars_h)
        buys = _collect_council_buy(bars_h, MIN_QUALITY)
        out["n_council_buys_holdout"] = len(buys)
        if buys:
            res = _pf_from_trades(buys, bars_by_sym, HOLD_HORIZON_BARS)
            out["result"] = res
            # decay segments — divide holdout bar timestamps into N parts
            ts_sorted = sorted({b.timestamp for b in bars_h})
            if len(ts_sorted) >= decay_segments * 30:
                per_n = len(ts_sorted) // decay_segments
                edges = [ts_sorted[i * per_n] for i in range(decay_segments)] + [ts_sorted[-1]]
                seg_pfs = []
                for i in range(decay_segments):
                    lo, hi = edges[i], edges[i + 1]
                    seg_trades = [t for t in buys
                                  if lo <= datetime.fromisoformat(t.timestamp) <= hi]
                    if not seg_trades:
                        seg_pfs.append({"segment": i, "pf_net": None, "n": 0})
                        continue
                    sm = _pf_from_trades(seg_trades, bars_by_sym, HOLD_HORIZON_BARS)
                    seg_pfs.append({"segment": i, "pf_net": sm["pf_net"],
                                    "n": sm["n_executed"]})
                out["decay_segments"] = seg_pfs

    # in-sample comparison (same locked rule) on same loaded symbols
    bars_i = _load_bars(in_sample_dir, loaded_syms or symbols, in_sample_suffix)
    if bars_i:
        bars_by_sym = _index_by_sym(bars_i)
        buys = _collect_council_buy(bars_i, MIN_QUALITY)
        out["in_sample_compare"] = {
            "n_symbols": len(set(b.symbol for b in bars_i)),
            "n_council_buys": len(buys),
            **(_pf_from_trades(buys, bars_by_sym, HOLD_HORIZON_BARS) if buys else {}),
        }

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-sample-dir", default="data/market/intraday_60m_resampled")
    ap.add_argument("--in-sample-suffix", default="_60m")
    ap.add_argument("--holdout-dir", default="data/market/intraday_5m_forward_extra",
                    help="forward holdout source (DEFAULT: 5m forward_extra)")
    ap.add_argument("--holdout-suffix", default="_5m")
    ap.add_argument("--holdout-hold-bars", type=int, default=None,
                    help="override HOLD_HORIZON_BARS for holdout (5m bars × 8 trading days = 624)")
    ap.add_argument("--output", required=True)
    global HOLD_HORIZON_BARS   # mutate at function scope for V3b only
    args = ap.parse_args()

    in_dir = Path(args.in_sample_dir)
    h_dir = Path(args.holdout_dir)
    if not in_dir.exists():
        print(f"missing: {in_dir}"); return 2

    # 3 groups (same as prior swing matrix)
    G1 = SYMBOLS_ALL[:12]; G2 = SYMBOLS_ALL[12:24]; G3 = SYMBOLS_ALL[24:]

    out = {
        "cost_bps":      DEFAULT_COST.round_trip_cost_bps(),
        "cost_fraction": COST_FRACTION,
        "min_quality":   MIN_QUALITY,
        "hold_horizon_bars_in_sample":  HOLD_HORIZON_BARS,
        "in_sample_dir": str(in_dir),
        "holdout_dir":   str(h_dir),
    }

    # V1 — per-symbol per group
    print("  → V1 per-symbol G1/G2/G3 (in-sample 60m)...", flush=True)
    out["v1_per_symbol"] = {
        "G1": run_v1_per_symbol(in_dir, G1, args.in_sample_suffix, "G1"),
        "G2": run_v1_per_symbol(in_dir, G2, args.in_sample_suffix, "G2"),
        "G3": run_v1_per_symbol(in_dir, G3, args.in_sample_suffix, "G3"),
    }

    # V2 — blue chips only
    print("  → V2 blue-chip (11 종목, in-sample 60m)...", flush=True)
    out["v2_blue_chip"] = run_v2_blue_chip(in_dir, args.in_sample_suffix)

    # V3a — chronological split on the main 60m dataset (TRUE OOS in time)
    print("  → V3a chronological split (last 20% = holdout)...", flush=True)
    out["v3a_chrono_split"] = run_v3_chronological_holdout(
        in_sample_dir=in_dir, in_sample_suffix=args.in_sample_suffix,
        symbols=SYMBOLS_ALL, holdout_pct=0.20,
    )

    # V3b — external dataset (forward_extra / 1y_old) for cross-check
    # 5m bars × 8 trading days (KRX = ~78 bars/day) = 624 bars
    holdout_hold = args.holdout_hold_bars
    if holdout_hold is None:
        holdout_hold = 624 if "5m" in args.holdout_suffix else 64
    print(f"  → V3b external holdout (hold_bars={holdout_hold} on {args.holdout_suffix})...",
          flush=True)
    in_sample_hold_orig = HOLD_HORIZON_BARS
    HOLD_HORIZON_BARS = holdout_hold
    try:
        out["v3b_external_holdout"] = run_v3_external_holdout(
            in_sample_dir=in_dir, in_sample_suffix=args.in_sample_suffix,
            holdout_dir=h_dir, holdout_suffix=args.holdout_suffix,
            symbols=SYMBOLS_ALL,
        )
        out["v3b_external_holdout"]["holdout_hold_bars"] = holdout_hold
    finally:
        HOLD_HORIZON_BARS = in_sample_hold_orig

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    print(f"  wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
