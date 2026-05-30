#!/usr/bin/env python3
"""TEMP — 오버나이트 전략 백테스트 (PART 2): 장마감 정보 종합 → 아침 매수 → 청산.

가설: 하루 1거래로 단타 비용벽(왕복 33bps)을 회피하면서, 전날 미국시장 + 거래량
신호로 다음날 아침 갭/장중 상승을 먹는다.

설계 (lookahead 0 — 오버나이트는 미래 갭 엿보기 쉬워 엄격히):
  - 신호는 *전날 종가까지의 정보*만: (a) 전날 미국시장(S&P/NASDAQ/SOX) 등락,
    (b) 한국종목 전날까지 거래량 패턴(전일 거래량 / 20일 평균).
  - 진입: 다음날 *시가(open)* 매수 (신호일 종가엔 미국시장 이미 마감 → 정보 사용 OK,
    한국 시가는 다음 거래일 — 미래 시가를 신호로 쓰지 않음).
  - 청산: 같은 날 *종가(close)* (하루 1거래). 변형으로 익일 시가 청산도 측정.
  - 수익 = (청산가 - 진입 시가) / 진입 시가 - 비용. 갭하락(밤사이 악재)은 시가가
    낮게 열리므로 자동 반영(시가 매수라 갭다운을 "사는" 게 아니라 갭다운 후 진입).
    ★중요: 진짜 위험은 "장중 추가 하락" — close < open 이면 손실로 그대로 잡힘.

비용: 왕복 33bps (매수 6.5 + 매도 26.5). 신호 없으면 현금(수익 0).
벤치마크: 같은 종목 buy&hold. OOS 분할 + per-symbol median.
판정: 비용 후 PF>1 & buy&hold 초과 & OOS 유지 → "모의 후보", 아니면 탈락.

출력: reports/backtest/_overnight.json
"""
from __future__ import annotations
import json, glob, os
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd

KR_DIR = Path("data/market/yf_multiyear")
US_DIR = Path("data/market/us_indices")
OUT = Path("reports/backtest/_overnight.json")

BUY_BPS = 6.5 / 1e4
SELL_BPS = 26.5 / 1e4
ROUNDTRIP = BUY_BPS + SELL_BPS  # 33bps
INDEX_SYM = "KS11"
ETF_SYM = "069500"


def _r(x, n=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), n)


def load_kr():
    out = {}
    for f in glob.glob(str(KR_DIR / "*.csv")):
        sym = os.path.basename(f)[:-4]
        if sym in (INDEX_SYM,) or not sym.isdigit():
            continue
        df = pd.read_csv(f, usecols=["timestamp", "open", "high", "low", "close", "volume"])
        # tz-naive date only (avoid ancient-date DST localization errors)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None).dt.normalize()
        df = df.dropna().drop_duplicates("timestamp", keep="last").set_index("timestamp").sort_index()
        df = df[(df["open"] > 0) & (df["close"] > 0)]
        if len(df) > 300:
            out[sym] = df
    return out


def load_us():
    us = {}
    for name in ("SP500", "NASDAQ", "SOX"):
        p = US_DIR / f"{name}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, usecols=["timestamp", "close"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None).dt.normalize()
        df = df.dropna().drop_duplicates("timestamp", keep="last").set_index("timestamp").sort_index()
        # US date D return becomes actionable on KR day D+1; shift US date forward by 1 calendar day
        # so reindex(method=ffill) onto KR dates picks up the prior US session.
        s = df["close"].pct_change()
        s.index = s.index + pd.Timedelta(days=1)
        us[name] = s
    return us


def metrics_from_trades(rets: list[float]) -> dict:
    if not rets:
        return {"n": 0, "pf": None, "win_rate": None, "avg": None, "expectancy": None, "total": None}
    arr = np.array(rets)
    wins = arr[arr > 0].sum()
    losses = -arr[arr < 0].sum()
    pf = (wins / losses) if losses > 0 else (None if wins == 0 else float("inf"))
    return {
        "n": len(arr), "pf": _r(pf), "win_rate": _r((arr > 0).mean()),
        "avg": _r(arr.mean()), "expectancy": _r(arr.mean()),
        "total": _r(np.prod(1 + arr) - 1),
        "median": _r(np.median(arr)),
    }


def run_overnight(kr, us, *, us_thresh=0.0, vol_min=1.0, exit_mode="close", apply_cost=True):
    """For each KR symbol/day: signal = prev-US-up (avg of available US idx > us_thresh)
    AND prev-day volume / 20d-avg >= vol_min. Enter next open, exit same close (or next open).
    Returns per-symbol trade returns + pooled."""
    # combined US signal: average of SP500/NASDAQ/SOX prev-day return, shifted so it is known
    # at KR close of day t (US day t-1 close is known before KR open t+1). We align by KR date:
    # signal for KR entry on day D uses US return of the US session ending the night before D.
    # Approx: US return on calendar day D-1 (KST normalized). Use reindex+ffill(limit=1).
    us_df = pd.DataFrame(us)
    us_avg = us_df.mean(axis=1)  # daily mean of available indices
    per_symbol = {}
    pooled = []
    for sym, df in kr.items():
        df = df.copy()
        df["vol20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol20"]
        # US_avg index already shifted +1 calendar day in load_us(), so reindex(ffill)
        # onto KR date D yields the prior US session's return (known before KR open D).
        # No further shift needed for US. Volume uses prev-day ratio (shift 1).
        sig_us = us_avg.reindex(df.index, method="ffill")
        sig_vol = df["vol_ratio"].shift(1)  # prev day's volume ratio (known before open)
        rets = []
        idx = df.index
        for i in range(1, len(df)):
            su = sig_us.iloc[i]
            sv = sig_vol.iloc[i]
            if pd.isna(su) or pd.isna(sv):
                continue
            if su > us_thresh and sv >= vol_min:
                o = df["open"].iloc[i]
                if exit_mode == "close":
                    x = df["close"].iloc[i]
                else:  # next open
                    if i + 1 >= len(df):
                        continue
                    x = df["open"].iloc[i + 1]
                if o and o > 0 and x and x > 0:
                    gross = x / o - 1.0
                    net = gross - (ROUNDTRIP if apply_cost else 0.0)
                    rets.append(net)
        if rets:
            per_symbol[sym] = rets
            pooled.extend(rets)
    return per_symbol, pooled


