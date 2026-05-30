#!/usr/bin/env python3
"""TEMP — 모멘텀 × 분산 통합 설계 (갈래1: 안전한 자산증식에 모멘텀 tilt).

분산 buy&hold 를 main, 모멘텀(상위10)을 tilt 로 얹어 위험 낮추며 초과수익 일부 유지.
- 데이터: 검증된 cohort(2001 이전 상장, 사후선택 제거) 55종목 중심 + 120종목 보조.
- 두 sleeve(균등분산 / 모멘텀)의 *net 일간수익*을 고정비중 blend (각 leg 이미 33bps 반영).
  sleeve 간 리밸런싱 비용은 소액이라 별도 부과 X (보수적으로 결과를 *낮추지는* 않음 — 명시).
- 위험장치(B3): ① 변동성 타게팅(trailing vol, lookahead 0) ② regime 방어(KOSPI MA200, t-1)
  ③ 최대낙폭 stop(equity peak 대비, t-1).
- B6 보수: 모멘텀 excess 를 하한(+5%p대)으로 *축소*한 conservative 버전 별도 산출.
- lookahead 0(모든 신호 t-1 이하). in-sample 1등 신뢰 금지 — Calmar 곡선/OOS 일관성으로 판정.

출력: reports/backtest/_blend.json (사람이 MD 작성).
백테스트 전용. 실거래/주문/플래그 변경 0.
"""
from __future__ import annotations
import json
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("mv", str(Path(__file__).parent / "_tmp_momentum_verify.py"))
mv = importlib.util.module_from_spec(spec); spec.loader.exec_module(mv)

OUT = Path("reports/backtest/_blend.json")
TD = 252

def _r(x, n=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), n)

