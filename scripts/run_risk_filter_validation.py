#!/usr/bin/env python3
"""RISK_FILTER_ONLY OOS 검증 리포트 (CHECKLIST-04 PR, 백테스트 전용, read-only).

  python scripts/run_risk_filter_validation.py [--write-latest]

→ reports/backtest/risk_filter_only_validation_{result,latest}.{json,md}

RISK_FILTER_ONLY 고정 규칙을 전체/OOS/rolling/stress 로 검증. 런타임/전략 자동 적용
0건, 주문 호출 0건. paper rehearsal 도 별도 승인 후에만 검토.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.risk_filter_validation import validate_risk_filter  # noqa: E402

_OUT = Path("reports/backtest")


def _md(r: dict) -> str:
    if not r.get("available"):
        return (f"# RISK_FILTER_ONLY 검증 (연구용)\n\n> {r.get('disclaimer','')}\n\n"
                f"- verdict: {r.get('verdict')} (reason {r.get('reason')})\n")
    ob, of = r["oos"]["baseline"], r["oos"]["risk_filter"]
    L = [f"# RISK_FILTER_ONLY OOS 검증 (연구용) — verdict: {r['verdict']}", "",
         f"> {r['disclaimer']}", "",
         f"- 고정 룰: {r['fixed_rule']}", f"- 데이터: {r['data']}", "",
         "## OOS (earliest baseline vs RISK_FILTER)",
         f"- baseline: PF {ob['profit_factor']} / MDD {ob['mdd_pct']}% / exp {ob['expectancy']}bps / n {ob['trade_count']}",
         f"- filter:   PF {of['profit_factor']} / MDD {of['mdd_pct']}% / exp {of['expectancy']}bps / n {of['trade_count']}",
         f"- delta: {r['vs_earliest_first']}", "",
         "## rolling"]
    for w in r["rolling"]:
        L.append(f"- {w['window']}: base PF {w['baseline_pf']} → filter PF {w['filter_pf']} "
                 f"(≥baseline {w['filter_ge_baseline']})")
    L += ["", "## slippage stress (OOS PF: baseline / filter)"]
    for k, v in r["slippage_stress_oos"].items():
        L.append(f"- {k}: {v['baseline']} / {v['filter']}")
    fo = r["filtered_out_analysis"]
    L += ["", "## 필터 제거 신호 분석",
          f"- 제거 {fo['removed_count']}/{fo['total_trades']} · 제거 net_pnl {fo['removed_net_pnl']} "
          f"(손실 집중 {fo['loss_concentrated_in_removed']})",
          f"- missed winners {fo['missed_winners_count']} (+{fo['missed_winners_pnl']}) / "
          f"avoided losers {fo['avoided_losers_count']} ({fo['avoided_losers_pnl']})",
          "", f"## verdict: {r['verdict']}", f"- 권고: {r['recommendation']}",
          f"- auto_apply_allowed={r['auto_apply_allowed']} · applied_to_runtime={r['applied_to_runtime']}",
          "", "## 다음 단계"]
    L += [f"- {x}" for x in r["next_steps"]]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RISK_FILTER_ONLY validation")
    p.add_argument("--one-min-dir", default="data/market/robust_intraday_1m_subset")
    p.add_argument("--five-min-dir", default="data/market/intraday_ohlcv/kis_6m")
    p.add_argument("--write-latest", action="store_true")
    p.add_argument("--out-dir", default=str(_OUT))
    args = p.parse_args(argv)

    r = validate_risk_filter(one_min_dir=args.one_min_dir, five_min_dir=args.five_min_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "risk_filter_only_validation_result.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "risk_filter_only_validation_result.md").write_text(_md(r), encoding="utf-8")
    if args.write_latest:
        (out / "risk_filter_only_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] verdict={r['verdict']} available={r.get('available')} "
          f"auto_apply={r.get('auto_apply_allowed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