def buy_hold_compare(kr):
    """Annualized-ish: per-symbol total buy&hold return over the period for context."""
    rs = []
    for sym, df in kr.items():
        c = df["close"]
        rs.append(c.iloc[-1] / c.iloc[0] - 1.0)
    return _r(float(np.median(rs))) if rs else None


def oos_split(per_symbol, k=3):
    """Split each symbol's trades chronologically? We only have returns list (ordered by date).
    Pool by thirds of the *pooled ordered-by-symbol* is not chronological. Instead recompute
    pooled with dates. Simpler: report per-symbol median PF stability via thirds of each symbol."""
    # pooled chronological is hard here; approximate OOS by splitting each symbol's own trade
    # sequence into halves and checking sign stability.
    first_pf, second_pf = [], []
    for sym, rets in per_symbol.items():
        if len(rets) < 20:
            continue
        h = len(rets) // 2
        m1 = metrics_from_trades(rets[:h]); m2 = metrics_from_trades(rets[h:])
        if m1["pf"] is not None:
            first_pf.append(m1["pf"])
        if m2["pf"] is not None:
            second_pf.append(m2["pf"])
    return {
        "first_half_median_pf": _r(float(np.median(first_pf))) if first_pf else None,
        "second_half_median_pf": _r(float(np.median(second_pf))) if second_pf else None,
        "n_symbols": len([s for s in per_symbol if len(per_symbol[s]) >= 20]),
    }


def main():
    kr = load_kr()
    us = load_us()
    res = {"meta": {
        "kr_symbols": len(kr), "us_indices": list(us.keys()),
        "roundtrip_bps": ROUNDTRIP * 1e4,
        "news_data": "확인불가 — 무료 과거 뉴스 데이터 미확보. 거래량+미국시장만으로 1차 검증.",
        "design": "signal=prev US up + prev vol ratio; enter next OPEN, exit same CLOSE; lookahead 0",
        "buyhold_median_total": buy_hold_compare(kr),
    }}
    if not us:
        res["error"] = "US index data not available"
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print("NO US DATA"); return

    # grid over signal thresholds (simple — avoid overfit; report all)
    grid = []
    for us_t in [0.0, 0.005]:
        for vmin in [1.0, 1.5]:
            for exit_mode in ["close", "nextopen"]:
                ps_net, pool_net = run_overnight(kr, us, us_thresh=us_t, vol_min=vmin, exit_mode=exit_mode, apply_cost=True)
                ps_gross, pool_gross = run_overnight(kr, us, us_thresh=us_t, vol_min=vmin, exit_mode=exit_mode, apply_cost=False)
                m_net = metrics_from_trades(pool_net)
                m_gross = metrics_from_trades(pool_gross)
                # per-symbol median net expectancy
                sym_exp = [np.mean(r) for r in ps_net.values() if len(r) >= 20]
                oos = oos_split(ps_net)
                grid.append({
                    "us_thresh": us_t, "vol_min": vmin, "exit": exit_mode,
                    "n_trades": m_net["n"],
                    "pf_gross": m_gross["pf"], "pf_net": m_net["pf"],
                    "win_rate_net": m_net["win_rate"],
                    "avg_net_bps": _r(m_net["avg"] * 1e4, 1) if m_net["avg"] is not None else None,
                    "total_net": m_net["total"],
                    "per_symbol_median_exp_bps": _r(float(np.median(sym_exp)) * 1e4, 1) if sym_exp else None,
                    "oos_first_half_pf": oos["first_half_median_pf"],
                    "oos_second_half_pf": oos["second_half_median_pf"],
                    "oos_n_symbols": oos["n_symbols"],
                })
    res["grid"] = grid
    res["meta"]["total_combos"] = len(grid)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print(f"KR symbols: {len(kr)}, US: {list(us.keys())}, buyhold median total: {res['meta']['buyhold_median_total']}")
    print("\nGRID (cost-after):")
    print("us_t  vmin exit      nTrades pfGross pfNet  winR  avgNet(bps) total  symMedExp(bps) oos1pf oos2pf")
    for g in grid:
        print(f"{g['us_thresh']:<5} {g['vol_min']:<4} {g['exit']:<9} {g['n_trades']:<7} "
              f"{str(g['pf_gross']):<7} {str(g['pf_net']):<6} {str(g['win_rate_net']):<5} "
              f"{str(g['avg_net_bps']):<11} {str(g['total_net']):<6} {str(g['per_symbol_median_exp_bps']):<14} "
              f"{str(g['oos_first_half_pf']):<6} {str(g['oos_second_half_pf'])}")


if __name__ == "__main__":
    main()
