"""종목군 × 시장국면 다변화 백테스트 CLI (CHECKLIST-05, 백테스트 전용).

5개 종목군 × 5개 시장국면으로 4전략/Council/Risk Filter/평균회귀를 재검증하고
reports/backtest/universe_regime_backtest_result.{json,md}(+latest) +
universe_diversification_manifest.{json,md} + market_regime_diversification_manifest.{json,md}
를 생성한다. read-only — 실주문 0건, 자동 적용 0건. 결과가 나빠도 그대로 보고.

사용:
  PYTHONPATH=backend python scripts/run_universe_regime_backtest.py --write-latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.system.universe_regime_backtest import run_universe_regime_backtest  # noqa: E402

_OUT = _ROOT / "reports" / "backtest"


def _pf(v):
    return "—" if v is None else f"{v:.3f}"


def _manifest_md(u: dict, r: dict) -> tuple[str, str]:
    ul = ["# Universe 다변화 manifest (CHECKLIST-05)", "",
          "> research-only — 데이터 가용성은 수집 CSV 존재로 판정(KIS 라이브 호출 0건).", "",
          f"총 {u['total_symbols']}종목 중 수집 {u['total_present']}종목. proxy={u['market_proxy_symbol']}", "",
          "| 그룹 | 가용/전체 | 충분 | 가용 종목 |", "|---|---|---|---|"]
    for g, gr in u["groups"].items():
        ul.append(f"| {g} | {gr['available_count']}/{gr['available_count']+gr['missing_count']} | "
                  f"{gr['data_sufficient']} | {', '.join(gr['available_symbols']) or '—'} |")
    rl = ["# 시장국면 manifest (CHECKLIST-05)", "",
          f"> 사후 attribution 전용(look-ahead={r.get('regime_is_lookahead')}, 진입 신호 미사용). "
          f"proxy={r.get('proxy_symbol')}", "",
          f"거래일 {r.get('trading_days')}", "", "| 국면 | 일수 |", "|---|---|"]
    for rg, n in (r.get("regime_day_counts") or {}).items():
        rl.append(f"| {rg} | {n} |")
    return "\n".join(ul), "\n".join(rl)


def _md(r: dict) -> str:
    L = ["# 종목군 × 시장국면 다변화 백테스트 (CHECKLIST-05)", "",
         "> 연구용 백테스트이며 **실전매매 권고가 아닙니다.** regime 라벨은 사후 attribution "
         "전용(진입 신호 미사용). 어떤 전략/종목군도 런타임에 자동 적용되지 않습니다"
         "(research_only, auto_apply=false). 수익을 보장하지 않습니다.", ""]
    if not r.get("available"):
        L += [f"**상태**: {r.get('verdict')} — {r.get('reason')}", "",
              f"수집 종목 {r.get('symbol_count')}."]
        return "\n".join(L)

    L += [f"**최종 verdict**: `{r['verdict']}`", "",
          "## 결론", *[f"- {c}" for c in r.get("conclusion", [])], "",
          "## 그룹별 4전략 net PF (+ Council)", "",
          "| 그룹 | n종목 | ORB | MOMENTUM | GAP | VWAP | Council | Council+RF | best |",
          "|---|---|---|---|---|---|---|---|---|"]
    for g, gr in r["group_results"].items():
        if not gr.get("present"):
            L.append(f"| {g} | 0 | — | — | — | — | — | — | (데이터 없음) |")
            continue
        fs = gr["four_strategy"]
        L.append(f"| {g} | {gr['present']} | {_pf(fs['ORB'].get('net_pf'))} | "
                 f"{_pf(fs['MOMENTUM'].get('net_pf'))} | {_pf(fs['GAP'].get('net_pf'))} | "
                 f"{_pf(fs['VWAP'].get('net_pf'))} | {_pf(gr['council'].get('net_pf'))} | "
                 f"{_pf(gr.get('council_risk_filter_pf'))} | "
                 f"{gr.get('best_single_strategy')}({_pf(gr.get('best_single_pf'))}) |")
    L.append("")

    L += ["## 그룹별 평균회귀 후보 net PF", "",
          "| 그룹 | " + " | ".join(list(__import__('app.research.mean_reversion_candidates',
                                                     fromlist=['CANDIDATES']).CANDIDATES)) + " |",
          "|" + "---|" * 7]
    from app.research.mean_reversion_candidates import CANDIDATES as MC
    for g, gr in r["group_results"].items():
        if not gr.get("present"):
            continue
        cells = [_pf((gr["mean_reversion"].get(k, {}) or {}).get("net_pf")) for k in MC]
        L.append(f"| {g} | " + " | ".join(cells) + " |")
    L.append("")

    L += ["## 국면별 4전략 net PF (+ Council, 사후 attribution)", "",
          "| 국면 | ORB | MOMENTUM | GAP | VWAP | Council |", "|---|---|---|---|---|---|"]
    for rg, blk in r["regime_results"].items():
        L.append(f"| {rg} | {_pf(blk.get('ORB',{}).get('net_pf'))} | "
                 f"{_pf(blk.get('MOMENTUM',{}).get('net_pf'))} | {_pf(blk.get('GAP',{}).get('net_pf'))} | "
                 f"{_pf(blk.get('VWAP',{}).get('net_pf'))} | {_pf(blk.get('COUNCIL',{}).get('net_pf'))} |")
    L.append("")

    L += ["## 핵심 질문 요약", "", *[f"- {k}: {v}" for k, v in r.get("key_questions", {}).items()], "",
          "## 살아남은 / 실패한", "",
          f"- 살아남은(PF≥1) 그룹×전략: {r['survivors'] or '없음'}",
          f"- 실패한 그룹(전 전략 PF<1): {r['failures'] or '없음'}", "",
          "## 다음 단계", *[f"- {s}" for s in r.get("next_steps", [])]]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dirs", nargs="*")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--no-council", action="store_true")
    ap.add_argument("--write-latest", action="store_true")
    ap.add_argument("--out-dir")
    a = ap.parse_args()

    try:
        r = run_universe_regime_backtest(
            data_dirs=[Path(d) for d in a.data_dirs] if a.data_dirs else None,
            symbols=a.symbols or None, run_council=not a.no_council)
    except Exception as e:  # noqa: BLE001
        print(f"[error] {e}")
        return 2

    out = Path(a.out_dir) if a.out_dir else _OUT
    out.mkdir(parents=True, exist_ok=True)

    def _dump(name, data):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")

    um, rm = r.get("universe_manifest", {}), r.get("regime_manifest", {})
    _dump("universe_diversification_manifest.json", um)
    _dump("market_regime_diversification_manifest.json", rm)
    umd, rmd = _manifest_md(um, rm)
    (out / "universe_diversification_manifest.md").write_text(umd, encoding="utf-8")
    (out / "market_regime_diversification_manifest.md").write_text(rmd, encoding="utf-8")
    _dump("universe_regime_backtest_result.json", r)
    (out / "universe_regime_backtest_result.md").write_text(_md(r), encoding="utf-8")
    if a.write_latest:
        _dump("universe_regime_backtest_latest.json", r)
    print(f"[ok] verdict={r.get('verdict')} available={r.get('available')} -> {out}")
    return 0 if r.get("available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
