#!/usr/bin/env python3
"""TEMP — Pair Trading 확대 검증 (E1-E7).

★ 핵심 질문 (E4): 페어 신호 vs 단순 눌림목 벤치마크 — 페어 관계가 진짜 알파인가?

NO new strategy, NO simulator, NO broker / route_order / place_order.
"""
from __future__ import annotations

import csv, json, math, statistics, sys
from pathlib import Path

DATA_DIR = Path("data/market/yf_multiyear")
KOSPI = "KS11"

# Pair candidates (자연 + control)
PAIRS = [
    # 반도체
    ("005930", "000660", "SAME_SECTOR", "반도체 1·2위"),
    ("009150", "000660", "SAME_SECTOR", "삼성전기-SK하이닉스 (부품-메모리)"),
    # 자동차 그룹
    ("005380", "000270", "SAME_GROUP", "현대-기아"),
    # 인터넷·게임
    ("035420", "035720", "SAME_SECTOR", "NAVER-카카오"),
    ("036570", "251270", "SAME_SECTOR", "엔씨-넷마블"),
    # 배터리
    ("051910", "006400", "SAME_SECTOR", "LG화학-삼성SDI"),
    # 화학·정유
    ("096770", "010950", "SAME_SECTOR", "SK이노-S-Oil"),
    ("096770", "011170", "SAME_SECTOR", "SK이노-롯데케미칼"),
    ("010950", "011170", "SAME_SECTOR", "S-Oil-롯데케미칼"),
    # 통신
    ("017670", "030200", "SAME_SECTOR", "SKT-KT"),
    ("030200", "032640", "SAME_SECTOR", "KT-LGU+"),
    ("017670", "032640", "SAME_SECTOR", "SKT-LGU+"),
    # 철강
    ("005490", "004020", "SAME_SECTOR", "POSCO-현대제철"),
    # 보험
    ("032830", "088350", "SAME_SECTOR", "삼성생명-한화생명"),
    ("000810", "032830", "SAME_SECTOR", "삼성화재-삼성생명"),
    # 제약
    ("068270", "128940", "SAME_SECTOR", "셀트리온-한미약품"),
    # 유통
    ("139480", "023530", "SAME_SECTOR", "이마트-롯데쇼핑"),
    # 금융지주
    ("086790", "105560", "SAME_SECTOR", "하나-KB"),
    ("105560", "316140", "SAME_SECTOR", "KB-우리"),
    ("055550", "105560", "SAME_SECTOR", "신한-KB"),
    # 지주-자회사
    ("003550", "034220", "PARENT_SUB", "LG-LG디스플레이"),
    # 화학지주
    ("051910", "009830", "SAME_SECTOR", "LG화학-한화솔루션"),
    # ETF 페어
    ("069500", "102110", "ETF_TWIN", "KODEX 200-TIGER 200 (동일지수)"),
    ("091160", "139660", "ETF_TWIN", "KODEX 반도체-TIGER 반도체"),
    # CONTROL — 관련성 낮음 예상
    ("005490", "055550", "CONTROL", "철강-금융 (무관 예상)"),
    ("068270", "012330", "CONTROL", "바이오-부품 (무관 예상)"),
    ("005930", "139480", "CONTROL", "삼성전자-이마트"),
    ("000660", "086790", "CONTROL", "SK하이닉스-하나금융"),
]

# Strategy params (직전 작업과 동일)
ENTRY_Z = 2.0
EXIT_Z = 0.5
STOP_Z = 3.5
TIME_STOP_DAYS = 30
ROLLING_WINDOW = 90

COMMISSION_BPS = 1.5
SLIP_BPS = 5.0
TAX_BPS = 20.0
ONE_SIDE_ROUNDTRIP_BPS = COMMISSION_BPS * 2 + SLIP_BPS * 2 + TAX_BPS   # 33 (long-only single side roundtrip)


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


def align_pair(a, b):
    da, db = dict(a), dict(b)
    common = sorted(set(da) & set(db))
    return [(d, da[d], db[d]) for d in common]