def metrics(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {"cagr": None, "vol": None, "sharpe": None, "mdd": None, "calmar": None, "days": int(len(r))}
    eq = (1 + r).cumprod()
    yrs = len(r) / TD
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else None
    vol = r.std() * np.sqrt(TD)
    sharpe = (r.mean() * TD) / vol if vol and vol > 0 else None
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    calmar = (cagr / abs(mdd)) if (cagr is not None and mdd < 0) else None
    return {"cagr": _r(cagr), "vol": _r(vol), "sharpe": _r(sharpe), "mdd": _r(mdd),
            "calmar": _r(calmar), "days": int(len(r))}

def cohort(mat):
    fv = {s: mat[s].first_valid_index() for s in mat.columns if s.isdigit() and s != mv.ETF_SYM}
    old = sorted([s for s, d in fv.items() if d is not None and d <= pd.Timestamp("2001-06-30", tz="Asia/Seoul")])
    return old

def build_sleeves(mat, ret, universe):
    mom = mv.run_momentum(mat, ret, universe, N=10, L=252, gap=21, M=21)["net"]
    eq = mv.run_equal_weight(mat, ret, universe, M=21)
    common = mom.index.intersection(eq.index)
    return eq.reindex(common), mom.reindex(common)

def blend(eq, mom, w_mom):
    return (1 - w_mom) * eq + w_mom * mom

# ----- risk overlays (lookahead-free) -----
def vol_target(r, target_ann=0.15, lookback=60, cap=1.0):
    """scale exposure by target/trailing_vol using returns up to t-1; cash earns 0; leverage<=cap."""
    tv = r.shift(1).rolling(lookback).std() * np.sqrt(TD)
    scale = (target_ann / tv).clip(upper=cap)
    scale = scale.fillna(0.0)
    return r * scale

def regime_defense(eq, mom, idx, base_wmom, reduce_to=0.0):
    """when KOSPI[t-1] < MA200[t-1]: cut momentum weight base->reduce_to; else base."""
    ma = idx.rolling(200).mean()
    bull = (idx > ma).shift(1)  # decision uses t-1
    bull = bull.reindex(eq.index).fillna(False)
    wm = pd.Series(np.where(bull, base_wmom, reduce_to), index=eq.index)
    return (1 - wm) * eq + wm * mom

def maxdd_stop(r, dd_limit=0.25, reenter=0.05):
    """if equity drawdown (through t-1) <= -dd_limit -> cash(0) until dd recovers above -reenter."""
    out = r.copy().astype(float)
    eqv = 1.0; peak = 1.0; risk_on = True
    vals = r.values; idx = r.index
    res = np.zeros(len(r))
    for i in range(len(r)):
        # decide with state through previous bar
        dd = eqv / peak - 1.0
        if risk_on and dd <= -dd_limit:
            risk_on = False
        elif (not risk_on) and dd >= -reenter:
            risk_on = True
        ri = vals[i] if risk_on else 0.0
        res[i] = ri
        eqv *= (1 + (vals[i] if risk_on else 0.0))
        peak = max(peak, eqv)
    return pd.Series(res, index=idx)

def splits(r, base, k=5):
    common = r.index.intersection(base.index)
    rr = r.reindex(common); bb = base.reindex(common); n = len(common)
    out = []
    for j in range(k):
        a = j * n // k; b = (j + 1) * n // k
        mr = metrics(rr.iloc[a:b]); mb = metrics(bb.iloc[a:b])
        out.append({"from": str(common[a].date()), "to": str(common[b-1].date()),
                    "cagr": mr["cagr"], "base_cagr": mb["cagr"],
                    "excess_pp": _r((mr["cagr"]-mb["cagr"])*100, 2) if (mr["cagr"] is not None and mb["cagr"] is not None) else None,
                    "mdd": mr["mdd"]})
    return out

def window(r, base, y0, m0, y1, m1):
    common = r.index.intersection(base.index)
    mask = (common >= pd.Timestamp(y0,m0,1,tz="Asia/Seoul")) & (common <= pd.Timestamp(y1,m1,28,tz="Asia/Seoul"))
    rr = r.reindex(common)[mask]; bb = base.reindex(common)[mask]
    if len(rr) < 5: return {"days": int(len(rr))}
    return {"days": int(len(rr)),
            "blend_total_%": _r(((1+rr).prod()-1)*100,2),
            "base_total_%": _r(((1+bb).prod()-1)*100,2),
            "blend_mdd_%": _r((( (1+rr).cumprod()/(1+rr).cumprod().cummax()-1).min())*100,2)}

def main():
    mat = mv.load_matrix(); ret = mat.pct_change()
    idx = mat[mv.INDEX_SYM].dropna()
    old = cohort(mat)
    all_eq = [s for s in mat.columns if s.isdigit() and s != mv.ETF_SYM]
    print(f"cohort(<=2001-06)={len(old)} | all={len(all_eq)}")

    res = {"meta": {"data": f"{mat.index.min().date()}..{mat.index.max().date()}",
                    "primary_universe": "old_cohort_55 (listed<=2001, hindsight-selection removed)",
                    "n_cohort": len(old), "n_all": len(all_eq),
                    "roundtrip_bps": 33, "momentum": "top10, L252/gap21, monthly",
                    "blend": "fixed-weight combine of equalweight & momentum net daily returns (sleeve rebal cost not added)",
                    "conservative_note": "B6: also report version with momentum excess scaled to +5pp/yr lower bound"}}

    for tag, uni in [("cohort55", old), ("all120", all_eq)]:
        eq, mom = build_sleeves(mat, ret, uni)
        kospi = ret[mv.INDEX_SYM].reindex(eq.index)
        base = eq  # benchmark = pure diversified buy&hold (equalweight)
        block = {"period": f"{str(eq.index.min().date())}..{str(eq.index.max().date())}",
                 "pure_diversified_eq": metrics(eq),
                 "pure_momentum_top10": metrics(mom),
                 "kospi_buyhold": metrics(kospi.dropna())}
        # B2 sweep
        sweep = []
        for wm in [0.0, 0.2, 0.4, 0.5, 1.0]:
            b = blend(eq, mom, wm)
            m = metrics(b)
            sweep.append({"div_pct": int((1-wm)*100), "mom_pct": int(wm*100),
                          "cagr_%": _r(m["cagr"]*100,2) if m["cagr"] is not None else None,
                          "excess_vs_div_pp": _r((m["cagr"]-metrics(eq)["cagr"])*100,2) if m["cagr"] is not None else None,
                          "mdd_%": _r(m["mdd"]*100,2), "sharpe": m["sharpe"], "calmar": m["calmar"]})
        block["B2_sweep"] = sweep
        # B6 conservative: scale momentum excess to +5pp/yr lower bound
        mexc = mom - eq
        mom_full_excess = metrics(mom)["cagr"] - metrics(eq)["cagr"]
        if mom_full_excess and mom_full_excess > 0.05:
            k_cons = 0.05 / mom_full_excess
        else:
            k_cons = 1.0
        mom_cons = eq + k_cons * mexc  # conservative momentum sleeve (same shape, smaller excess)
        block["B6_conservative"] = {"scale_k": _r(k_cons,3), "implied_mom_excess_pp": 5.0, "sweep": []}
        for wm in [0.0, 0.2, 0.4, 0.5, 1.0]:
            b = blend(eq, mom_cons, wm); m = metrics(b)
            block["B6_conservative"]["sweep"].append({
                "div_pct": int((1-wm)*100), "mom_pct": int(wm*100),
                "cagr_%": _r(m["cagr"]*100,2) if m["cagr"] is not None else None,
                "excess_vs_div_pp": _r((m["cagr"]-metrics(eq)["cagr"])*100,2) if m["cagr"] is not None else None,
                "mdd_%": _r(m["mdd"]*100,2), "calmar": m["calmar"]})
        # B3 risk overlays on a representative 60:40 blend (and conservative)
        b6040 = blend(eq, mom, 0.4)
        b6040_cons = blend(eq, mom_cons, 0.4)
        overlays = {"base_60_40": metrics(b6040)}
        overlays["vol_target_15"] = metrics(vol_target(b6040, target_ann=0.15, cap=1.0))
        overlays["vol_target_20"] = metrics(vol_target(b6040, target_ann=0.20, cap=1.0))
        overlays["regime_defense_off"] = metrics(regime_defense(eq, mom, idx, 0.4, reduce_to=0.0))
        overlays["regime_defense_half"] = metrics(regime_defense(eq, mom, idx, 0.4, reduce_to=0.2))
        overlays["maxdd_stop_25"] = metrics(maxdd_stop(b6040, dd_limit=0.25, reenter=0.05))
        overlays["maxdd_stop_35"] = metrics(maxdd_stop(b6040, dd_limit=0.35, reenter=0.10))
        # combined: regime-defended 60:40 + vol target 15
        rd = regime_defense(eq, mom, idx, 0.4, reduce_to=0.0)
        overlays["regime_def_plus_voltarget15"] = metrics(vol_target(rd, target_ann=0.15, cap=1.0))
        block["B3_overlays_on_60_40"] = {k: {kk: v[kk] for kk in ("cagr","mdd","sharpe","calmar","vol")} for k, v in overlays.items()}
        # also conservative overlay headline
        rd_c = regime_defense(eq, mom_cons, idx, 0.4, reduce_to=0.0)
        block["B3_conservative_60_40"] = {
            "plain": {k: metrics(b6040_cons)[k] for k in ("cagr","mdd","calmar")},
            "regime_def": {k: metrics(rd_c)[k] for k in ("cagr","mdd","calmar")},
            "regime_def_voltarget15": {k: metrics(vol_target(rd_c,0.15,cap=1.0))[k] for k in ("cagr","mdd","calmar")},
        }
        # B4 Calmar optimum across finer grid (plain + with best overlay) — conservative basis
        grid = []
        for wm in [0.0,0.1,0.2,0.3,0.4,0.5,0.6,0.8,1.0]:
            b = blend(eq, mom_cons, wm)
            rdb = regime_defense(eq, mom_cons, idx, wm, reduce_to=0.0)
            rdv = vol_target(rdb, 0.15, cap=1.0)
            grid.append({"mom_pct": int(wm*100),
                         "plain_calmar": metrics(b)["calmar"], "plain_mdd": metrics(b)["mdd"], "plain_excess_pp": _r((metrics(b)["cagr"]-metrics(eq)["cagr"])*100,2) if metrics(b)["cagr"] is not None else None,
                         "rd_volt_calmar": metrics(rdv)["calmar"], "rd_volt_mdd": metrics(rdv)["mdd"], "rd_volt_cagr_%": _r(metrics(rdv)["cagr"]*100,2) if metrics(rdv)["cagr"] is not None else None,
                         "rd_volt_excess_pp": _r((metrics(rdv)["cagr"]-metrics(eq)["cagr"])*100,2) if metrics(rdv)["cagr"] is not None else None})
        block["B4_calmar_grid_conservative"] = grid
        # B5 OOS splits + crisis windows for recommended (conservative regime-defended 60:40)
        rec = vol_target(regime_defense(eq, mom_cons, idx, 0.4, reduce_to=0.0), 0.15, cap=1.0)
        block["B5_splits_recommended"] = splits(rec, eq, 5)
        block["B5_crisis"] = {
            "2008_crisis": window(rec, eq, 2008,6,2008,12),
            "2009_rebound": window(rec, eq, 2009,3,2009,9),
            "2020_covid": window(rec, eq, 2020,2,2020,9),
        }
        block["recommended_def"] = "conservative momentum sleeve (+5pp), 60% div / 40% mom, regime-defense(off in bear) + voltarget15%"
        block["recommended_metrics"] = metrics(rec)
        res[tag] = block

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    c = res["cohort55"]
    print("\n== COHORT55 ==")
    print("pure div:", c["pure_diversified_eq"])
    print("pure mom:", c["pure_momentum_top10"])
    print("B2 sweep:")
    for s in c["B2_sweep"]:
        print("  ", s)
    print("B6 conservative scale_k:", c["B6_conservative"]["scale_k"], "sweep:")
    for s in c["B6_conservative"]["sweep"]:
        print("  ", s)
    print("B3 overlays(60:40):")
    for k,v in c["B3_overlays_on_60_40"].items():
        print("  ", k, v)
    print("B3 conservative 60:40:", c["B3_conservative_60_40"])
    print("B4 calmar grid (conservative):")
    for g in c["B4_calmar_grid_conservative"]:
        print("  ", g)
    print("recommended:", c["recommended_def"])
    print("recommended metrics:", c["recommended_metrics"])
    print("B5 splits:", [(s["from"][:7], s["excess_pp"], s["mdd"]) for s in c["B5_splits_recommended"]])
    print("B5 crisis:", c["B5_crisis"])

if __name__ == "__main__":
    main()
