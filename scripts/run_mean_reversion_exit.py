"""평균회귀 exit 구조 연구 CLI (CHECKLIST-05, 백테스트 전용).

평균회귀 entry 후보 6종 × 사전 정의 exit 후보 10종 matrix 를 실데이터로 비교하고
reports/backtest/mean_reversion_exit_result.{json,md}(+latest) 를 생성한다.

read-only — 실주문 0건, entry/exit 런타임 자동 등록/적용 0건. 결과가 나빠도 그대로 보고.

사용:
  PYTHONPATH=backend python scripts/run_mean_reversion_exit.py --write-latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.system.mean_reversion_exit import run_mean_reversion_exit  # noqa: E402

_OUT = _ROOT / "reports" / "backtest"


def _pf(v):
    return "—" if v is None else f"{v:.3f}"


def _md(r: dict) -> str:
    L = ["# 평균회귀 exit 구조 연구 (CHECKLIST-05)", "",
         "> 연구용 백테스트입니다. **실전매매 권고가 아니며**, 결과가 좋아도 paper rehearsal "
         "후보일 뿐입니다. entry/exit 구조는 런타임에 자동 적용되지 않습니다(research_only, "
         "auto_apply=false). 수익을 보장하지 않습니다.", ""]
    if not r.get("available"):
        L += [f"**상태**: {r.get('verdict')} — {r.get('reason')}", "",
              f"거래 표본 {r.get('trade_count')} (종목 {r.get('symbols')})."]
        return "\n".join(L)

    L += [f"**최종 verdict**: `{r['verdict']}`", "",
          "## 결론", *[f"- {c}" for c in r.get("conclusion", [])], "",
          "## exit mismatch 근거",
          "- 평균회귀 후보 6종은 비용 전에도 PF<1, target_hit 0.04~0.14(연속형 +1.5% target 과 미스매치).",
          "- 되돌림은 잦으나(stop 후 51%) 고정 −1% stop 이 되돌림 전에 청산 → exit 구조 재설계 비교.", "",
          f"데이터: 종목 {r['data']['symbols']} / entry 이벤트 {r['data']['entry_event_counts']}", "",
          f"exit 후보: {', '.join(r['exit_plans'])}", ""]

    # entry × exit net PF matrix
    L += ["## entry × exit net PF matrix", "",
          "| entry \\ exit | " + " | ".join(r["exit_plans"]) + " |",
          "|" + "---|" * (len(r["exit_plans"]) + 1)]
    for entry, row in r["matrix"].items():
        cells = [_pf(row.get(p, {}).get("net_pf")) for p in r["exit_plans"]]
        L.append(f"| {entry} | " + " | ".join(cells) + " |")
    L.append("")

    # OOS PF matrix
    L += ["## entry × exit OOS PF matrix", "",
          "| entry \\ exit | " + " | ".join(r["exit_plans"]) + " |",
          "|" + "---|" * (len(r["exit_plans"]) + 1)]
    for entry, row in r["matrix"].items():
        cells = [_pf(row.get(p, {}).get("oos_pf")) for p in r["exit_plans"]]
        L.append(f"| {entry} | " + " | ".join(cells) + " |")
    L.append("")

    L += ["## entry 별 best exit", "", "| entry | best exit | net PF | OOS PF | verdict |",
          "|---|---|---|---|---|"]
    for e, b in r["best_exit_per_entry"].items():
        L.append(f"| {e} | {b['exit']} | {_pf(b['net_pf'])} | {_pf(b['oos_pf'])} | {b['verdict']} |")
    L.append("")

    # 살아남은 조합 detail (target/stop/time + 10bps stress)
    L += ["## 살아남은(net PF≥1) 조합 detail", "",
          "| 조합 | n | net PF | OOS | 10bps | MDD% | tgt/stop/time | cost판정 | verdict |",
          "|---|---|---|---|---|---|---|---|---|"]
    found = False
    for entry, row in r["matrix"].items():
        for p, v in row.items():
            if (v.get("net_pf") or 0) >= 1.0:
                found = True
                s10 = (v.get("slippage_stress") or {}).get("10.0bps")
                L.append(f"| {entry}+{p} | {v['trade_count']} | {_pf(v['net_pf'])} | "
                         f"{_pf(v['oos_pf'])} | {_pf(s10)} | {v['mdd_pct']} | "
                         f"{v['target_hit_ratio']}/{v['stop_first_ratio']}/{v['time_exit_ratio']} | "
                         f"{v['cost_verdict']} | {v['verdict']} |")
    if not found:
        L.append("| (없음 — 모든 조합 net PF<1) | | | | | | | | |")
    L += ["", f"- 살아남은 조합: {r['survivors'] or '없음'}",
          f"- COST_FRAGILE: {r['fragile'] or '없음'}", "",
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
        r = run_mean_reversion_exit(
            one_min_dir=Path(a.one_min_dir) if a.one_min_dir else None,
            five_min_dir=Path(a.five_min_dir) if a.five_min_dir else None,
            symbols=a.symbols or None)
    except Exception as e:  # noqa: BLE001
        print(f"[error] {e}")
        return 2

    out = Path(a.out_dir) if a.out_dir else _OUT
    out.mkdir(parents=True, exist_ok=True)
    (out / "mean_reversion_exit_result.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "mean_reversion_exit_result.md").write_text(_md(r), encoding="utf-8")
    if a.write_latest:
        (out / "mean_reversion_exit_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[ok] verdict={r.get('verdict')} available={r.get('available')} -> {out}")
    return 0 if r.get("available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