def rolling_z(series, window=ROLLING_WINDOW):
    n = len(series); out = [None] * n
    for t in range(window, n):
        win = series[t-window:t]   # ★ excludes t
        mean = sum(win) / window
        var = sum((x - mean) ** 2 for x in win) / (window - 1)
        sd = math.sqrt(var) if var > 0 else 0
        if sd > 0:
            out[t] = (series[t-1] - mean) / sd
    return out


def rolling_corr(a, b, window=ROLLING_WINDOW):
    n = len(a); out = [None] * n
    for t in range(window, n):
        wa, wb = a[t-window:t], b[t-window:t]
        ma, mb = sum(wa)/window, sum(wb)/window
        cov = sum((x-ma)*(y-mb) for x, y in zip(wa, wb)) / (window-1)
        sa = math.sqrt(sum((x-ma)**2 for x in wa) / (window-1))
        sb = math.sqrt(sum((y-mb)**2 for y in wb) / (window-1))
        if sa > 0 and sb > 0:
            out[t] = cov / (sa * sb)
    return out


def simulate_pair_long_only(aligned, slice_lo=0, slice_hi=None):
    """Long-only pair: z extreme → underperformer long; z→0 exit."""
    if slice_hi is None: slice_hi = len(aligned)
    sub = aligned[slice_lo:slice_hi]
    if len(sub) < ROLLING_WINDOW + 30: return None

    a_closes = [r[1] for r in sub]
    b_closes = [r[2] for r in sub]
    dates = [r[0] for r in sub]
    log_spread = [math.log(a) - math.log(b) for a, b in zip(a_closes, b_closes)]
    z_series = rolling_z(log_spread, ROLLING_WINDOW)

    trades = []
    position = None
    n = len(sub)
    for t in range(ROLLING_WINDOW + 1, n):
        z = z_series[t]
        if z is None: continue

        if position is not None:
            held = t - position["entry_t"]
            # exit
            if abs(z) <= EXIT_Z:
                pnl = _close_long_only(position, a_closes[t], b_closes[t])
                trades.append({"entry_date": position["entry_date"], "exit_date": dates[t],
                               "held_days": held, "entry_z": position["entry_z"],
                               "exit_z": round(z, 3), "exit_reason": "CONVERGE",
                               "pnl_pct": pnl, "side": position["side"]})
                position = None
            elif abs(z) >= STOP_Z:
                pnl = _close_long_only(position, a_closes[t], b_closes[t])
                trades.append({"entry_date": position["entry_date"], "exit_date": dates[t],
                               "held_days": held, "entry_z": position["entry_z"],
                               "exit_z": round(z, 3), "exit_reason": "STOP",
                               "pnl_pct": pnl, "side": position["side"]})
                position = None
            elif held >= TIME_STOP_DAYS:
                pnl = _close_long_only(position, a_closes[t], b_closes[t])
                trades.append({"entry_date": position["entry_date"], "exit_date": dates[t],
                               "held_days": held, "entry_z": position["entry_z"],
                               "exit_z": round(z, 3), "exit_reason": "TIME",
                               "pnl_pct": pnl, "side": position["side"]})
                position = None
        if position is None and abs(z) >= ENTRY_Z:
            # long-only: z>0 → a over → b under → LONG b; z<0 → LONG a
            side = "B" if z > 0 else "A"
            position = {
                "entry_t": t, "entry_date": dates[t],
                "entry_z": round(z, 3), "side": side,
                "entry_a": a_closes[t], "entry_b": b_closes[t],
            }

    return {"trades": trades, "n_aligned": n, "dates": dates}


def _close_long_only(pos, a_now, b_now):
    if pos["side"] == "B":
        gross = b_now / pos["entry_b"] - 1
    else:
        gross = a_now / pos["entry_a"] - 1
    cost = ONE_SIDE_ROUNDTRIP_BPS / 1e4
    return round((gross - cost) * 100, 4)


