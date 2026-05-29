"""전략 엣지 재설계 연구 CLI (CHECKLIST-05, 백테스트 전용).

실제 1분봉 aligned 데이터로 기존 4전략 실패 원인 + 신규 후보 검증 + exit 구조 비교를
수행하고 reports/backtest/strategy_edge_redesign_*.{json,md} 를 생성한다.

read-only — 실주문 0건, 신규 전략 자동 적용 0건. 결과가 나빠도 그대로 보고한다.

사용:
  PYTHONPATH=backend python scripts/run_strategy_edge_redesign.py --write-latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.system.strategy_edge_redesign import run_strategy_edge_redesign  # noqa: E402

_OUT = _ROOT / "reports" / "backtest"


def _fmt_pf(v):
    return "—" if v is None else f"{v:.3f}"


def _md(r: dict) -> str:
    L = ["# 전략 엣지 재설계 연구 (CHECKLIST-05)", "",
         "> 연구용 백테스트입니다. **실전매매 권고가 아니며**, 신규 후보는 런타임 전략으로 "
         "등록/적용되지 않습니다(auto_apply=false). 수익을 보장하지 않습니다.", ""]
    if not r.get("available"):
        L += [f"**상태**: {r.get('verdict')} — {r.get('reason')}", "",
              f"거래 표본 {r.get('trade_count')} (종목 {r.get('symbols')})."]
        return "\n".join(L)

    L += [f"**최종 verdict**: `{r['verdict']}`", "",
          "## 결론", *[f"- {c}" for c in r.get("conclusion", [])], "",
          f"데이터: 종목 {r['data']['symbols']} / 기존 거래수 {r['data']['existing_trade_counts']} "
          f"/ 후보 거래수 {r['data']['candidate_trade_counts']}", ""]

    L += ["## 1. 기존 전략 실패 원인", "",
          "| 전략 | n | gross PF | net PF | 비용이죽임 | gross엣지없음 | stop먼저 | target도달 | MFE/MAE(bps) | 보유(분) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, f in r["failure_analysis"].items():
        if not f.get("trade_count"):
            L.append(f"| {name} | 0 | — | — | — | — | — | — | — | — |")
            continue
        L.append(f"| {name} | {f['trade_count']} | {_fmt_pf(f['gross_pf'])} | "
                 f"{_fmt_pf(f['net_pf'])} | {f['cost_kills_edge']} | {f['no_gross_edge']} | "
                 f"{f['stop_first_ratio']} | {f['target_hit_ratio']} | "
                 f"{f['avg_mfe_bps']}/{f['avg_mae_bps']} | {f['avg_hold_minutes']} |")
    L.append("")

    # 시간대 (ORB 기준 예시)
    orb = r["failure_analysis"].get("ORB", {})
    if orb.get("by_time_bucket"):
        L += ["### ORB 시간대별", "", "| 시간대 | n | PF | win |", "|---|---|---|---|"]
        for k, v in orb["by_time_bucket"].items():
            L.append(f"| {k} | {v['n']} | {_fmt_pf(v['pf'])} | {v['win_rate']} |")
        L.append("")
        L += ["### ORB regime별 (사후 귀속 — look-ahead, 진입신호 아님)", "",
              "| regime | n | PF | win |", "|---|---|---|---|"]
        for k, v in orb.get("by_regime", {}).items():
            L.append(f"| {k} | {v['n']} | {_fmt_pf(v['pf'])} | {v['win_rate']} |")
        L.append("")

    L += ["## 2~3. 신규 후보 전략 검증", "",
          "| 후보 | n | net PF | cost전 PF | OOS PF | rolling PF(median) | MDD% | win | grade | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for cname, v in r["candidate_results"].items():
        if not v.get("trade_count"):
            L.append(f"| {cname} | 0 | — | — | — | — | — | — | — | {v.get('verdict')} |")
            continue
        L.append(f"| {cname} | {v['trade_count']} | {_fmt_pf(v['profit_factor'])} | "
                 f"{_fmt_pf(v['cost_before_pf'])} | {_fmt_pf(v['oos'].get('oos_pf'))} | "
                 f"{_fmt_pf(v['rolling'].get('oos_pf_median'))} | {v['mdd_pct']} | "
                 f"{v['win_rate']} | {v['grade']} | **{v['verdict']}** |")
    L.append("")

    ntf = r["no_trade_filter"]
    L += ["## F. NO_TRADE_FILTER 효과 (ORB 기준)", "",
          f"- ORB baseline: PF {_fmt_pf(ntf['orb_baseline_pf'])} (n={ntf['orb_baseline_n']})",
          f"- ORB + NO_TRADE_FILTER: PF {_fmt_pf(ntf['orb_filtered_pf'])} "
          f"(n={ntf['orb_filtered_n']}, 제거 {ntf['removed_n']})", ""]

    ex = r["exit_structure_analysis"]
    L += ["## 4. exit 구조 비교 (OOS, 과최적화 금지)", "",
          f"평가 대상: {ex['evaluated_on']} / scope {ex['scope']} / n={ex['n']}", "",
          "| 구조 | PF |", "|---|---|"]
    for k, v in ex["pf_by_structure"].items():
        L.append(f"| {k} | {_fmt_pf(v)} |")
    L.append("")

    L += ["## 살아남은 후보 / 버릴 전략", "",
          f"- 살아남은 후보: {r['survivors'] or '없음'}",
          f"- WATCH: {r['watch'] or '없음'}",
          f"- 버릴(기존, net PF<1): {r['exclude_strategies'] or '없음'}", "",
          "## 다음 단계", *[f"- {s}" for s in r.get("next_steps", [])]]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--one-min-dir")
    ap.add_argument("--five-min-dir")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--write-latest", action="store_true")
    ap.add_argument("--out-dir")
    a = ap.parse_args()

    try:
        r = run_strategy_edge_redesign(
            one_min_dir=Path(a.one_min_dir) if a.one_min_dir else None,
            five_min_dir=Path(a.five_min_dir) if a.five_min_dir else None,
            symbols=a.symbols or None)
    except Exception as e:  # noqa: BLE001
        print(f"[error] {e}")
        return 2

    out = Path(a.out_dir) if a.out_dir else _OUT
    out.mkdir(parents=True, exist_ok=True)
    (out / "strategy_edge_redesign_result.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "strategy_edge_redesign_result.md").write_text(_md(r), encoding="utf-8")
    if a.write_latest:
        (out / "strategy_edge_redesign_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[ok] verdict={r.get('verdict')} available={r.get('available')} -> {out}")
    return 0 if r.get("available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
