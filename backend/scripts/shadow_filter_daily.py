#!/usr/bin/env python
"""
shadow_filter_daily.py — 단타 조건부 필터 shadow 기록 스크립트
매일 개장 전 실행: 판정(SKIP/TRADE) → daily_log.csv append
장 마감 후 재실행(--post): 당일 세션 성과 기록

Usage:
  python shadow_filter_daily.py              # 개장 전: 오늘 판정 기록
  python shadow_filter_daily.py --post       # 마감 후: 오늘 성과 append
  python shadow_filter_daily.py --date 2026-06-22  # 특정 날짜 (소급)
  python shadow_filter_daily.py --date 2026-06-22 --post

조건 (preregistration.md 고정):
  i)  전일 SP500 종가 <= -1.5%
  ii) 코스피 시가 갭다운 <= -1.0%
  iii) 코스피 전일 종가 < 20일 이동평균
"""
import sys, os, csv, sqlite3, argparse
from datetime import date, datetime, timedelta

# ── paths ──────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
LOG_CSV  = os.path.join(_ROOT, "results", "conditional_filter", "daily_log.csv")
DB_PATH  = os.path.join(_ROOT, "data", "auto_trader.db")

# ── market data fetch ───────────────────────────────────
def _fetch_ohlc(ticker, start, end):
    """yfinance fetch, returns {date_str: {open,close}} sorted."""
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance not installed: pip install yfinance")
    df = yf.download(ticker, start=start, end=end, interval="1d",
                     progress=False, auto_adjust=False)
    if df.empty:
        return {}
    result = {}
    close_col = "Close"
    open_col = "Open"
    if hasattr(df.columns, "levels"):  # MultiIndex
        close_col = ("Close", ticker)
        open_col  = ("Open",  ticker)
    for idx, row in df.iterrows():
        dt_str = str(idx)[:10]
        try:
            result[dt_str] = {
                "open":  float(row[open_col]),
                "close": float(row[close_col]),
            }
        except Exception:
            pass
    return result

def _kospi_ma20(ks_data, prev_date_str):
    """20-day SMA of KS11 closes ending at prev_date (inclusive)."""
    dates = sorted(ks_data.keys())
    if prev_date_str not in dates:
        return None
    idx = dates.index(prev_date_str)
    if idx < 19:
        return None
    closes = [ks_data[dates[i]]["close"] for i in range(idx - 19, idx + 1)]
    return sum(closes) / 20

def compute_verdict(target_date: str, sp_data: dict, ks_data: dict):
    """
    Returns (verdict, reasons, details_dict).
    verdict: "SKIP" or "TRADE"
    """
    dates_sp = sorted(sp_data.keys())
    dates_ks = sorted(ks_data.keys())

    # ① SP500 prev-day return
    sp_ret = None
    for back in range(1, 6):
        cand = (date.fromisoformat(target_date) - timedelta(days=back)).isoformat()
        if cand in sp_data:
            ci = dates_sp.index(cand)
            if ci > 0:
                prev2 = dates_sp[ci - 1]
                sp_ret = (sp_data[cand]["close"] - sp_data[prev2]["close"]) / sp_data[prev2]["close"]
            break
    cond1 = (sp_ret is not None) and (sp_ret <= -0.015)

    # ② KOSPI gap-down
    gap_pct = None
    ko_open = prev_ks_close = None
    if target_date in ks_data:
        ko_open = ks_data[target_date]["open"]
        kd_idx = dates_ks.index(target_date)
        if kd_idx > 0:
            prev_ks_close = ks_data[dates_ks[kd_idx - 1]]["close"]
            gap_pct = (ko_open - prev_ks_close) / prev_ks_close
    cond2 = (gap_pct is not None) and (gap_pct <= -0.01)

    # ③ KOSPI prev close < 20d MA
    ma20 = cond3 = None
    if target_date in dates_ks:
        kd_idx = dates_ks.index(target_date)
        if kd_idx > 0:
            prev_ks_date = dates_ks[kd_idx - 1]
            ma20 = _kospi_ma20(ks_data, prev_ks_date)
            if ma20 and prev_ks_close:
                cond3 = prev_ks_close < ma20

    reasons = []
    if cond1: reasons.append("i")
    if cond2: reasons.append("ii")
    if cond3: reasons.append("iii")
    verdict = "SKIP" if reasons else "TRADE"

    details = {
        "sp500_prev_ret": f"{sp_ret*100:+.2f}%" if sp_ret is not None else "N/A",
        "kospi_gap": f"{gap_pct*100:+.2f}%" if gap_pct is not None else "N/A",
        "kospi_prev_close": f"{prev_ks_close:.0f}" if prev_ks_close else "N/A",
        "kospi_ma20": f"{ma20:.0f}" if ma20 else "N/A",
        "cond_i": str(cond1),
        "cond_ii": str(cond2),
        "cond_iii": str(cond3),
    }
    return verdict, reasons, details

