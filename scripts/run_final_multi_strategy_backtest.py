#!/usr/bin/env python3
"""최종 다전략 + Council 백테스트 리포트 (CHECKLIST-04 capstone, read-only).

  python scripts/run_final_multi_strategy_backtest.py [--write-latest]

→ reports/backtest/full_report_{YYYYMMDD,latest}.md + full_report_latest.json

ORB/Momentum/Gap/VWAP 단독 + Council 통합 비교. 자동 적용/런타임 반영 0건, 주문 0건.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.final_multi_strategy_backtest import run_final_multi_strategy_backtest  # noqa: E402

_OUT = Path("reports/backtest")


def _md(r: dict) -> str:
    if not r.get("available"):
        return (f"# 최종 다전략 백테스트 (연구용)\n\n> {r.get('disclaimer','')}\n\n"
                f"- verdict: {r.get('verdict')} (reason {r.get('reason')})\n")
    L = [f"# 최종 다전략 + Council 백테스트 (연구용) — verdict: {r['verdict']}", "",
         f"> {r['disclaimer']}", "",
         f"- 데이터: {r['data']}", f"- 비용모델: {r['cost_model']}", "",
         "## 4전략 단독 (intrabar_1m)",
         "| 전략 | n | PF | MDD% | ret% | win | grade | cost전→후 PF | RF PF |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n, v in r["per_strategy"].items():
        m = v["intrabar_1m"]
        L.append(f"| {n} | {m['trade_count']} | {m['profit_factor']} | {m['mdd_pct']} | "
                 f"{m['return_pct']} | {m['win_rate']} | {v['grade']} | "
                 f"{v['cost_before_pf']}→{v['cost_after_pf']} | "
                 f"{v['intrabar_1m_risk_filter']['profit_factor']} |")
    c = r["council"]
    L += ["", "## slippage stress (전략별 PF)"]
    for n, v in r["per_strategy"].items():
        L.append(f"- {n}: {v['slippage_stress']}")
    L += ["", "## Risk Filter 효과 (전략별)"]
    for n, v in r["per_strategy"].items():
        L.append(f"- {n}: {v['risk_filter']}")
    L += ["", "## Council 통합",
          f"- basic: PF {c['basic']['profit_factor']} / MDD {c['basic']['mdd_pct']}% / n {c['basic']['trade_count']}",
          f"- +RiskFilter: PF {c['with_risk_filter']['profit_factor']}",
          f"- best_single {c['best_single_strategy']}({c['best_single_pf']}) / avg {c['avg_single_pf']}",
          f"- council vs best {c['council_vs_best_single_pf_delta']} / vs avg {c['council_vs_avg_single_pf_delta']}",
          f"- agent helped {c['agent_helped_count']} / hurt {c['agent_hurt_count']}",
          f"- veto {c['risk_veto_count']} / quality_block {c['quality_gate_block_count']} / "
          f"no_exit_plan {c['no_exit_plan_block_count']} / council_buy {c['council_buy_count']}",
          f"- vote dist: {c['strategy_vote_distribution']}",
          "", f"## 전략 등급: {r['strategy_grades']}", "", f"## verdict: {r['verdict']}"]
    L += [f"- {x}" for x in r["conclusion"]]
    L += ["", "## 다음 단계"] + [f"- {x}" for x in r["next_steps"]]
    L += ["", f"- auto_apply_allowed={r['auto_apply_allowed']} · "
          f"applied_to_runtime={r['applied_to_runtime']} · is_live_authorization={r['is_live_authorization']}"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Final multi-strategy + council backtest")
    p.add_argument("--one-min-dir", default="data/market/robust_intraday_1m_subset")
    p.add_argument("--five-min-dir", default="data/market/intraday_ohlcv/kis_6m")
    p.add_argument("--write-latest", action="store_true")
    p.add_argument("--out-dir", default=str(_OUT))
    args = p.parse_args(argv)

    r = run_final_multi_strategy_backtest(one_min_dir=args.one_min_dir,
                                          five_min_dir=args.five_min_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = _md(r)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (out / f"full_report_{stamp}.md").write_text(md, encoding="utf-8")
    (out / "full_report_latest.md").write_text(md, encoding="utf-8")
    if args.write_latest:
        (out / "full_report_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] verdict={r['verdict']} available={r.get('available')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
