#!/usr/bin/env python3
"""TEMP — 모멘텀 생존편향 STEP 2: 부분완화 + 정직한 상한/하한.

STEP 0 결과(별도 확인): 완전한 PIT(point-in-time) KOSPI200 구성종목 + 상폐종목
데이터는 **확보 불가**.
  - pykrx 의 KOSPI200 구성종목 조회는 KRX 로그인 자격(KRX_ID/PW) 필요 → null (자격 추가 금지).
  - KRX/yfinance 한국 일봉은 모두 2014-05 이후만 제공 (그 이전 PIT 불가).
  - 상폐종목 시세는 yfinance 가 일부만(예 003450) + 2014부터만 → 표본 부적.
→ "생존편향 완전제거 불가" 로 기록하고 본 STEP 2(부분완화)로 진행.

본 스크립트가 *실제로 계산*하는 부분완화:
  momentum−equalweight 차이(+17pp)는 "살아남은 대형주라서 오른 부분"은 상쇄하지만,
  **"오늘 큰 종목을 universe 로 고른" 사후선택(future-winner selection)** 은 상쇄 못 함.
  이를 *cohort 분리*로 측정한다:
   (1) FULL 120종목 (오늘 기준 대형주 — 사후선택 최대)
   (2) OLD-COHORT (2001-06 이전 상장, 그때 이미 대형주 — 사후 '최근 승자' 선택 제거)
       동일 엔진·동일 lookback·동일 비용, 각 시점 상장여부 PIT(엔진이 자동 배제).
  (1)→(2) premium 하락폭 = 사후선택 편향의 *측정 가능한* 부분.
  잔여(상폐편향)는 측정 불가 → 문헌 기반 보수적 추가 할인으로 하한 제시.

비용 33bps + 회전율, lookahead 0(엔진 기존). 백테스트 전용.
출력: reports/backtest/_surv_step2.json (사람이 MD 작성).
"""
from __future__ import annotations
import json
from pathlib import Path
import importlib.util
import pandas as pd

spec = importlib.util.spec_from_file_location("mv", str(Path(__file__).parent / "_tmp_momentum_verify.py"))
mv = importlib.util.module_from_spec(spec); spec.loader.exec_module(mv)

OUT = Path("reports/backtest/_surv_step2.json")

def cohort_run(mat, ret, universe, N, label):
    r = mv.run_momentum(mat, ret, universe, N=N, L=252, gap=21, M=21)
    eq = mv.run_equal_weight(mat, ret, universe, M=21)
    mm = mv.metrics(r["net"]); ee = mv.metrics(eq)
    kospi = mv.metrics(mv.buy_hold(mat, mv.INDEX_SYM))["cagr"]
    # align common window for fair compare
    common = r["net"].index.intersection(eq.index)
    return {
        "label": label, "n_universe": len(universe), "N_hold": N,
        "period": f"{str(common.min().date())}..{str(common.max().date())}" if len(common) else None,
        "mom_cagr": mm["cagr"], "eq_cagr": ee["cagr"], "kospi_cagr": mv._r(kospi),
        "mom_sharpe": mm["sharpe"], "eq_sharpe": ee["sharpe"], "mom_mdd": mm["mdd"],
        "excess_vs_eq_pp": mv._r((mm["cagr"] - ee["cagr"]) * 100, 2) if (mm["cagr"] is not None and ee["cagr"] is not None) else None,
        "excess_vs_kospi_pp": mv._r((mm["cagr"] - kospi) * 100, 2) if mm["cagr"] is not None else None,
        "avg_turnover": r["avg_turnover"],
    }

def main():
    mat = mv.load_matrix(); ret = mat.pct_change()
    all_eq = [s for s in mat.columns if s.isdigit() and s != mv.ETF_SYM]
    # cohort by first-valid date
    fv = {s: mat[s].first_valid_index() for s in all_eq}
    old_cohort = sorted([s for s in all_eq if fv[s] is not None and fv[s] <= pd.Timestamp("2001-06-30", tz="Asia/Seoul")])
    mid_cohort = sorted([s for s in all_eq if fv[s] is not None and fv[s] <= pd.Timestamp("2006-12-31", tz="Asia/Seoul")])
    print(f"all_eq={len(all_eq)} old_cohort(<=2001-06)={len(old_cohort)} mid_cohort(<=2006)={len(mid_cohort)}")

    res = {"meta": {
        "data": f"{mat.index.min().date()}..{mat.index.max().date()}",
        "n_all": len(all_eq), "n_old_cohort": len(old_cohort), "n_mid_cohort": len(mid_cohort),
        "step0_pit_feasible": False,
        "step0_reason": "KOSPI200 PIT constituents require KRX login (null); KR daily OHLCV only >=2014-05; "
                        "delisted-name coverage spotty (yfinance serves few, also >=2014). Full survivorship "
                        "removal infeasible -> STEP 2 partial mitigation.",
        "method": "cohort split isolates hindsight future-winner-selection bias; engine is PIT on listing "
                  "(stock excluded until it has L+gap history). Residual delisting bias unmeasurable -> literature haircut.",
        "old_cohort_symbols": old_cohort,
    }}
    runs = []
    # full universe, two hold sizes
    runs.append(cohort_run(mat, ret, all_eq, 10, "FULL_120 (today's caps, hindsight-selected)"))
    # old cohort: use N=5 (smaller pool) and N=10
    runs.append(cohort_run(mat, ret, old_cohort, 5, "OLD_COHORT N=5 (listed<=2001, recency-bias removed)"))
    runs.append(cohort_run(mat, ret, old_cohort, 10, "OLD_COHORT N=10"))
    runs.append(cohort_run(mat, ret, mid_cohort, 10, "MID_COHORT N=10 (listed<=2006)"))
    # full universe but restrict to a fair window matching old cohort full period already handled by engine PIT
    res["runs"] = runs

    # also: same-window comparison — run FULL only over old-cohort's common window for apples-to-apples
    # (the FULL run already starts ~2001 because oldest names drive start). Provide split consistency on OLD cohort.
    r_old = mv.run_momentum(mat, ret, old_cohort, N=5, L=252, gap=21, M=21)
    eq_old = mv.run_equal_weight(mat, ret, old_cohort, M=21)
    res["old_cohort_splits"] = mv.split_metrics(r_old["net"], eq_old, 5)
    res["old_cohort_regime"] = mv.regime_excess(r_old["net"], eq_old, mv.regime_labels(mat))

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    for r in runs:
        print(f"  {r['label']:<52} n={r['n_universe']:<3} N={r['N_hold']} "
              f"mom={r['mom_cagr']} eq={r['eq_cagr']} exVsEq={r['excess_vs_eq_pp']}pp "
              f"exVsKOSPI={r['excess_vs_kospi_pp']}pp Shrp {r['mom_sharpe']}/{r['eq_sharpe']} MDD={r['mom_mdd']}")
    print("old cohort splits exVsEq:", [s["excess_cagr_pp"] for s in res["old_cohort_splits"]])
    print("old cohort regime:", {k: v.get("ann_excess_pp") for k, v in res["old_cohort_regime"].items()})

if __name__ == "__main__":
    main()