def compute_session_pnl(session_date: str):
    """
    Return (n_buy_fills, session_net_ret_pct) for session_date from DB.
    net_ret = sum(matched_pnl) / sum(matched_cost_basis)  -- FIFO per symbol.
    Includes next-day SELLs for overnight holds (up to +1 trading day).
    """
    if not os.path.exists(DB_PATH):
        return 0, None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # KST session window: UTC (session_date-1) 15:00 ~ (session_date+1) 15:00
    utc_start = (datetime.fromisoformat(session_date) - timedelta(days=1)).strftime("%Y-%m-%d") + " 15:00:00"
    utc_end   = (datetime.fromisoformat(session_date) + timedelta(days=2)).strftime("%Y-%m-%d") + " 15:00:00"
    cur.execute("""
        SELECT created_at, symbol, side, filled_quantity, avg_fill_price
        FROM order_audit_log
        WHERE created_at >= ? AND created_at < ?
          AND decision = 'APPROVED'
          AND filled_quantity > 0 AND avg_fill_price > 0
        ORDER BY created_at
    """, (utc_start, utc_end))
    db_rows = cur.fetchall()
    conn.close()

    from collections import defaultdict

    # Separate session day vs next day
    sess_cutoff = session_date + " 15:00:00"  # UTC end of session day
    buys  = defaultdict(list)   # sym -> [(qty, px)]
    sells = defaultdict(list)   # sym -> [(qty, px)]
    n_buy_fills = 0

    for raw_dt, sym, side, qty, px in db_rows:
        q, p = float(qty), float(px)
        if side == "BUY" and raw_dt < sess_cutoff:
            buys[sym].append((q, p))
            n_buy_fills += 1
        elif side == "SELL":
            sells[sym].append((q, p))

    # FIFO match per symbol
    total_pnl = total_cost = 0.0
    for sym, buy_list in buys.items():
        sell_q = list(sells.get(sym, []))
        si = 0
        sell_rem = sell_q[si][0] if sell_q else 0.0
        for bq, bp in buy_list:
            rem = bq
            while rem > 0 and si < len(sell_q):
                _, sp = sell_q[si]
                matched = min(rem, sell_rem)
                total_pnl  += matched * (sp - bp)
                total_cost += matched * bp
                rem -= matched
                sell_rem -= matched
                if sell_rem <= 0:
                    si += 1
                    sell_rem = sell_q[si][0] if si < len(sell_q) else 0.0

    if total_cost == 0:
        return n_buy_fills, None
    net_ret = total_pnl / total_cost
    return n_buy_fills, round(net_ret * 100, 4)

# ── CSV helpers ─────────────────────────────────────────
FIELDNAMES = [
    "date", "verdict", "reasons",
    "sp500_prev_ret", "kospi_gap", "kospi_prev_close", "kospi_ma20",
    "cond_i", "cond_ii", "cond_iii",
    "n_orders", "session_net_ret_pct",
    "recorded_at",
]

