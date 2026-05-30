#!/usr/bin/env python3
"""TEMP — B축 재검증: 횡단면(상대강도) 모멘텀, 표본 확대 + 생존편향 정직 처리.

백테스트 전용. 실거래/주문/UI 0건. 안전 플래그 변경 0건.
- 데이터: data/market/yf_multiyear/*.csv (일봉, ~2000~2026, KR 대형주 survivors).
- Lookahead 0: 리밸런싱일 t 의 신호는 close[t] 이하만 사용, 수익은 t+1 부터.
- 비용: 매수레그 6.5bps(1.5수수료+5슬리피지) / 매도레그 26.5bps(1.5+20세금+5)
        => 완전교체 왕복 33bps. 회전율 기반(M6 교체비용 민감도 자동 반영).
- 측정: 포트폴리오 단위 CAGR/Sharpe/Calmar/MDD + per-symbol 기여 보조.
- 벤치마크: KOSPI(KS11) buy&hold / 동일가중 universe(같은 주기 리밸런싱) / 모멘텀.
- 생존편향: 본 universe 는 *현재* 대형주 → 절대수익 부풀려짐. 하지만 모멘텀 -
  동일가중(같은 universe) 차이는 같은 표본을 쓰므로 생존편향이 상당부분 상쇄됨.
  그 차이(within-universe 프리미엄)를 핵심 판정 지표로 사용. 한계는 리포트에 명시.

출력: reports/backtest/momentum_verify_20260530.json (수치) — MD 는 사람이 작성.
"""
from __future__ import annotations
import json, glob, os
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path("data/market/yf_multiyear")
OUT_JSON = Path("reports/backtest/momentum_verify_20260530.json")

BUY_BPS = 6.5 / 1e4    # 1.5 commission + 5 slippage
SELL_BPS = 26.5 / 1e4  # 1.5 commission + 20 tax + 5 slippage
TRADING_DAYS = 252
INDEX_SYM = "KS11"        # KOSPI index (regime + buy&hold benchmark)
ETF_SYM = "069500"        # KODEX 200 ETF (investable benchmark)

