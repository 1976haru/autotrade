#!/usr/bin/env python3
"""TEMP — 오버나이트 모의 sleeve dry-run (O4): 월요일 아침 매수 후보 *계산* (발사 X).

`app.auto_paper.overnight_sleeve`(계산 전용, broker import 0)로:
  - 전날 미국시장(S&P/나스닥/SOX 평균) + 종목 거래량 → 아침 매수 후보 계산
  - 단타와 분리: trade_reason="overnight_paper"
  - 검증 미통과 라벨 carry
가정(주말): 오버나이트 전용 시드 1,000만원(예시, 운영자가 월요일 비중 결정).
시세/거래량은 cohort 일봉의 as-of(금요일). 월요일엔 실시세로 교체.

출력: reports/backtest/_overnight_dryrun.json
"""
from __future__ import annotations
import json, sys, glob, os
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.auto_paper.overnight_sleeve import (  # noqa: E402
    OvernightConfig, OvernightSignalInputs, build_overnight_plan, plan_to_dict,
)

KR_DIR = Path("data/market/yf_multiyear")
US_DIR = Path("data/market/us_indices")
OUT = Path("reports/backtest/_overnight_dryrun.json")
OVERNIGHT_SEED = 10_000_000


def main():
    # US prev return = mean of latest daily return across S&P/NASDAQ/SOX
    us_rets = []
    for name in ("SP500", "NASDAQ", "SOX"):
        p = US_DIR / f"{name}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, usecols=["timestamp", "close"])
        c = df["close"].astype(float)
        if len(c) >= 2 and c.iloc[-2] > 0:
            us_rets.append(c.iloc[-1] / c.iloc[-2] - 1.0)
    us_prev = sum(us_rets) / len(us_rets) if us_rets else None

    ph, vh, prices = {}, {}, {}
    for f in glob.glob(str(KR_DIR / "*.csv")):
        sym = os.path.basename(f)[:-4]
        if not sym.isdigit():
            continue
        df = pd.read_csv(f, usecols=["timestamp", "close", "volume"])
        c = df["close"].astype(float).dropna()
        v = df["volume"].astype(float).dropna()
        if len(c) < 40 or len(v) < 40:
            continue
        ph[sym] = [float(x) for x in c.values]
        vh[sym] = [float(x) for x in v.values]
        prices[sym] = float(c.iloc[-1])

    sig = OvernightSignalInputs(price_history=ph, volume_history=vh,
                                us_prev_return=us_prev, prices=prices)
    cfg = OvernightConfig(overnight_seed_krw=OVERNIGHT_SEED, max_new_positions=5, exit_mode="close")
    plan = build_overnight_plan(as_of="2026-05-29", signals=sig, cfg=cfg)
    d = plan_to_dict(plan)

    checks = {
        "trade_reason_separate": d["trade_reason"] == "overnight_paper",
        "validation_passed_false": d["validation_passed"] is False,
        "label_present": "검증 미통과" in d["validation_failed_label"],
        "invariants_false": not (d["is_order_signal"] or d["is_live_authorization"]
                                 or d["broker_order_sent"] or d["order_created"]),
        "buy_within_seed": d["total_buy_krw"] <= OVERNIGHT_SEED + 1,
        "all_buy": all(o["side"] == "BUY" for o in d["orders"]),
        "n_orders": len(d["orders"]),
        "signal_active": d["signal_active"],
        "us_prev_return_pct": round((us_prev or 0) * 100, 2),
    }
    out = {"dryrun": d, "checks": checks, "universe": len(ph), "seed": OVERNIGHT_SEED}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print("us_prev_return:", round((us_prev or 0) * 100, 2), "% | signal_active:", d["signal_active"],
          "| n_orders:", len(d["orders"]), "| buy:", d["total_buy_krw"], "| cost:", d["total_est_cost_krw"])
    print("trade_reason:", d["trade_reason"], "| validation_passed:", d["validation_passed"])
    print("label:", d["validation_failed_label"][:50], "...")
    for o in d["orders"]:
        print(f"  BUY {o['symbol']} qty={o['quantity']} @ {o['price']:.0f} = {o['notional']:,.0f}원 (vol {o['volume_ratio']}x)")
    print("notes:", d["notes"])
    bad = [k for k, v in checks.items() if isinstance(v, bool) and not v]
    print("\nALL CHECKS PASS" if not bad else f"\nFAILED: {bad}")


if __name__ == "__main__":
    main()
