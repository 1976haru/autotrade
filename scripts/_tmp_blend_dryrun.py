#!/usr/bin/env python3
"""TEMP — 추천 blend 설계 dry-run: 월요일 *주문 리스트 계산* (발사 안 함).

PART A2/A3: `app.portfolio.blend_allocation`(순수 계산 모듈)을 사용해
  - as-of 시세(검증된 cohort 데이터)로 목표 비중 + 리밸런싱 주문 리스트 계산
  - 검증: 비중 합 ≤ 1, 음수 0, BUY notional ≤ 자본, 비용 반영
  - **broker / route_order / place_order 절대 호출 안 함** (모듈 자체가 import 0건)

가정(주말 dry-run): 시작 전액 현금 1,000만원, 현재 보유 0 → 전부 BUY 후보.
시세는 cohort 55종목의 최근 종가(as-of). 실제 월요일엔 KIS Paper 현재가로 갱신 필요.

출력: reports/backtest/_blend_dryrun.json
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import importlib.util

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.portfolio.blend_allocation import (  # noqa: E402
    AllocationConfig, SignalInputs, build_target_allocation, build_rebalance_plan, plan_to_dict,
)

# reuse the verified loader for cohort universe + closes
spec = importlib.util.spec_from_file_location("mv", str(Path(__file__).parent / "_tmp_momentum_verify.py"))
mv = importlib.util.module_from_spec(spec); spec.loader.exec_module(mv)

OUT = Path("reports/backtest/_blend_dryrun.json")
EQUITY = 10_000_000


def main():
    mat = mv.load_matrix()
    # cohort: listed <= 2001-06 (survivorship-clean primary universe)
    import pandas as pd
    fv = {s: mat[s].first_valid_index() for s in mat.columns if s.isdigit() and s != mv.ETF_SYM}
    cohort = sorted([s for s, d in fv.items() if d is not None and d <= pd.Timestamp("2001-06-30", tz="Asia/Seoul")])
    as_of = str(mat.index.max().date())

    # build price history (lists, oldest..as-of) for cohort + index
    price_history = {}
    prices = {}
    for s in cohort:
        ser = mat[s].dropna()
        if len(ser) < 300:
            continue
        price_history[s] = [float(x) for x in ser.values]
        prices[s] = float(ser.iloc[-1])
    index_series = mat[mv.INDEX_SYM].dropna()
    index_history = [float(x) for x in index_series.values]

    sig = SignalInputs(price_history=price_history, index_history=index_history)
    cfg = AllocationConfig()  # recommended defaults: 70/30, top10, voltarget15, regime defense
    target = build_target_allocation(list(price_history.keys()), sig, cfg)
    plan = build_rebalance_plan(
        as_of=as_of, total_equity_krw=EQUITY,
        current_holdings={},   # start all cash (weekend dry-run)
        prices=prices, target=target,
    )
    d = plan_to_dict(plan)

    # ---- validation checks (A3) ----
    checks = {}
    wsum = sum(target.weights.values()) + target.cash_weight
    checks["weights_plus_cash_le_1"] = wsum <= 1.0 + 1e-6
    checks["no_negative_weight"] = all(w >= 0 for w in target.weights.values())
    checks["buy_notional_le_equity"] = plan.total_buy_krw <= EQUITY + 1
    checks["all_quantities_positive_int"] = all(isinstance(o.quantity, int) and o.quantity > 0 for o in plan.orders)
    checks["cost_nonneg"] = plan.total_est_cost_krw >= 0
    checks["invariants_false"] = not (plan.is_order_signal or plan.is_live_authorization
                                      or plan.broker_order_sent or plan.order_created)
    checks["n_orders"] = len(plan.orders)
    checks["regime_is_bull"] = target.regime_is_bull
    checks["vol_scale"] = round(target.vol_scale, 3)
    checks["gross_exposure"] = round(target.gross_exposure, 3)
    checks["cash_weight"] = round(target.cash_weight, 3)

    out = {"dryrun": d, "checks": checks, "universe_size": len(price_history), "equity_krw": EQUITY}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print("as_of:", as_of, "| universe:", len(price_history), "| bull:", target.regime_is_bull,
          "| vol_scale:", round(target.vol_scale, 3), "| gross:", round(target.gross_exposure, 3))
    print("orders:", len(plan.orders), "| buy:", plan.total_buy_krw, "| sell:", plan.total_sell_krw,
          "| est_cost:", plan.total_est_cost_krw)
    print("momentum picks:", target.momentum_picks)
    print("notes:", target.notes)
    print("CHECKS:")
    for k, v in checks.items():
        print(f"  {k}: {v}")
    print("\nTOP 12 ORDER LIST (preview):")
    for o in sorted(plan.orders, key=lambda x: x.notional, reverse=True)[:12]:
        tag = "[MOM]" if o.symbol in target.momentum_picks else "[DIV]"
        print(f"  {o.side} {o.symbol} {tag} qty={o.quantity} @ {o.price:.0f} = {o.notional:,.0f}원 (cost ~{o.est_cost_krw:.0f})")
    # all checks pass?
    bad = [k for k, v in checks.items() if isinstance(v, bool) and not v]
    print("\nALL CHECKS PASS" if not bad else f"\nFAILED CHECKS: {bad}")


if __name__ == "__main__":
    main()
