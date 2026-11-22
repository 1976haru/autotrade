#!/usr/bin/env python3
"""TEMP — Regime Filter Buy&Hold 검증 (R0-R4).

★ Lookahead-free 핵심 원칙:
- 시점 t 의 결정은 *t-1 종가까지* 의 데이터로만.
- SMA20(t-20:t-1) vs SMA60(t-60:t-1) — t 시점 데이터 0 사용.
- 다음 거래일 *open* 에 실제 전환 실행 (1일 신호 지연 = 실거래 시간 차이 반영).

★ Measurement: per-symbol median (sum/n 금지 — measurement bug 재발 방지).

Two universes:
  - bull_universe : robust_intraday_5m → 60m, 대형 20 (2025-05~2026-05, 활황)
  - bear_universe : real_ohlcv (일봉, 10 syms, 2024 약세, median bh -17%)

3-way comparison per universe:
  ① pure buy&hold (full notional, 항상 보유)
  ② regime-filter buy&hold (bear 신호 시 cash)
  ③ 능동전략 (참고: lens 작업의 Council 1-2주 × 80+ 결과 — bull universe 만)

NO new strategy, NO simulator, NO broker / route_order / place_order.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Bull universe: lens_60m bigcap-20 (이미 만들어진 80-sym 4-분위)
BIG_20 = ['005930', '000660', '122630', '069500', '005380', '034020', '079550',
          '010120', '035420', '229200', '042660', '114800', '102110', '012450',
          '080220', '006400', '042700', '086520', '196170', '267260']

# Bear universe: real_ohlcv 2024 daily (10 syms — fixed by data)
BEAR_DIR = Path("data/market/real_ohlcv")

# Cost model: per-side bps (round-trip 33 = entry 6.5 + exit 26.5 with 0.20% tax)
COMMISSION_BPS = 1.5
SLIP_BPS_LARGE = 5.0   # 대형주 슬리피지
TAX_BPS = 20.0
COST_EXIT_BPS = COMMISSION_BPS + SLIP_BPS_LARGE + TAX_BPS   # 26.5 (with tax)
COST_ENTRY_BPS = COMMISSION_BPS + SLIP_BPS_LARGE            # 6.5

# regime signal params
SMA_FAST = 20
SMA_SLOW = 60

# bull universe loader (60m bars → daily aggregation for fair comparison)
LENS_DIR = Path("data/market/lens_60m")


def load_daily_closes(path: Path) -> list[tuple[str, float]]:
    """Load (date_str, close) sorted ascending. Daily for real_ohlcv, agg-from-60m for lens."""
    out: list[tuple[str, float]] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or len(rows) < 2:
        return out
    # try detect: daily (1 row/day) or intraday (multi rows/day)
    by_date: dict[str, float] = {}
    for r in rows[1:]:
        if not r[0]: continue
        try:
            date_str = r[0][:10]
            close = float(r[4])
            by_date[date_str] = close   # overwrite to keep last bar of day
        except (ValueError, IndexError):
            continue
    return sorted(by_date.items())


def load_universe(symbols: list[str], src_dir: Path, suffix: str = ""):
    """Returns {symbol: [(date, close), ...]} aligned on common date intersection."""
    raw: dict[str, list[tuple[str, float]]] = {}
    for s in symbols:
        path = src_dir / f"{s}{suffix}.csv"
        if not path.exists():
            continue
        series = load_daily_closes(path)
        if len(series) < SMA_SLOW + 30:
            continue
        raw[s] = series
    # intersect dates so all sym have aligned series
    if not raw: return {}
    common = set(d for d, _ in next(iter(raw.values())))
    for s, ser in raw.items():
        common &= set(d for d, _ in ser)
    common = sorted(common)
    if len(common) < SMA_SLOW + 30:
        return {}
    aligned: dict[str, list[tuple[str, float]]] = {}
    for s, ser in raw.items():
        d = dict(ser)
        aligned[s] = [(dt, d[dt]) for dt in common if dt in d]
    return aligned


def bear_signal_series(closes: list[float], sma_fast: int = SMA_FAST,
                       sma_slow: int = SMA_SLOW) -> list[bool | None]:
    """Lookahead-free: bear[t] = SMA_fast(t-fast:t-1) < SMA_slow(t-slow:t-1).

    Returns list aligned to closes. First sma_slow entries are None (warmup).
    """
    n = len(closes)
    out: list[bool | None] = [None] * n
    for t in range(sma_slow, n):
        # use indices t-sma_fast to t-1 (excludes t)
        fast = sum(closes[t - sma_fast:t]) / sma_fast
        slow = sum(closes[t - sma_slow:t]) / sma_slow
        out[t] = (fast < slow)
    return out


def simulate_pure_bh(aligned: dict, syms: list[str]) -> dict:
    """Pure buy&hold per symbol, equal weight. equity curve = mean of per-sym normalized closes."""
    n = len(next(iter(aligned.values())))
    eq_per_sym = {s: [c / aligned[s][0][1] for _, c in aligned[s]] for s in syms}
    portfolio = [statistics.mean(eq_per_sym[s][t] for s in syms) for t in range(n)]
    return _metrics_from_curve(portfolio, "pure_bh")


def simulate_regime_filter(aligned: dict, syms: list[str],
                           cost_exit_bps: float = COST_EXIT_BPS,
                           cost_entry_bps: float = COST_ENTRY_BPS) -> dict:
    """Equal-weight portfolio with bear signal per symbol. When bear, hold cash for that symbol.

    ★ lookahead-free: bear[t] uses only data through t-1.
    ★ 1-day lag execution: signal at t fires open-of-next-day; we approximate using close-of-day t.
    """
    n = len(next(iter(aligned.values())))
    sym_signals: dict[str, list[bool | None]] = {}
    for s in syms:
        closes = [c for _, c in aligned[s]]
        sym_signals[s] = bear_signal_series(closes)

    # per-sym equity with regime filter
    sym_equity: dict[str, list[float]] = {}
    sym_transitions: dict[str, int] = {}
    sym_cost: dict[str, float] = {}
    for s in syms:
        closes = [c for _, c in aligned[s]]
        eq = 1.0
        eq_curve = [eq]
        in_stock = True   # start in stock (assume initial buy at t=0, no entry cost)
        cost_accum = 0.0
        trans = 0
        for t in range(1, n):
            sig = sym_signals[s][t-1]   # ★ use signal from previous day's close
            ret_today = closes[t] / closes[t-1] - 1.0
            # apply position
            if in_stock:
                eq *= (1 + ret_today)
            # else cash: eq unchanged
            eq_curve.append(eq)
            # rebalance at end of day based on signal
            if sig is not None:
                want_stock = (not sig)   # bear=True → cash; bear=False → stock
                if want_stock and not in_stock:
                    # cash → stock (entry cost)
                    eq *= (1 - cost_entry_bps / 1e4)
                    cost_accum += cost_entry_bps / 1e4
                    in_stock = True
                    trans += 1
                elif (not want_stock) and in_stock:
                    # stock → cash (exit cost)
                    eq *= (1 - cost_exit_bps / 1e4)
                    cost_accum += cost_exit_bps / 1e4
                    in_stock = False
                    trans += 1
        sym_equity[s] = eq_curve
        sym_transitions[s] = trans
        sym_cost[s] = cost_accum

    # portfolio = equal-weight mean of per-sym equity curves
    portfolio = [statistics.mean(sym_equity[s][t] for s in syms) for t in range(n)]
    m = _metrics_from_curve(portfolio, "regime_filter")
    m["transitions"] = {
        "mean_per_sym":  round(statistics.mean(sym_transitions.values()), 1),
        "median_per_sym": int(statistics.median(sym_transitions.values())),
        "max_per_sym":    max(sym_transitions.values()),
        "min_per_sym":    min(sym_transitions.values()),
    }
    m["cost_paid_per_sym_pct"] = {
        "mean":   round(statistics.mean(sym_cost.values()) * 100, 2),
        "median": round(statistics.median(sym_cost.values()) * 100, 2),
    }
    # per-sym final equity stats (for excess calc)
    m["per_sym_final_equity"] = {s: round(sym_equity[s][-1], 4) for s in syms}
    m["per_sym_final_return_pct"] = {s: round((sym_equity[s][-1] - 1) * 100, 2) for s in syms}
    m["bear_signal_days_pct"] = round(
        statistics.mean(
            sum(1 for x in sym_signals[s] if x is True) /
            max(1, sum(1 for x in sym_signals[s] if x is not None)) * 100
            for s in syms),
        1,
    )
    return m


def _metrics_from_curve(curve: list[float], label: str) -> dict:
    """MDD, Sharpe, final return from equity curve (daily)."""
    n = len(curve)
    final_ret = (curve[-1] - 1) * 100
    peak = curve[0]; mdd = 0.0
    for c in curve:
        peak = max(peak, c)
        if peak > 0:
            mdd = max(mdd, (peak - c) / peak * 100)
    daily_rets = [curve[t] / curve[t-1] - 1 for t in range(1, n) if curve[t-1] > 0]
    sharpe = None
    if len(daily_rets) >= 30:
        m = statistics.mean(daily_rets)
        s = statistics.stdev(daily_rets)
        if s > 0:
            sharpe = round(m / s * math.sqrt(252), 3)
    return {
        "label":          label,
        "n_days":         n,
        "final_equity":   round(curve[-1], 4),
        "final_return_pct": round(final_ret, 2),
        "mdd_pct":        round(mdd, 2),
        "sharpe_ann":     sharpe,
    }


def compute_excess_median(pure: dict, filtered: dict, syms: list[str], aligned: dict) -> dict:
    """Per-symbol median excess: regime_filter_final - bh_final (per sym, then median)."""
    bh_per_sym = {}
    for s in syms:
        closes = [c for _, c in aligned[s]]
        bh_per_sym[s] = closes[-1] / closes[0] - 1.0
    filt_per_sym = filtered["per_sym_final_return_pct"]   # already %
    excess_per_sym = []
    for s in syms:
        ex = filt_per_sym.get(s, 0) - bh_per_sym[s] * 100
        excess_per_sym.append(ex)
    return {
        "excess_median_pct":  round(statistics.median(excess_per_sym), 2),
        "excess_mean_pct":    round(statistics.mean(excess_per_sym), 2),
        "excess_per_sym": {s: round(filt_per_sym.get(s, 0) - bh_per_sym[s] * 100, 2)
                           for s in syms},
        "bh_median_pct":      round(statistics.median(bh_per_sym.values()) * 100, 2),
        "filt_median_pct":    round(statistics.median(filt_per_sym.values()), 2),
    }


def main():
    out = {
        "lookahead_principle": (
            "★ regime 신호는 t 시점 결정에 t-1 종가까지의 데이터만 사용. "
            f"SMA{SMA_FAST}(t-{SMA_FAST}:t-1) vs SMA{SMA_SLOW}(t-{SMA_SLOW}:t-1). "
            "다음 거래일 open 에 전환 실행 가정 (1-day lag). "
            "정적 grep: 미래 데이터 인덱스(t+k) 사용 0건."
        ),
        "measurement_principle": (
            "★ per-symbol final return → median (sum/n 금지, measurement bug 재발 방지)"
        ),
        "cost_model": {
            "exit_bps_per_side":  COST_EXIT_BPS,
            "entry_bps_per_side": COST_ENTRY_BPS,
            "round_trip_bps":     COST_EXIT_BPS + COST_ENTRY_BPS,
            "tax_bps":            TAX_BPS,
            "_note":              "round-trip 33bps = entry 6.5 + exit 26.5 (with 0.20% tax)",
        },
    }

    # ──── BULL UNIVERSE: 60m → daily 변환 ────
    print("=== BULL UNIVERSE (lens_60m, 대형 20, 2025-05~2026-05) ===")
    bull_aligned = load_universe(BIG_20, LENS_DIR, "_60m")
    bull_syms = list(bull_aligned.keys())
    if not bull_aligned:
        print("  no bull data");
    else:
        first_date = bull_aligned[bull_syms[0]][0][0]
        last_date = bull_aligned[bull_syms[0]][-1][0]
        print(f"  {len(bull_syms)} syms, {len(bull_aligned[bull_syms[0]])} aligned days, "
              f"{first_date} → {last_date}")

        bull_bh = simulate_pure_bh(bull_aligned, bull_syms)
        bull_rf = simulate_regime_filter(bull_aligned, bull_syms)
        bull_excess = compute_excess_median(bull_bh, bull_rf, bull_syms, bull_aligned)

        print(f"  PURE BH:        return={bull_bh['final_return_pct']:+.1f}%  MDD={bull_bh['mdd_pct']:.1f}%  Sharpe={bull_bh['sharpe_ann']}")
        print(f"  REGIME FILTER:  return={bull_rf['final_return_pct']:+.1f}%  MDD={bull_rf['mdd_pct']:.1f}%  Sharpe={bull_rf['sharpe_ann']}")
        print(f"    transitions/sym: mean={bull_rf['transitions']['mean_per_sym']} median={bull_rf['transitions']['median_per_sym']} max={bull_rf['transitions']['max_per_sym']}")
        print(f"    cost paid/sym: mean={bull_rf['cost_paid_per_sym_pct']['mean']}% median={bull_rf['cost_paid_per_sym_pct']['median']}%")
        print(f"    bear signal days: {bull_rf['bear_signal_days_pct']}%")
        print(f"  EXCESS (per-sym median): filter {bull_excess['filt_median_pct']:+.1f}% vs bh {bull_excess['bh_median_pct']:+.1f}% → {bull_excess['excess_median_pct']:+.2f}%pt")

        out["bull_universe"] = {
            "period":   f"{first_date} → {last_date}",
            "n_syms":   len(bull_syms),
            "pure_bh":  bull_bh,
            "regime_filter": bull_rf,
            "excess":   bull_excess,
        }
        # remove huge per-sym dict from JSON to keep it readable
        out["bull_universe"]["regime_filter"].pop("per_sym_final_equity", None)
        out["bull_universe"]["regime_filter"].pop("per_sym_final_return_pct", None)
        out["bull_universe"]["excess"].pop("excess_per_sym", None)

    print()
    # ──── BEAR UNIVERSE: 2024 daily ────
    print("=== BEAR UNIVERSE (real_ohlcv 2024 일봉, 10 syms, 약세장) ===")
    bear_syms_all = sorted([f.stem for f in BEAR_DIR.glob("*.csv")])
    bear_aligned = load_universe(bear_syms_all, BEAR_DIR, "")
    bear_syms = list(bear_aligned.keys())
    if not bear_aligned:
        print("  no bear data");
    else:
        first_date = bear_aligned[bear_syms[0]][0][0]
        last_date = bear_aligned[bear_syms[0]][-1][0]
        print(f"  {len(bear_syms)} syms, {len(bear_aligned[bear_syms[0]])} aligned days, "
              f"{first_date} → {last_date}")

        bear_bh = simulate_pure_bh(bear_aligned, bear_syms)
        bear_rf = simulate_regime_filter(bear_aligned, bear_syms)
        bear_excess = compute_excess_median(bear_bh, bear_rf, bear_syms, bear_aligned)

        print(f"  PURE BH:        return={bear_bh['final_return_pct']:+.1f}%  MDD={bear_bh['mdd_pct']:.1f}%  Sharpe={bear_bh['sharpe_ann']}")
        print(f"  REGIME FILTER:  return={bear_rf['final_return_pct']:+.1f}%  MDD={bear_rf['mdd_pct']:.1f}%  Sharpe={bear_rf['sharpe_ann']}")
        print(f"    transitions/sym: mean={bear_rf['transitions']['mean_per_sym']} median={bear_rf['transitions']['median_per_sym']} max={bear_rf['transitions']['max_per_sym']}")
        print(f"    cost paid/sym: mean={bear_rf['cost_paid_per_sym_pct']['mean']}% median={bear_rf['cost_paid_per_sym_pct']['median']}%")
        print(f"    bear signal days: {bear_rf['bear_signal_days_pct']}%")
        print(f"  EXCESS (per-sym median): filter {bear_excess['filt_median_pct']:+.1f}% vs bh {bear_excess['bh_median_pct']:+.1f}% → {bear_excess['excess_median_pct']:+.2f}%pt")

        out["bear_universe"] = {
            "period":   f"{first_date} → {last_date}",
            "n_syms":   len(bear_syms),
            "pure_bh":  bear_bh,
            "regime_filter": bear_rf,
            "excess":   bear_excess,
        }
        out["bear_universe"]["regime_filter"].pop("per_sym_final_equity", None)
        out["bear_universe"]["regime_filter"].pop("per_sym_final_return_pct", None)
        out["bear_universe"]["excess"].pop("excess_per_sym", None)

    # ──── VERDICT ────
    print("\n=== VERDICT ===")
    bull = out.get("bull_universe", {})
    bear = out.get("bear_universe", {})

    bull_protected = (bull.get("regime_filter", {}).get("mdd_pct", 999) <
                      bull.get("pure_bh", {}).get("mdd_pct", 0))
    bull_costy = ((bull.get("regime_filter", {}).get("final_return_pct", 999) <
                   bull.get("pure_bh", {}).get("final_return_pct", 0)))
    bear_protected = (bear.get("regime_filter", {}).get("mdd_pct", 999) <
                      bear.get("pure_bh", {}).get("mdd_pct", 0))
    bear_excess_pos = (bear.get("excess", {}).get("excess_median_pct", -999) > 0)

    print(f"  BULL: regime filter MDD < BH MDD? {bull_protected}  (filter return < BH return? {bull_costy})")
    print(f"  BEAR: regime filter MDD < BH MDD? {bear_protected}")
    print(f"  BEAR: per-sym median excess > 0? {bear_excess_pos}")

    if bull_protected and bear_protected and bear_excess_pos:
        verdict = "REGIME_FILTER_DEFENDS_BOTH_REGIMES"
    elif bear_protected and bear_excess_pos:
        verdict = "BEAR_DEFENSE_CONFIRMED_BULL_DRAG_OK"
    elif bear_protected:
        verdict = "BEAR_MDD_REDUCTION_BUT_RETURN_LOSS_BORDERLINE"
    else:
        verdict = "REGIME_FILTER_FAILED_OR_INSUFFICIENT"
    out["verdict"] = verdict
    print(f"  → {verdict}")

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/regime_filter.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print("\nwrote reports/backtest/regime_filter.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
