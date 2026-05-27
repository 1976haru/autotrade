#!/usr/bin/env python3
"""Ranking 재설계 비교 리포트 (CHECKLIST-04, 백테스트 전용, read-only).

  python scripts/run_ranking_redesign.py [--write-latest]

→ reports/backtest/ranking_redesign_{result,latest}.{json,md}
  + ranking_redesign_diagnosis.{json,md}

9 고정 ranking 후보를 60일 1분봉 aligned 데이터로 비교 + OOS + attribution.
어떤 후보도 런타임/전략/ranking weight 에 자동 적용하지 않는다. 주문 호출 0건.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.ranking_redesign import run_ranking_redesign  # noqa: E402

_OUT = Path("reports/backtest")


def _md(r: dict) -> str:
    if not r.get("available"):
        return (f"# Ranking 재설계 (연구용)\n\n> {r.get('disclaimer','')}\n\n"
                f"- verdict: {r.get('verdict')} (reason: {r.get('reason')})\n")
    L = [f"# Ranking 재설계 비교 (연구용) — verdict: {r['verdict']}", "",
         f"> {r['disclaimer']}", "",
         f"⚠️ look-ahead 경고: {r['look_ahead_warning']}", "",
         f"- 데이터: {r['data']}", "",
         "## 기존 composite 실패 재현",
         f"- {r['current_composite_failure']}", "",
         "## OOS 후보별 (PF / MDD / expectancy / n / 종목수)"]
    for n in r["candidates"]:
        m = r["oos"][n]
        la = " [look-ahead]" if n in r["look_ahead_candidates"] else ""
        L.append(f"- {n}{la}: PF {m['profit_factor']} / MDD {m['mdd_pct']} / "
                 f"exp {m['expectancy']} / n {m['trade_count']} / syms {len(m['selected_symbols'])}")
    L += ["", "## feature attribution (corr vs net_pnl / win)"]
    for f, v in r["feature_attribution"].items():
        L.append(f"- {f}: net_pnl {v['corr_vs_net_pnl']} / win {v['corr_vs_win']}")
    L += ["", "## slippage stress (OOS PF)"]
    for n in r["candidates"]:
        L.append(f"- {n}: {r['slippage_stress_oos'][n]}")
    L += ["", f"## 최종 verdict: {r['verdict']}", f"- best: {r['best_candidate']['name']}",
          f"- 권고: {r['recommendation']}", "",
          f"- auto_apply_allowed={r['auto_apply_allowed']} · "
          f"applied_to_runtime={r['applied_to_runtime']}", "", "## 다음 단계"]
    L += [f"- {x}" for x in r["next_steps"]]
    return "\n".join(L) + "\n"


def _diagnosis(r: dict) -> dict:
    if not r.get("available"):
        return {"available": False}
    return {
        "current_composite_failure": r["current_composite_failure"],
        "feature_attribution": r["feature_attribution"],
        "look_ahead_candidates": r["look_ahead_candidates"],
        "look_ahead_warning": r["look_ahead_warning"],
        "selected_vs_rejected_note": "filter 가 reorder 보다 우위 — 점수 재배열 무가치",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ranking redesign comparison")
    p.add_argument("--one-min-dir", default="data/market/robust_intraday_1m_subset")
    p.add_argument("--five-min-dir", default="data/market/intraday_ohlcv/kis_6m")
    p.add_argument("--write-latest", action="store_true")
    p.add_argument("--out-dir", default=str(_OUT))
    args = p.parse_args(argv)

    r = run_ranking_redesign(one_min_dir=args.one_min_dir, five_min_dir=args.five_min_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ranking_redesign_result.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "ranking_redesign_result.md").write_text(_md(r), encoding="utf-8")
    (out / "ranking_redesign_diagnosis.json").write_text(
        json.dumps(_diagnosis(r), ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_latest:
        (out / "ranking_redesign_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] verdict={r['verdict']} available={r.get('available')} "
          f"best={r.get('best_candidate',{}).get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