def load_log():
    rows = {}
    if not os.path.exists(LOG_CSV):
        return rows
    with open(LOG_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["date"]] = row
    return rows

def save_log(rows: dict):
    os.makedirs(os.path.dirname(LOG_CSV), exist_ok=True)
    with open(LOG_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for d in sorted(rows.keys()):
            w.writerow(rows[d])

# ── main ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="Target session date YYYY-MM-DD (default: today KST)")
    parser.add_argument("--post", action="store_true",
                        help="Post-session: append session P&L to today's row")
    args = parser.parse_args()

    if args.date:
        target = args.date
    else:
        # today KST
        kst_now = datetime.utcnow() + timedelta(hours=9)
        target = kst_now.strftime("%Y-%m-%d")

    print(f"Target date: {target}  mode: {'POST' if args.post else 'PRE'}")

    # Fetch market data (3 months back for MA20)
    start = (date.fromisoformat(target) - timedelta(days=90)).isoformat()
    end   = (date.fromisoformat(target) + timedelta(days=3)).isoformat()

    print("Fetching ^GSPC ...")
    sp_raw = _fetch_ohlc("^GSPC", start, end)
    sp_data = {dt: {"open": v["open"], "close": v["close"]} for dt, v in sp_raw.items()}

    print("Fetching ^KS11 ...")
    ks_raw = _fetch_ohlc("^KS11", start, end)
    ks_data = {dt: {"open": v["open"], "close": v["close"]} for dt, v in ks_raw.items()}

    rows = load_log()

    if not args.post:
        verdict, reasons, details = compute_verdict(target, sp_data, ks_data)
        row = {
            "date": target,
            "verdict": verdict,
            "reasons": "+".join(reasons) if reasons else "-",
            "sp500_prev_ret": details["sp500_prev_ret"],
            "kospi_gap": details["kospi_gap"],
            "kospi_prev_close": details["kospi_prev_close"],
            "kospi_ma20": details["kospi_ma20"],
            "cond_i": details["cond_i"],
            "cond_ii": details["cond_ii"],
            "cond_iii": details["cond_iii"],
            "n_orders": "",
            "session_net_ret_pct": "",
            "recorded_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        rows[target] = row
        save_log(rows)
        print(f"Verdict: {verdict} (reasons: {row['reasons']})")
        print(f"  i) SP500 prev: {details['sp500_prev_ret']}")
        print(f"  ii) KOSPI gap: {details['kospi_gap']}")
        print(f"  iii) prev_close {details['kospi_prev_close']} vs MA20 {details['kospi_ma20']}")
        print(f"Saved to {LOG_CSV}")

    else:
        # post: append P&L
        n_orders, net_ret = compute_session_pnl(target)
        existing = rows.get(target, {})
        if not existing:
            # Run pre first to get verdict
            verdict, reasons, details = compute_verdict(target, sp_data, ks_data)
            existing = {
                "date": target,
                "verdict": verdict,
                "reasons": "+".join(reasons) if reasons else "-",
                "sp500_prev_ret": details["sp500_prev_ret"],
                "kospi_gap": details["kospi_gap"],
                "kospi_prev_close": details["kospi_prev_close"],
                "kospi_ma20": details["kospi_ma20"],
                "cond_i": details["cond_i"],
                "cond_ii": details["cond_ii"],
                "cond_iii": details["cond_iii"],
                "recorded_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        existing["n_orders"] = n_orders
        existing["session_net_ret_pct"] = net_ret if net_ret is not None else ""
        rows[target] = existing
        save_log(rows)
        print(f"Session P&L: {net_ret:+.4f}%  (n_orders={n_orders})" if net_ret else f"P&L: N/A (n={n_orders})")
        print(f"Updated {LOG_CSV}")

if __name__ == "__main__":
    main()