def simulate_pullback_benchmark(aligned, slice_lo=0, slice_hi=None):
    """E4 ★ — 단순 눌림목 매수: 두 종목 각각 자체 z-score 음수 극단 시 매수, z→0 청산.

    페어 관계 *무시*. 두 종목 따로따로 단일 z 신호.
    페어 신호 vs 본 결과 비교 → 관계 정보의 추가 가치 판정.
    """
    if slice_hi is None: slice_hi = len(aligned)
    sub = aligned[slice_lo:slice_hi]
    if len(sub) < ROLLING_WINDOW + 30: return None

    a_closes = [r[1] for r in sub]
    b_closes = [r[2] for r in sub]
    dates = [r[0] for r in sub]
    # log returns → cumulative log price
    log_a = [math.log(x) for x in a_closes]
    log_b = [math.log(x) for x in b_closes]
    za = rolling_z(log_a, ROLLING_WINDOW)
    zb = rolling_z(log_b, ROLLING_WINDOW)

    trades = []
    pos_a = None; pos_b = None
    n = len(sub)
    for t in range(ROLLING_WINDOW + 1, n):
        for series, z_ser, closes, sym_label, pos_var in [
            (log_a, za, a_closes, "A", "pos_a"),
            (log_b, zb, b_closes, "B", "pos_b"),
        ]:
            z = z_ser[t]
            if z is None: continue
            cur_pos = pos_a if sym_label == "A" else pos_b
            if cur_pos is not None:
                held = t - cur_pos["entry_t"]
                if abs(z) <= EXIT_Z:
                    pnl = (closes[t] / cur_pos["entry_close"] - 1) * 100 - ONE_SIDE_ROUNDTRIP_BPS / 100
                    trades.append({"entry_date": cur_pos["entry_date"], "exit_date": dates[t],
                                   "held_days": held, "entry_z": cur_pos["entry_z"],
                                   "exit_reason": "CONVERGE", "pnl_pct": round(pnl, 4),
                                   "side": sym_label})
                    if sym_label == "A": pos_a = None
                    else: pos_b = None
                elif abs(z) >= STOP_Z:
                    pnl = (closes[t] / cur_pos["entry_close"] - 1) * 100 - ONE_SIDE_ROUNDTRIP_BPS / 100
                    trades.append({"entry_date": cur_pos["entry_date"], "exit_date": dates[t],
                                   "held_days": held, "entry_z": cur_pos["entry_z"],
                                   "exit_reason": "STOP", "pnl_pct": round(pnl, 4),
                                   "side": sym_label})
                    if sym_label == "A": pos_a = None
                    else: pos_b = None
                elif held >= TIME_STOP_DAYS:
                    pnl = (closes[t] / cur_pos["entry_close"] - 1) * 100 - ONE_SIDE_ROUNDTRIP_BPS / 100
                    trades.append({"entry_date": cur_pos["entry_date"], "exit_date": dates[t],
                                   "held_days": held, "entry_z": cur_pos["entry_z"],
                                   "exit_reason": "TIME", "pnl_pct": round(pnl, 4),
                                   "side": sym_label})
                    if sym_label == "A": pos_a = None
                    else: pos_b = None
            # entry: z 음수 극단 (눌림목)
            cur_pos = pos_a if sym_label == "A" else pos_b
            if cur_pos is None and z is not None and z <= -ENTRY_Z:
                new_pos = {"entry_t": t, "entry_date": dates[t],
                           "entry_z": round(z, 3),
                           "entry_close": closes[t]}
                if sym_label == "A": pos_a = new_pos
                else: pos_b = new_pos
    return {"trades": trades, "n_aligned": n}


def trade_metrics(trades):
    if not trades: return None
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) < 0 else None
    cum = 0; peak = 0; mdd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum); mdd = max(mdd, peak - cum)
    # Sharpe / Calmar (sum-curve based)
    daily_pnls = pnls   # approximation per-trade
    sharpe = None
    if len(pnls) >= 10:
        m = sum(pnls)/len(pnls); s = statistics.stdev(pnls) if len(pnls)>=2 else 0
        if s > 0: sharpe = round(m / s * math.sqrt(50), 3)  # ~50 trades/year for pair sim
    calmar = round(cum / mdd, 3) if mdd > 0 else None
    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins)/len(pnls), 3),
        "pf": round(pf, 3) if pf else None,
        "expectancy_pct": round(sum(pnls)/len(pnls), 3),
        "sum_pct": round(cum, 2),
        "mdd_pct": round(mdd, 2),
        "biggest_loss": round(min(pnls), 2),
        "biggest_win":  round(max(pnls), 2),
        "stop_count":   sum(1 for t in trades if t.get("exit_reason") == "STOP"),
        "sharpe_proxy": sharpe,
        "calmar":       calmar,
    }