# ----------------------------------------------------------------------------
def load_matrix():
    files = sorted(glob.glob(str(DATA / "*.csv")))
    closes = {}
    for f in files:
        sym = os.path.basename(f)[:-4]
        df = pd.read_csv(f, usecols=["timestamp", "close"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Seoul").dt.normalize()
        df = df.dropna(subset=["close"]).drop_duplicates("timestamp", keep="last")
        s = df.set_index("timestamp")["close"].astype(float)
        s = s[s > 0]
        closes[sym] = s
    mat = pd.DataFrame(closes).sort_index()
    return mat

def metrics(daily_ret: pd.Series) -> dict:
    """daily_ret: simple daily returns (already net of cost). Empty-safe."""
    r = daily_ret.dropna()
    if len(r) < 5:
        return {"cagr": None, "vol": None, "sharpe": None, "mdd": None, "calmar": None, "days": int(len(r)), "total_return": None}
    eq = (1 + r).cumprod()
    n_years = len(r) / TRADING_DAYS
    total = eq.iloc[-1] - 1.0
    cagr = eq.iloc[-1] ** (1 / n_years) - 1 if n_years > 0 and eq.iloc[-1] > 0 else None
    vol = r.std() * np.sqrt(TRADING_DAYS)
    sharpe = (r.mean() * TRADING_DAYS) / vol if vol and vol > 0 else None
    peak = eq.cummax()
    dd = eq / peak - 1.0
    mdd = dd.min()
    calmar = (cagr / abs(mdd)) if (cagr is not None and mdd < 0) else None
    return {"cagr": _r(cagr), "vol": _r(vol), "sharpe": _r(sharpe), "mdd": _r(mdd),
            "calmar": _r(calmar), "days": int(len(r)), "total_return": _r(total)}

def _r(x, n=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), n)

# ----------------------------------------------------------------------------
def run_momentum(mat: pd.DataFrame, ret: pd.DataFrame, universe: list[str],
                 N: int, L: int, gap: int, M: int, exclude: set[str] | None = None):
    """Returns (net_daily_ret Series, gross_daily_ret Series, per_symbol_contrib dict,
    avg_turnover, n_rebalances). No lookahead."""
    exclude = exclude or set()
    dates = mat.index
    idx_all = list(range(len(dates)))
    # first index where we can compute momentum: need i-gap-L >= 0
    start = gap + L
    if start >= len(dates):
        return None
    rebal_idx = list(range(start, len(dates), M))
    rebal_set = set(rebal_idx)
    weights = {}  # symbol -> weight (drifted)
    contrib = {s: 0.0 for s in universe}
    net = {}; gross = {}
    turnovers = []
    started = False
    for i in range(start, len(dates)):
        d = dates[i]
        # 1) earn return from i-1 -> i using current (pre-rebalance) weights
        if started and weights:
            day_r = 0.0
            for s, w in weights.items():
                rr = ret.iat[i, ret.columns.get_loc(s)]
                if pd.notna(rr):
                    day_r += w * rr
                    contrib[s] += w * rr
            # drift weights
            new_w = {}
            denom = 1 + day_r
            for s, w in weights.items():
                rr = ret.iat[i, ret.columns.get_loc(s)]
                rr = 0.0 if pd.isna(rr) else rr
                new_w[s] = w * (1 + rr) / denom if denom != 0 else w
            weights = new_w
            gross[d] = day_r
            net[d] = day_r
        # 2) rebalance at close of day i (affects i+1 returns); cost hits today
        if i in rebal_set:
            # eligible symbols: have close[i], close[i-gap], close[i-gap-L]
            elig = []
            for s in universe:
                if s in exclude:
                    continue
                c_now = mat.iat[i, mat.columns.get_loc(s)]
                c_rec = mat.iat[i - gap, mat.columns.get_loc(s)]
                c_old = mat.iat[i - gap - L, mat.columns.get_loc(s)]
                if pd.notna(c_now) and pd.notna(c_rec) and pd.notna(c_old) and c_old > 0:
                    mom = c_rec / c_old - 1.0
                    elig.append((s, mom))
            if len(elig) >= N:
                elig.sort(key=lambda x: x[1], reverse=True)
                picks = [s for s, _ in elig[:N]]
                target = {s: 1.0 / N for s in picks}
                # turnover cost vs current drifted weights
                buy = 0.0; sell = 0.0
                allsyms = set(weights) | set(target)
                for s in allsyms:
                    delta = target.get(s, 0.0) - weights.get(s, 0.0)
                    if delta > 0: buy += delta
                    else: sell += -delta
                cost = buy * BUY_BPS + sell * SELL_BPS
                turnovers.append(buy + sell)
                # apply cost as negative return today
                if d in net:
                    net[d] -= cost
                else:
                    net[d] = -cost; gross[d] = 0.0
                weights = target
                started = True
    net_s = pd.Series(net).sort_index()
    gross_s = pd.Series(gross).sort_index()
    return {
        "net": net_s, "gross": gross_s, "contrib": contrib,
        "avg_turnover": _r(float(np.mean(turnovers)) if turnovers else None),
        "n_rebal": len(turnovers),
    }

def run_equal_weight(mat, ret, universe, M, exclude=None):
    """Equal-weight all eligible (listed) symbols, rebalanced every M days."""
    exclude = exclude or set()
    dates = mat.index
    start = 1
    rebal_set = set(range(start, len(dates), M))
    weights = {}
    net = {}
    turnovers = []
    started = False
    for i in range(start, len(dates)):
        d = dates[i]
        if started and weights:
            day_r = 0.0
            for s, w in weights.items():
                rr = ret.iat[i, ret.columns.get_loc(s)]
                if pd.notna(rr): day_r += w * rr
            denom = 1 + day_r
            weights = {s: w * (1 + (0 if pd.isna(ret.iat[i, ret.columns.get_loc(s)]) else ret.iat[i, ret.columns.get_loc(s)])) / denom
                       for s, w in weights.items()} if denom != 0 else weights
            net[d] = day_r
        if i in rebal_set:
            elig = [s for s in universe if s not in exclude and pd.notna(mat.iat[i, mat.columns.get_loc(s)])]
            if elig:
                target = {s: 1.0 / len(elig) for s in elig}
                buy = sum(max(target.get(s,0)-weights.get(s,0),0) for s in set(weights)|set(target))
                sell = sum(max(weights.get(s,0)-target.get(s,0),0) for s in set(weights)|set(target))
                cost = buy*BUY_BPS + sell*SELL_BPS
                net[d] = net.get(d, 0.0) - cost
                turnovers.append(buy+sell)
                weights = target
                started = True
    return pd.Series(net).sort_index()

def buy_hold(mat, sym):
    s = mat[sym].dropna()
    return s.pct_change().dropna()

# ----------------------------------------------------------------------------
def regime_labels(mat):
    """KOSPI MA200 regime per date: up / down / sideways."""
    k = mat[INDEX_SYM].dropna()
    ma = k.rolling(200).mean()
    slope = ma.diff(20)
    lab = pd.Series(index=k.index, dtype=object)
    for d in k.index:
        if pd.isna(ma[d]):
            lab[d] = "warmup"; continue
        above = k[d] > ma[d]
        rising = slope[d] > 0 if pd.notna(slope[d]) else False
        if above and rising: lab[d] = "up"
        elif (not above) and (not rising): lab[d] = "down"
        else: lab[d] = "sideways"
    return lab

def split_metrics(net_mom, net_eq, k_splits=5):
    """Chronological splits; excess CAGR (mom - equalweight) per split."""
    common = net_mom.index.intersection(net_eq.index)
    m = net_mom.reindex(common); e = net_eq.reindex(common)
    n = len(common)
    out = []
    for j in range(k_splits):
        a = j * n // k_splits; b = (j + 1) * n // k_splits
        seg_m = m.iloc[a:b]; seg_e = e.iloc[a:b]
        mm = metrics(seg_m); me = metrics(seg_e)
        cm = mm["cagr"]; ce = me["cagr"]
        out.append({
            "split": j + 1,
            "from": str(common[a].date()), "to": str(common[b-1].date()),
            "mom_cagr": cm, "eq_cagr": ce,
            "excess_cagr_pp": _r((cm - ce) * 100, 2) if (cm is not None and ce is not None) else None,
            "mom_sharpe": mm["sharpe"], "eq_sharpe": me["sharpe"],
        })
    return out

def regime_excess(net_mom, net_eq, labels):
    common = net_mom.index.intersection(net_eq.index)
    diff = (net_mom.reindex(common) - net_eq.reindex(common))
    lab = labels.reindex(common).fillna("warmup")
    out = {}
    for rg in ["up", "sideways", "down"]:
        sub = diff[lab == rg]
        if len(sub) > 5:
            ann = sub.mean() * TRADING_DAYS
            out[rg] = {"days": int(len(sub)), "ann_excess_pp": _r(ann * 100, 2),
                       "mean_daily_bps": _r(sub.mean() * 1e4, 2)}
        else:
            out[rg] = {"days": int(len(sub)), "ann_excess_pp": None}
    return out

# ----------------------------------------------------------------------------
def main():
    mat = load_matrix()
    all_syms = list(mat.columns)
    universe = [s for s in all_syms if s not in (INDEX_SYM,) and s.isdigit()]
    # restrict to common period start where index exists
    ret = mat.pct_change()
    print(f"loaded {len(all_syms)} symbols ({len(universe)} equities), "
          f"{mat.index.min().date()}..{mat.index.max().date()}, {len(mat)} days")

    result = {
        "meta": {
            "data_start": str(mat.index.min().date()), "data_end": str(mat.index.max().date()),
            "n_days": int(len(mat)), "n_symbols_total": len(all_syms),
            "n_equities": len(universe), "universe": universe,
            "cost_buy_bps": BUY_BPS*1e4, "cost_sell_bps": SELL_BPS*1e4,
            "roundtrip_bps": (BUY_BPS+SELL_BPS)*1e4,
            "survivorship_note": "current large-cap survivors; absolute returns inflated; "
                                 "momentum-minus-equalweight differential largely cancels universe bias",
        },
        "benchmarks": {}, "sweep": [], "deep_dive": {},
    }

    # Benchmarks
    result["benchmarks"]["kospi_buyhold"] = metrics(buy_hold(mat, INDEX_SYM))
    if ETF_SYM in mat.columns:
        result["benchmarks"]["kodex200_buyhold"] = metrics(buy_hold(mat, ETF_SYM))

    # ---- M2 sweep ----
    Ns = [5, 10, 20]; Ls = [126, 252]; gaps = [0, 21]; Ms = [21, 63]
    combos = 0
    eq_cache = {}
    for M in Ms:
        eq_cache[M] = run_equal_weight(mat, ret, universe, M)
    for N in Ns:
        for L in Ls:
            for g in gaps:
                for M in Ms:
                    combos += 1
                    r = run_momentum(mat, ret, universe, N, L, g, M)
                    if r is None:
                        continue
                    mm = metrics(r["net"]); mg = metrics(r["gross"])
                    eqm = metrics(eq_cache[M])
                    excess_eq = _r((mm["cagr"] - eqm["cagr"]) * 100, 2) if (mm["cagr"] is not None and eqm["cagr"] is not None) else None
                    excess_k = _r((mm["cagr"] - result["benchmarks"]["kospi_buyhold"]["cagr"]) * 100, 2) if mm["cagr"] is not None else None
                    result["sweep"].append({
                        "N": N, "L": L, "gap": g, "M": M,
                        "net": mm, "gross_cagr": mg["cagr"],
                        "eq_cagr": eqm["cagr"],
                        "excess_vs_equalweight_pp": excess_eq,
                        "excess_vs_kospi_pp": excess_k,
                        "avg_turnover": r["avg_turnover"], "n_rebal": r["n_rebal"],
                    })
    result["meta"]["total_combos"] = combos

    # ---- Deep dive on representative standard 12-1 monthly: N=10,L=252,g=21,M=21 ----
    cfg = dict(N=10, L=252, gap=21, M=21)
    base = run_momentum(mat, ret, universe, **cfg)
    eq_base = eq_cache[21]
    labels = regime_labels(mat)
    dd = {
        "config": cfg,
        "momentum_net": metrics(base["net"]),
        "momentum_gross": metrics(base["gross"]),
        "equalweight": metrics(eq_base),
        "kospi": result["benchmarks"]["kospi_buyhold"],
        "splits": split_metrics(base["net"], eq_base, 5),
        "regime_excess": regime_excess(base["net"], eq_base, labels),
    }
    # M3 outlier: top contributors
    contrib = base["contrib"]
    top_contrib = sorted(contrib.items(), key=lambda x: x[1], reverse=True)
    dd["top_contributors"] = [{"sym": s, "contrib_sum": _r(v, 3)} for s, v in top_contrib[:6]]
    # exclude top1 and top2
    for k_excl in (1, 2):
        excl = set(s for s, _ in top_contrib[:k_excl])
        r_ex = run_momentum(mat, ret, universe, exclude=excl, **cfg)
        eq_ex = run_equal_weight(mat, ret, universe, M=21, exclude=excl)
        mm = metrics(r_ex["net"]); ee = metrics(eq_ex)
        dd[f"exclude_top{k_excl}"] = {
            "excluded": sorted(excl),
            "momentum_cagr": mm["cagr"], "equalweight_cagr": ee["cagr"],
            "excess_vs_equalweight_pp": _r((mm["cagr"]-ee["cagr"])*100,2) if (mm["cagr"] is not None and ee["cagr"] is not None) else None,
            "momentum_sharpe": mm["sharpe"], "mdd": mm["mdd"],
        }
    # M6 turnover sensitivity: monthly vs quarterly for the rep config
    dd["turnover_sensitivity"] = []
    for M in [21, 63, 126]:
        r = run_momentum(mat, ret, universe, N=10, L=252, gap=21, M=M)
        eqm = run_equal_weight(mat, ret, universe, M)
        mmn = metrics(r["net"]); mmg = metrics(r["gross"]); een = metrics(eqm)
        dd["turnover_sensitivity"].append({
            "rebal_days": M, "net_cagr": mmn["cagr"], "gross_cagr": mmg["cagr"],
            "cost_drag_pp": _r((mmg["cagr"]-mmn["cagr"])*100,2) if (mmg["cagr"] is not None and mmn["cagr"] is not None) else None,
            "excess_vs_equalweight_pp": _r((mmn["cagr"]-een["cagr"])*100,2) if (mmn["cagr"] is not None and een["cagr"] is not None) else None,
            "avg_turnover": r["avg_turnover"],
        })
    # momentum-crash windows (known weakness): 2009 rebound, 2020 COVID rebound
    def window_excess(y0, m0, y1, m1):
        common = base["net"].index.intersection(eq_base.index)
        diff = (base["net"].reindex(common) - eq_base.reindex(common))
        mask = (common >= pd.Timestamp(y0, m0, 1, tz="Asia/Seoul")) & (common <= pd.Timestamp(y1, m1, 28, tz="Asia/Seoul"))
        sub = diff[mask]
        return {"days": int(len(sub)), "total_excess_pp": _r(sub.sum()*100,2) if len(sub) else None}
    dd["momentum_crash_windows"] = {
        "2009_rebound": window_excess(2009,3,2009,9),
        "2020_covid_rebound": window_excess(2020,3,2020,9),
        "2008_crisis": window_excess(2008,6,2008,12),
    }
    result["deep_dive"] = dd

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT_JSON)
    # quick console summary
    print("\n== deep dive (N=10,L=252,gap=21,M=21) ==")
    print("momentum net:", dd["momentum_net"])
    print("equalweight :", dd["equalweight"])
    print("kospi b&h   :", dd["kospi"])
    print("excess vs eq (full):", _r((dd['momentum_net']['cagr']-dd['equalweight']['cagr'])*100,2), "pp")
    print("splits:", [s["excess_cagr_pp"] for s in dd["splits"]])
    print("exclude top1:", dd["exclude_top1"]["excess_vs_equalweight_pp"], "pp ; top2:", dd["exclude_top2"]["excess_vs_equalweight_pp"], "pp")
    print("regime excess:", dd["regime_excess"])

if __name__ == "__main__":
    main()
