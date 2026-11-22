#!/usr/bin/env python3
"""TEMP — 주식 페어 트레이딩 탐색 (역김프 구조 이식).

★ kimp_mean_reversion 구조 그대로:
  - 진입(entry_z): 스프레드 |z| ≥ 2.0 → 벌어진 쪽 short, 좁혀진 쪽 long
  - 청산(exit_z): |z| ≤ 0.5 → 수렴
  - 손절(stop_z): |z| ≥ 3.5 → 발산, 손실 확정 청산
  - 시간청산(time_stop_days): 30일 경과
  - ★cost-block: rolling 기대수익 < 비용 → 진입 BLOCK (역김프 핵심 안전장치)

★ Lookahead-free:
  - rolling z-score: closes[t-N:t] (t 미포함)
  - rolling cointegration: 같은 window

★ 비용: 양다리 왕복 = 33bps × 2 (long leg + short leg)
        공매도 차입수수료: 연 1.5% → daily ≈ 0.6bps × hold days
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data/market/yf_multiyear")

# Natural pair candidates (같은 업종 1·2위 / 그룹)
PAIRS = [
    ("005930", "000660", "반도체 1·2위 (삼성전자-SK하이닉스)"),
    ("005380", "000270", "자동차 그룹 (현대차-기아)"),
    ("035420", "035720", "인터넷 1·2위 (NAVER-카카오)"),
    ("051910", "006400", "배터리 1·2위 (LG화학-삼성SDI)"),
    # cross-sector control pairs (의도적으로 무관 — market-neutral baseline 비교)
    ("005490", "055550", "[CONTROL] 철강-금융 (관련성 없음 예상)"),
    ("068270", "012330", "[CONTROL] 바이오-부품 (관련성 없음 예상)"),
]

KOSPI = "KS11"

# Strategy params (역김프 구조 그대로)
ENTRY_Z = 2.0
EXIT_Z  = 0.5
STOP_Z  = 3.5
TIME_STOP_DAYS = 30
ROLLING_WINDOW = 90    # ★ rolling z-score window (lookahead-free)

# Costs
COMMISSION_BPS = 1.5   # per side
SLIP_BPS = 5.0
TAX_BPS = 20.0
PAIR_ROUNDTRIP_BPS = (COMMISSION_BPS * 2 + SLIP_BPS * 2 + TAX_BPS) * 2   # 양다리 × 2
SHORT_BORROW_BPS_PER_DAY = 0.6   # 연 1.5% / 250 ≈ 0.6bps/day


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
            if c > 0: out.append((d, c))
        except (ValueError, IndexError): continue
    out.sort()
    return out


def align_pair(a: list[tuple[str, float]], b: list[tuple[str, float]]):
    """Inner-join on dates."""
    da = dict(a); db = dict(b)
    common = sorted(set(da.keys()) & set(db.keys()))
    return [(d, da[d], db[d]) for d in common]


def rolling_zscores(spread: list[float], window: int = ROLLING_WINDOW) -> list[float | None]:
    """★ lookahead-free: z[t] uses spread[t-window:t] (t 미포함)."""
    n = len(spread)
    out: list[float | None] = [None] * n
    for t in range(window, n):
        win = spread[t-window:t]   # excludes t
        mean = sum(win) / window
        var = sum((x - mean) ** 2 for x in win) / (window - 1)
        sd = math.sqrt(var) if var > 0 else 0
        if sd > 0:
            out[t] = (spread[t-1] - mean) / sd   # ★ use spread[t-1] (latest available before t)
    return out


def rolling_corr(a: list[float], b: list[float], window: int = ROLLING_WINDOW) -> list[float | None]:
    """Rolling correlation lookahead-free."""
    n = len(a)
    out: list[float | None] = [None] * n
    for t in range(window, n):
        wa = a[t-window:t]; wb = b[t-window:t]
        ma = sum(wa) / window; mb = sum(wb) / window
        cov = sum((x - ma) * (y - mb) for x, y in zip(wa, wb)) / (window - 1)
        sa = math.sqrt(sum((x - ma) ** 2 for x in wa) / (window - 1))
        sb = math.sqrt(sum((y - mb) ** 2 for y in wb) / (window - 1))
        if sa > 0 and sb > 0:
            out[t] = cov / (sa * sb)
    return out


def simulate_pair(aligned: list[tuple[str, float, float]],
                  short_allowed: bool = True,
                  long_only_alt: bool = False):
    """페어 시뮬레이션 — entry on z extreme, exit on z convergence, stop on max divergence.

    short_allowed=True: classic pair (long underperformer + short outperformer)
    long_only_alt=True: long-only relative strength — z 극단 시 *underperformer* 만 long (no short)
    """
    n = len(aligned)
    if n < ROLLING_WINDOW + 30: return None

    a_closes = [r[1] for r in aligned]
    b_closes = [r[2] for r in aligned]
    dates = [r[0] for r in aligned]

    # log-spread
    log_spread = [math.log(a) - math.log(b) for a, b in zip(a_closes, b_closes)]
    z_series = rolling_zscores(log_spread, ROLLING_WINDOW)
    corr_series = rolling_corr(
        [math.log(a_closes[i]) - math.log(a_closes[i-1]) for i in range(1, n)],
        [math.log(b_closes[i]) - math.log(b_closes[i-1]) for i in range(1, n)],
        ROLLING_WINDOW,
    )

    # state
    trades = []
    position = None   # None or dict
    entry_blocked_cost = 0

    for t in range(ROLLING_WINDOW + 1, n):
        z = z_series[t]
        if z is None: continue

        # exit conditions
        if position is not None:
            held_days = t - position["entry_t"]
            zt = z_series[t]
            # exit on convergence
            if abs(zt) <= EXIT_Z:
                pnl_pct = _close_pair_pnl(position, a_closes[t], b_closes[t], held_days,
                                          short_allowed=short_allowed,
                                          long_only_alt=long_only_alt)
                trades.append({"entry_date": position["entry_date"], "exit_date": dates[t],
                               "held_days": held_days, "entry_z": position["entry_z"],
                               "exit_z": round(zt, 3), "exit_reason": "CONVERGE",
                               "pnl_pct": pnl_pct})
                position = None
            # stop on divergence
            elif abs(zt) >= STOP_Z:
                pnl_pct = _close_pair_pnl(position, a_closes[t], b_closes[t], held_days,
                                          short_allowed=short_allowed,
                                          long_only_alt=long_only_alt)
                trades.append({"entry_date": position["entry_date"], "exit_date": dates[t],
                               "held_days": held_days, "entry_z": position["entry_z"],
                               "exit_z": round(zt, 3), "exit_reason": "STOP",
                               "pnl_pct": pnl_pct})
                position = None
            # time stop
            elif held_days >= TIME_STOP_DAYS:
                pnl_pct = _close_pair_pnl(position, a_closes[t], b_closes[t], held_days,
                                          short_allowed=short_allowed,
                                          long_only_alt=long_only_alt)
                trades.append({"entry_date": position["entry_date"], "exit_date": dates[t],
                               "held_days": held_days, "entry_z": position["entry_z"],
                               "exit_z": round(zt, 3), "exit_reason": "TIME",
                               "pnl_pct": pnl_pct})
                position = None
        # entry condition
        if position is None and abs(z) >= ENTRY_Z:
            # ★ cost-block: expected convergence return < cost → BLOCK
            # expected return = magnitude of z * historical std dev of spread (rough)
            win = log_spread[t-ROLLING_WINDOW:t]
            sd = statistics.stdev(win) if len(win) > 1 else 0
            expected_convergence_pct = abs(z) * sd   # log return ≈ pct
            # cost: roundtrip 2 sides + short borrow estimate (held days unknown, use avg 15 days)
            est_cost_pct = (PAIR_ROUNDTRIP_BPS / 1e4) + (SHORT_BORROW_BPS_PER_DAY * 15 / 1e4 if short_allowed else 0)
            if expected_convergence_pct <= est_cost_pct:
                entry_blocked_cost += 1
                continue   # COST_BLOCK

            position = {
                "entry_t":    t,
                "entry_date": dates[t],
                "entry_z":    round(z, 3),
                "entry_a":    a_closes[t],
                "entry_b":    b_closes[t],
                "z_sign":     1 if z > 0 else -1,   # +z = log(a)>log(b) → a relatively over → short a / long b
                "expected_convergence_pct": expected_convergence_pct,
            }

    return {
        "n_aligned":        n,
        "n_trades":         len(trades),
        "entries_blocked_by_cost": entry_blocked_cost,
        "trades":           trades,
        "median_corr":      (round(statistics.median([c for c in corr_series if c is not None]), 3)
                              if any(c is not None for c in corr_series) else None),
    }


def _close_pair_pnl(pos, a_now, b_now, held_days,
                    short_allowed=True, long_only_alt=False) -> float:
    """Pair PnL %  (long-short market-neutral if short_allowed, else long-only alt)."""
    # z>0 (a relatively expensive vs b) → enter: SHORT a + LONG b. expects log(a) - log(b) ↓
    # z<0 → enter: LONG a + SHORT b. expects log(a) - log(b) ↑
    a_ret = (a_now / pos["entry_a"]) - 1
    b_ret = (b_now / pos["entry_b"]) - 1

    if long_only_alt:
        # long underperformer only (lower side of spread); no short
        if pos["z_sign"] > 0:
            # a over → b is under → LONG b only
            gross = b_ret
        else:
            gross = a_ret
        cost_pct = (PAIR_ROUNDTRIP_BPS / 1e4) / 2   # 한 다리만
        return round((gross - cost_pct) * 100, 4)

    if short_allowed:
        if pos["z_sign"] > 0:
            # short a, long b
            gross = b_ret - a_ret   # +b_ret from long, +(-a_ret) from short
        else:
            gross = a_ret - b_ret
        # cost: 양다리 round-trip + short borrow
        cost_pct = (PAIR_ROUNDTRIP_BPS / 1e4) + (SHORT_BORROW_BPS_PER_DAY * held_days / 1e4)
        return round((gross - cost_pct) * 100, 4)

    return 0.0


def metrics(trades: list[dict]):
    if not trades: return None
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    sum_wins = sum(wins); sum_losses = abs(sum(losses))
    pf = sum_wins / sum_losses if sum_losses > 0 else None
    win_rate = len(wins) / len(pnls)
    expectancy = sum(pnls) / len(pnls)
    # equity curve (cumulative sum proxy, not chained for simplicity)
    cum = 0; peak = 0; mdd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        mdd = max(mdd, dd)
    return {
        "n_trades":   len(trades),
        "win_rate":   round(win_rate, 3),
        "pf":         round(pf, 3) if pf else None,
        "expectancy_pct": round(expectancy, 3),
        "sum_pct":    round(cum, 2),
        "mdd_pct":    round(mdd, 2),
        "biggest_loss_pct": round(min(pnls), 2),
        "biggest_win_pct":  round(max(pnls), 2),
        "stop_count":  sum(1 for t in trades if t["exit_reason"] == "STOP"),
        "converge_count": sum(1 for t in trades if t["exit_reason"] == "CONVERGE"),
        "time_count":  sum(1 for t in trades if t["exit_reason"] == "TIME"),
    }


def market_neutral_test(trades, kospi_data, dates):
    """Check correlation of pair daily returns with KOSPI returns."""
    if not trades: return None
    kospi_d = dict(kospi_data)
    # convert trades to daily PnL by exit_date attribution
    daily_pair = {}
    for t in trades:
        d = t["exit_date"]
        daily_pair[d] = daily_pair.get(d, 0) + t["pnl_pct"]
    # KOSPI daily returns
    matched = [(d, daily_pair[d]) for d in daily_pair if d in kospi_d]
    if len(matched) < 10: return None
    # KOSPI return on those days
    pair_rets = [v for d, v in matched]
    kospi_rets = []
    sorted_kospi = sorted(kospi_data, key=lambda x: x[0])
    kdates = [x[0] for x in sorted_kospi]
    kvals = [x[1] for x in sorted_kospi]
    for d, _ in matched:
        if d in kospi_d:
            idx = kdates.index(d)
            if idx > 0:
                kospi_rets.append((kvals[idx] / kvals[idx-1] - 1) * 100)
            else:
                kospi_rets.append(0)
    if len(pair_rets) != len(kospi_rets) or len(pair_rets) < 10:
        return None
    # Pearson
    n = len(pair_rets)
    mp = sum(pair_rets) / n; mk = sum(kospi_rets) / n
    cov = sum((p - mp) * (k - mk) for p, k in zip(pair_rets, kospi_rets)) / (n - 1)
    sp = math.sqrt(sum((p - mp) ** 2 for p in pair_rets) / (n - 1))
    sk = math.sqrt(sum((k - mk) ** 2 for k in kospi_rets) / (n - 1))
    corr = cov / (sp * sk) if sp > 0 and sk > 0 else None
    return {
        "n_match_days": n,
        "corr_with_kospi": round(corr, 3) if corr is not None else None,
        "market_neutral":  abs(corr) < 0.2 if corr is not None else None,
    }


def run_pair(a_sym: str, b_sym: str, label: str, kospi_data,
             short_allowed: bool = True, long_only_alt: bool = False):
    a = load_daily(a_sym); b = load_daily(b_sym)
    if not a or not b: return {"error": f"missing data for {a_sym} or {b_sym}"}
    aligned = align_pair(a, b)
    if len(aligned) < ROLLING_WINDOW + 100:
        return {"error": f"too few aligned days ({len(aligned)})"}
    sim = simulate_pair(aligned, short_allowed=short_allowed, long_only_alt=long_only_alt)
    if not sim: return {"error": "sim failed"}
    m = metrics(sim["trades"])
    mn = market_neutral_test(sim["trades"], kospi_data, [d for d, _, _ in aligned]) if sim["trades"] else None
    return {
        "label":            label,
        "a_symbol":         a_sym,
        "b_symbol":         b_sym,
        "n_aligned_days":   len(aligned),
        "first_date":       aligned[0][0],
        "last_date":        aligned[-1][0],
        "median_corr":      sim["median_corr"],
        "entries_blocked_by_cost": sim["entries_blocked_by_cost"],
        "n_trades":         sim["n_trades"],
        "metrics":          m,
        "market_neutral":   mn,
        "short_mode":       "long-short" if short_allowed and not long_only_alt
                             else ("long-only-alt" if long_only_alt else "n/a"),
    }


def main():
    print(f"Loading KOSPI...")
    kospi = load_daily(KOSPI)
    print(f"  KOSPI {len(kospi)} days")

    out = {
        "lookahead_principle":
            "★ rolling z[t] uses log_spread[t-90:t] (t 미포함). signal at t-1 fires t. 정적 grep [t+|i+] 매치 0.",
        "structure": {
            "entry_z":   ENTRY_Z,
            "exit_z":    EXIT_Z,
            "stop_z":    STOP_Z,
            "time_stop_days": TIME_STOP_DAYS,
            "rolling_window": ROLLING_WINDOW,
            "cost_block_rule": "expected_convergence_pct (z * sd) ≤ est_cost → BLOCK (역김프 구조)",
            "pair_roundtrip_bps":   PAIR_ROUNDTRIP_BPS,
            "short_borrow_bps_per_day": SHORT_BORROW_BPS_PER_DAY,
        },
        "results_short_allowed":   {},
        "results_long_only_alt":   {},
    }

    print("\n=== Pairs (short allowed — classic market-neutral) ===")
    for a, b, label in PAIRS:
        print(f"  {a}-{b} ({label})", flush=True)
        r = run_pair(a, b, label, kospi, short_allowed=True, long_only_alt=False)
        out["results_short_allowed"][f"{a}-{b}"] = r
        if "error" in r:
            print(f"    ERR: {r['error']}"); continue
        m = r["metrics"]; mn = r["market_neutral"]
        print(f"    days={r['n_aligned_days']} corr={r['median_corr']} blocked={r['entries_blocked_by_cost']} trades={r['n_trades']}")
        if m:
            print(f"    PF={m['pf']} win={m['win_rate']*100:.0f}% exp_pct={m['expectancy_pct']} sum%={m['sum_pct']} MDD%={m['mdd_pct']} "
                  f"stop={m['stop_count']} converge={m['converge_count']} time={m['time_count']}")
            print(f"    biggest_loss={m['biggest_loss_pct']}% biggest_win={m['biggest_win_pct']}%")
        if mn:
            print(f"    market-neutral check: corr_with_KOSPI={mn['corr_with_kospi']} market_neutral={mn['market_neutral']}")

    print("\n=== Pairs (long-only alternative — 공매도 제약 시) ===")
    for a, b, label in PAIRS:
        r = run_pair(a, b, label, kospi, short_allowed=False, long_only_alt=True)
        out["results_long_only_alt"][f"{a}-{b}"] = r
        if "error" in r:
            print(f"  {a}-{b}: ERR"); continue
        m = r["metrics"]
        if m:
            print(f"  {a}-{b}: PF={m['pf']} win={m['win_rate']*100:.0f}% sum%={m['sum_pct']} trades={m['n_trades']}")

    # ─── Verdict ───
    print("\n=== VERDICT ===")
    short_pf = {}; short_mn = {}
    for k, r in out["results_short_allowed"].items():
        if "metrics" in r and r["metrics"]:
            short_pf[k] = r["metrics"]["pf"]
            if r["market_neutral"]:
                short_mn[k] = r["market_neutral"]["corr_with_kospi"]
    long_pf = {k: r["metrics"]["pf"] if r.get("metrics") else None for k, r in out["results_long_only_alt"].items()}

    print(f"  short-allowed pairs PF ≥ 1.2: {sum(1 for v in short_pf.values() if v and v >= 1.2)}/{len(short_pf)}")
    print(f"  short-allowed pairs market-neutral (|corr|<0.2): {sum(1 for v in short_mn.values() if v is not None and abs(v) < 0.2)}/{len(short_mn)}")
    print(f"  long-only-alt pairs PF ≥ 1.2: {sum(1 for v in long_pf.values() if v and v >= 1.2)}/{len(long_pf)}")

    out["verdict"] = {
        "short_pf_pass":     sum(1 for v in short_pf.values() if v and v >= 1.2),
        "short_neutral_pass": sum(1 for v in short_mn.values() if v is not None and abs(v) < 0.2),
        "long_pf_pass":      sum(1 for v in long_pf.values() if v and v >= 1.2),
        "short_pf_total":    len(short_pf),
        "long_pf_total":     len(long_pf),
        "natural_pairs":     4,
        "control_pairs":     2,
        "korea_short_caveat": (
            "★ 한국 개인 공매도 매우 어려움: 직접 대주(차입) 사실상 불가, "
            "ELW/Knock-out 외엔 실거래 ear지 못함. 대안: 롱-only 상대강도 페어, 인버스 ETF 활용."
        ),
    }

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/pairs.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nwrote reports/backtest/pairs.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