def kospi_corr(trades, kospi_data):
    if not trades or not kospi_data: return None
    kd = dict(kospi_data); ksort = sorted(kospi_data); kdates=[x[0] for x in ksort]; kvals=[x[1] for x in ksort]
    daily_pair = {}
    for t in trades:
        d = t["exit_date"]
        daily_pair[d] = daily_pair.get(d, 0) + t["pnl_pct"]
    pair_rets = []; kospi_rets = []
    for d, v in daily_pair.items():
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
    kospi = load_daily(KOSPI)
    print(f"Loading KOSPI: {len(kospi)} days")
    print(f"\nLoading pairs ({len(PAIRS)} candidates)...")

    out = {
        "structure": {
            "entry_z": ENTRY_Z, "exit_z": EXIT_Z, "stop_z": STOP_Z,
            "time_stop_days": TIME_STOP_DAYS, "rolling_window": ROLLING_WINDOW,
            "one_side_roundtrip_bps": ONE_SIDE_ROUNDTRIP_BPS,
        },
        "lookahead_principle": "★ rolling z[t] uses series[t-90:t] EXCLUDES t. 정적 grep [t+|i+] 매치 0.",
        "n_pairs_total": len(PAIRS),
        "pairs": [],
    }

    summary_rows = []
    for a, b, kind, label in PAIRS:
        da = load_daily(a); db = load_daily(b)
        if not da or not db:
            summary_rows.append({"pair": f"{a}-{b}", "kind": kind, "label": label,
                                  "error": "missing data"})
            continue
        aligned = align_pair(da, db)
        if len(aligned) < ROLLING_WINDOW + 200:
            summary_rows.append({"pair": f"{a}-{b}", "kind": kind, "label": label,
                                  "error": f"too few days ({len(aligned)})"})
            continue
        mid = len(aligned) // 2

        # Pair signal long-only — IN / OOS
        pair_in = simulate_pair_long_only(aligned, 0, mid)
        pair_oos = simulate_pair_long_only(aligned, mid)
        pair_full = simulate_pair_long_only(aligned)
        pair_in_m  = trade_metrics(pair_in["trades"])  if pair_in else None
        pair_oos_m = trade_metrics(pair_oos["trades"]) if pair_oos else None
        pair_full_m = trade_metrics(pair_full["trades"]) if pair_full else None
        pair_kospi = kospi_corr(pair_full["trades"], kospi) if pair_full else None

        # ★ E4 — pullback benchmark (same symbols, no pair info)
        pb_in = simulate_pullback_benchmark(aligned, 0, mid)
        pb_oos = simulate_pullback_benchmark(aligned, mid)
        pb_full = simulate_pullback_benchmark(aligned)
        pb_in_m  = trade_metrics(pb_in["trades"])  if pb_in else None
        pb_oos_m = trade_metrics(pb_oos["trades"]) if pb_oos else None
        pb_full_m = trade_metrics(pb_full["trades"]) if pb_full else None

        row = {
            "pair": f"{a}-{b}",
            "kind": kind,
            "label": label,
            "n_aligned": len(aligned),
            "first_date": aligned[0][0],
            "last_date":  aligned[-1][0],
            # pair stats
            "pair_in_pf":   pair_in_m["pf"]    if pair_in_m else None,
            "pair_oos_pf":  pair_oos_m["pf"]   if pair_oos_m else None,
            "pair_full_pf": pair_full_m["pf"]  if pair_full_m else None,
            "pair_full_sum_pct": pair_full_m["sum_pct"] if pair_full_m else None,
            "pair_full_mdd_pct": pair_full_m["mdd_pct"] if pair_full_m else None,
            "pair_full_trades":  pair_full_m["n_trades"] if pair_full_m else None,
            "pair_full_biggest_loss": pair_full_m["biggest_loss"] if pair_full_m else None,
            "pair_full_stop_count":   pair_full_m["stop_count"]   if pair_full_m else None,
            "pair_full_sharpe":  pair_full_m["sharpe_proxy"] if pair_full_m else None,
            "pair_full_calmar":  pair_full_m["calmar"]       if pair_full_m else None,
            "pair_kospi_corr": pair_kospi,
            # ★ E4 pullback bench
            "pb_in_pf":   pb_in_m["pf"]    if pb_in_m else None,
            "pb_oos_pf":  pb_oos_m["pf"]   if pb_oos_m else None,
            "pb_full_pf": pb_full_m["pf"]  if pb_full_m else None,
            "pb_full_sum_pct": pb_full_m["sum_pct"] if pb_full_m else None,
            "pb_full_trades":  pb_full_m["n_trades"] if pb_full_m else None,
            # diff: pair - pullback (excess from pair info)
            "pf_diff_pair_vs_pb_full":  ((pair_full_m["pf"] - pb_full_m["pf"])
                                          if (pair_full_m and pb_full_m
                                              and pair_full_m["pf"] and pb_full_m["pf"])
                                          else None),
            "pf_diff_oos":              ((pair_oos_m["pf"] - pb_oos_m["pf"])
                                          if (pair_oos_m and pb_oos_m
                                              and pair_oos_m["pf"] and pb_oos_m["pf"])
                                          else None),
        }
        summary_rows.append(row)
        out["pairs"].append(row)

    # Print summary table
    print()
    print(f'{"pair":18s} {"label":30s} {"kind":12s} {"IN PF":>7s} {"OOS PF":>7s} {"PB OOS":>7s} {"diff":>7s} {"KOSPI":>6s} {"trades":>6s} {"MDD":>6s} {"biggest_loss":>13s}')
    for r in summary_rows:
        if "error" in r:
            print(f"  {r['pair']:18s} {r['label'][:30]:30s} ERROR: {r['error']}")
            continue
        ipf  = r['pair_in_pf'] or 0
        opf  = r['pair_oos_pf'] or 0
        pbo  = r['pb_oos_pf'] or 0
        diff = r['pf_diff_oos']
        kospi_c = r['pair_kospi_corr']
        kospi_str = f'{kospi_c:+.2f}' if kospi_c is not None else 'n/a'
        diff_str = f'{diff:+.2f}' if diff is not None else 'n/a'
        print(f"  {r['pair']:18s} {r['label'][:30]:30s} {r['kind'][:12]:12s} "
              f"{ipf:7.2f} {opf:7.2f} {pbo:7.2f} {diff_str:>7s} {kospi_str:>6s} "
              f"{(r['pair_full_trades'] or 0):6d} {(r['pair_full_mdd_pct'] or 0):6.1f} "
              f"{(r['pair_full_biggest_loss'] or 0):13.1f}")

    # ─── E3 통과율 + 우연 기대 ───
    natural = [r for r in summary_rows if r.get("kind") not in ("CONTROL", None) and "error" not in r]
    controls = [r for r in summary_rows if r.get("kind") == "CONTROL" and "error" not in r]
    n_nat = len(natural); n_ctrl = len(controls)
    nat_oos_pass = sum(1 for r in natural if (r.get("pair_oos_pf") or 0) >= 1.2)
    ctrl_oos_pass = sum(1 for r in controls if (r.get("pair_oos_pf") or 0) >= 1.2)
    # 1.2 PF 무작위 통과율 ~30-40% (정규분포 가정 — 보수적으로 가정)
    expected_random = 0.35   # 추정치
    out["E3_pass_rate"] = {
        "n_natural": n_nat,
        "n_control": n_ctrl,
        "natural_oos_pass_count": nat_oos_pass,
        "natural_oos_pass_rate":  round(nat_oos_pass / max(1, n_nat), 3),
        "control_oos_pass_count": ctrl_oos_pass,
        "control_oos_pass_rate":  round(ctrl_oos_pass / max(1, n_ctrl), 3),
        "expected_random_rate":   expected_random,
    }
    print(f"\n=== E3 통과율 ===")
    print(f"  natural pairs: {nat_oos_pass}/{n_nat} OOS PF≥1.2 ({nat_oos_pass/max(1,n_nat)*100:.0f}%)")
    print(f"  control pairs: {ctrl_oos_pass}/{n_ctrl} OOS PF≥1.2 ({ctrl_oos_pass/max(1,n_ctrl)*100:.0f}%)")
    print(f"  random expected ~{expected_random*100:.0f}%")
    print(f"  natural > control? {nat_oos_pass/max(1,n_nat) > ctrl_oos_pass/max(1,n_ctrl)}")

    # ─── ★ E4 페어신호 vs 단순눌림목 분리 ===
    print(f"\n=== ★ E4 — 페어 신호 vs 단순 눌림목 벤치마크 ===")
    diffs_oos = [r["pf_diff_oos"] for r in summary_rows
                  if r.get("pf_diff_oos") is not None]
    diffs_full = [r["pf_diff_pair_vs_pb_full"] for r in summary_rows
                   if r.get("pf_diff_pair_vs_pb_full") is not None]
    if diffs_oos:
        med_diff = statistics.median(diffs_oos)
        positive = sum(1 for d in diffs_oos if d > 0)
        print(f"  diff (OOS): median={med_diff:+.3f}, pair>pullback in {positive}/{len(diffs_oos)} pairs")
        out["E4_pair_vs_pullback"] = {
            "diff_oos_median":         round(med_diff, 3),
            "pair_outperforms_count":  positive,
            "n_pairs":                 len(diffs_oos),
            "verdict_pair_adds_value": positive > len(diffs_oos) * 0.6 and med_diff > 0,
        }
    else:
        out["E4_pair_vs_pullback"] = {"note": "insufficient data"}

    # ─── KOSPI corr 분포 ───
    kospi_corrs = [r["pair_kospi_corr"] for r in summary_rows if r.get("pair_kospi_corr") is not None]
    if kospi_corrs:
        neutral_count = sum(1 for c in kospi_corrs if abs(c) < 0.2)
        out["market_neutrality"] = {
            "n_with_corr":   len(kospi_corrs),
            "neutral_count": neutral_count,
            "neutral_rate":  round(neutral_count/len(kospi_corrs), 3),
            "median_corr":   round(statistics.median(kospi_corrs), 3),
        }
        print(f"\n=== Market-neutral 분포 ({len(kospi_corrs)} pairs) ===")
        print(f"  median KOSPI corr: {statistics.median(kospi_corrs):+.3f}")
        print(f"  neutral (|corr|<0.2): {neutral_count}/{len(kospi_corrs)} ({neutral_count/len(kospi_corrs)*100:.0f}%)")

    # ─── verdict ───
    print(f"\n=== VERDICT ===")
    e4 = out.get("E4_pair_vs_pullback", {})
    e3 = out.get("E3_pass_rate", {})
    nat_rate = e3.get("natural_oos_pass_rate") or 0
    ctrl_rate = e3.get("control_oos_pass_rate") or 0
    pair_robust = nat_rate >= 0.5
    pair_adds_value = e4.get("verdict_pair_adds_value", False)
    nat_over_ctrl = nat_rate > ctrl_rate + 0.1

    if pair_robust and pair_adds_value and nat_over_ctrl:
        verdict = "PAIR_CATEGORY_ROBUST_AND_ADDS_VALUE"
    elif pair_robust and pair_adds_value:
        verdict = "PAIR_ROBUST_VALUE_PRESENT_BUT_CTRL_ALSO_PASS"
    elif pair_robust:
        verdict = "PAIR_ROBUST_BUT_PULLBACK_EQUALLY_GOOD"
    elif pair_adds_value:
        verdict = "PAIR_NOT_ROBUST_BUT_SOMETIMES_ADDS_VALUE"
    else:
        verdict = "PAIR_NEITHER_ROBUST_NOR_ADD_VALUE"
    out["verdict"] = verdict
    print(f"  natural pass rate: {nat_rate*100:.0f}% (control: {ctrl_rate*100:.0f}%)")
    print(f"  pair adds value over pullback (E4): {pair_adds_value}")
    print(f"  → {verdict}")

    Path("reports/backtest").mkdir(parents=True, exist_ok=True)
    Path("reports/backtest/pairs_expand.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nwrote reports/backtest/pairs_expand.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
