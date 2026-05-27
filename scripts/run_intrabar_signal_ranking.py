#!/usr/bin/env python3
"""Intrabar execution + composite signal ranking 리포트 생성 (백테스트 전용).

  python scripts/run_intrabar_signal_ranking.py [--slippage-bps 5] [--write-latest]

→ reports/backtest/intrabar_signal_ranking_result.{json,md}
  (+ intrabar_signal_ranking_latest.json with --write-latest, API 가 읽음)

실 KIS / broker / 주문 호출 0건 — 합성 샘플 기반 연구용 백테스트. EXE 빌드 0건.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.intrabar_signal_ranking import build_report  # noqa: E402

_OUT_DIR = Path("reports/backtest")


def _markdown(r: dict) -> str:
    cov = r["one_minute_coverage"]
    lines = [
        "# Intrabar Execution + Composite Signal Ranking (연구용 백테스트)",
        "",
        f"> {r['disclaimer']}",
        "",
        "## 1. 1분봉 coverage",
        f"- status: **{cov['status']}** {cov.get('warning','')}",
        f"- replayable_trades: {cov['replayable_trades']} / fallback_trades: "
        f"{cov['fallback_trades']} / ambiguous_trades: {cov['ambiguous_trades']}",
        f"- confidence_distribution: {cov['confidence_distribution']}",
        "",
        "## 2. 체결(execution) 비교 — 5분봉 fallback vs 1분봉 replay",
    ]
    for row in r["execution_comparison"]:
        lines.append(
            f"- **{row['name']}**: 5m={row['five_minute']['exit_reason']} "
            f"→ 1m={row['intrabar']['exit_reason']} "
            f"(net_pnl Δ {row['net_pnl_diff']}, source={row['intrabar']['execution_source']})")
    lines += [
        f"- 보수적 stop-first 적용: {r['conservative_stop_first_count']}회",
        "",
        "## 3. signal ranking — earliest-first vs composite",
        f"- earliest_first 선택: {r['earliest_first_selected']}",
        f"- composite 선택: {r['composite_selected']}  (differs={r['ranking_differs']})",
        f"- 선택 평균 score {r['selected_avg_score']} vs 버려진 평균 score "
        f"{r['rejected_avg_score']}  | veto {r['vetoed_count']}",
        "",
        "## 4. 비용 모델",
        f"- {r['cost_model']}",
        "",
        "## 5. 해석",
    ]
    lines += [f"- {x}" for x in r["interpretation"]]
    lines += ["", "## 6. 다음 단계"]
    lines += [f"- {x}" for x in r["next_steps"]]
    lines += ["", f"- is_live_authorization={r['is_live_authorization']} · "
              f"no_profit_guarantee={r['no_profit_guarantee']}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Intrabar + signal ranking report")
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--max-slots", type=int, default=5)
    p.add_argument("--position-count", type=int, default=3)
    p.add_argument("--write-latest", action="store_true")
    p.add_argument("--out-dir", default=str(_OUT_DIR))
    args = p.parse_args(argv)

    r = build_report(slippage_bps=args.slippage_bps, max_slots=args.max_slots,
                     position_count=args.position_count)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "intrabar_signal_ranking_result.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "intrabar_signal_ranking_result.md").write_text(
        _markdown(r), encoding="utf-8")
    if args.write_latest:
        (out_dir / "intrabar_signal_ranking_latest.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] ranking_differs={r['ranking_differs']} "
          f"selected_avg={r['selected_avg_score']} rejected_avg={r['rejected_avg_score']} "
          f"coverage={r['one_minute_coverage']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
