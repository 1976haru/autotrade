"""장중 평균회귀 전략 연구 CLI (CHECKLIST-05, 백테스트 전용).

기존 추세추종 실패 재분석(가설) + 평균회귀 후보 6종 검증을 수행하고
reports/backtest/mean_reversion_hypothesis_analysis.{json,md} +
reports/backtest/mean_reversion_strategy_result.{json,md}(+latest) 를 생성한다.

read-only — 실주문 0건, 후보 런타임 자동 등록/적용 0건. 결과가 나빠도 그대로 보고한다.

사용:
  PYTHONPATH=backend python scripts/run_mean_reversion_strategy.py --write-latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.system.mean_reversion_strategy import run_mean_reversion_strategy  # noqa: E402

_OUT = _ROOT / "reports" / "backtest"


def _pf(v):
    return "—" if v is None else f"{v:.3f}"


def _hypothesis_md(h: dict) -> str:
    L = ["# 평균회귀 가설 분석 (CHECKLIST-05)", "",
         "> 연구용 백테스트입니다. 실전매매 권고가 아닙니다.", "",
         "## 추세추종 BUY 이후 forward return (평균회귀 가설 근거)", ""]
    f = h.get("trend_follower_forward_returns", {})
    L += [f"- 표본 n={f.get('n')}",
          f"- 5/10/15/30분 평균 forward: {f.get('mean_h5_bps')} / {f.get('mean_h10_bps')} / "
          f"{f.get('mean_h15_bps')} / {f.get('mean_h30_bps')} bps",
          f"- 반대(평균회귀) 방향 30분: {f.get('reverse_mean_h30_bps')} bps", ""]
    sr = h.get("stop_first_then_revert", {})
    L += [f"- 손절 우선 거래 {sr.get('stop_first_n')}건 중 이후 +50bps 되돌림 "
          f"{sr.get('reverted_n')}건 (revert_ratio {sr.get('revert_ratio')})", "",
          "## 해석", *[f"- {m}" for m in h.get("interpretation", [])], "",
          "## 시간대별 추세추종 forward return (mean h30 bps)", "",
          "| 시간대 | n | mean_h30_bps |", "|---|---|---|"]
    for tb, v in h.get("trend_follower_forward_by_time", {}).items():
        L.append(f"| {tb} | {v.get('n')} | {v.get('mean_h30_bps')} |")
    L += ["", "## regime별 (사후 귀속, look-ahead — 진입신호 아님)", "",
          "| regime | n | mean_h30_bps |", "|---|---|---|"]
    for rg, v in h.get("trend_follower_forward_by_regime", {}).items():
        L.append(f"| {rg} | {v.get('n')} | {v.get('mean_h30_bps')} |")
    return "\n".join(L)


def _md(r: dict) -> str:
    L = ["# 장중 평균회귀 전략 연구 (CHECKLIST-05)", "",
         "> 연구용 백테스트입니다. **실전매매 권고가 아니며**, 후보는 런타임 전략으로 "
         "등록/적용되지 않습니다(research_only=true, auto_apply=false). 수익을 보장하지 않습니다.", ""]
    if not r.get("available"):
        L += [f"**상태**: {r.get('verdict')} — {r.get('reason')}", "",
              f"거래 표본 {r.get('trade_count')} (종목 {r.get('symbols')})."]
        return "\n".join(L)

    L += [f"**최종 verdict**: `{r['verdict']}`", "",
          "## 결론", *[f"- {c}" for c in r.get("conclusion", [])], "",
          f"데이터: 종목 {r['data']['symbols']} / 후보 거래수 {r['data']['candidate_trade_counts']}", ""]

    L += ["## 후보별 결과", "",
          "| 후보 | n | net PF | gross PF | OOS PF | roll(med) | even/odd PF | MDD% | stop/target | cost판정 | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, v in r["candidate_results"].items():
        if not v.get("trade_count"):
            L.append(f"| {c} | 0 | — | — | — | — | — | — | — | {v.get('cost_verdict')} | {v.get('verdict')} |")
            continue
        ss = v["symbol_split"]
        L.append(f"| {c} | {v['trade_count']} | {_pf(v['net_pf'])} | {_pf(v['gross_pf'])} | "
                 f"{_pf(v['oos'].get('oos_pf'))} | {_pf(v['rolling'].get('oos_pf_median'))} | "
                 f"{_pf(ss['even_pf'])}/{_pf(ss['odd_pf'])} | {v['mdd_pct']} | "
                 f"{v['stop_first_ratio']}/{v['target_hit_ratio']} | {v['cost_verdict']} | "
                 f"**{v['verdict']}** |")
    L.append("")

    L += ["## slippage stress (net PF @ 5/7/10/15bps)", "",
          "| 후보 | 5 | 7 | 10 | 15 |", "|---|---|---|---|---|"]
    for c, v in r["candidate_results"].items():
        s = v.get("slippage_stress", {})
        if not v.get("trade_count"):
            continue
        L.append(f"| {c} | {_pf(s.get('5.0bps'))} | {_pf(s.get('7.0bps'))} | "
                 f"{_pf(s.get('10.0bps'))} | {_pf(s.get('15.0bps'))} |")
    L.append("")

    L += ["## 변형(base / +RiskFilter / +trend-filter / +both) net PF", "",
          "| 후보 | base | +RiskFilter | +trend-filter | +both |", "|---|---|---|---|---|"]
    for c, v in r["candidate_results"].items():
        if not v.get("trade_count"):
            continue
        va = v["variants"]
        L.append(f"| {c} | {_pf(va['base_pf'])} | {_pf(va['risk_filter_pf'])} | "
                 f"{_pf(va['no_trade_trend_pf'])} | {_pf(va['both_pf'])} |")
    L.append("")

    L += ["## 살아남은 후보 / 버릴 후보", "",
          f"- 살아남은 후보(CANDIDATE+): {r['survivors'] or '없음'}",
          f"- WATCH: {r['watch'] or '없음'}",
          f"- LOW_CONFIDENCE: {r['low_confidence'] or '없음'}",
          f"- 버릴(REJECT): {r['reject'] or '없음'}", "",
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
        r = run_mean_reversion_strategy(
            one_min_dir=Path(a.one_min_dir) if a.one_min_dir else None,
            five_min_dir=Path(a.five_min_dir) if a.five_min_dir else None,
            symbols=a.symbols or None)
    except Exception as e:  # noqa: BLE001
        print(f"[error] {e}")
        return 2

    out = Path(a.out_dir) if a.out_dir else _OUT
    out.mkdir(parents=True, exist_ok=True)

    def _dump(name, data):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")

    if r.get("available"):
        _dump("mean_reversion_hypothesis_analysis.json", r["hypothesis_analysis"])
        (out / "mean_reversion_hypothesis_analysis.md").write_text(
            _hypothesis_md(r["hypothesis_analysis"]), encoding="utf-8")
    _dump("mean_reversion_strategy_result.json", r)
    (out / "mean_reversion_strategy_result.md").write_text(_md(r), encoding="utf-8")
    if a.write_latest:
        _dump("mean_reversion_strategy_latest.json", r)
    print(f"[ok] verdict={r.get('verdict')} available={r.get('available')} -> {out}")
    return 0 if r.get("available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
