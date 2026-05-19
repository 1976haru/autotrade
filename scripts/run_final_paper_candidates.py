#!/usr/bin/env python3
"""#3-15 CLI: final Paper combo candidate selector.

JSON 입력 (CandidateInput list) → 최종 후보 1~3개 선정 → reports/final_paper/
에 JSON/MD/CSV 3 파일 생성.

본 CLI 는 *분석 전용* — broker / OrderExecutor / route_order import 0건.

사용:
    python scripts/run_final_paper_candidates.py \\
        --inputs-file path/to/candidate_inputs.json \\
        --output-dir reports/final_paper \\
        --period "2026-05" \\
        --min-trades 10 \\
        --max-candidates 3

inputs.json 형식 (CandidateInput list):
    [
      {
        "name": "MOMENTUM+VWAP",
        "included_tactics": ["MOMENTUM", "VWAP"],
        "included_strategies": ["sma_crossover", "volume_breakout", "vwap_strategy"],
        "symbol": "005930",
        "primary_regime": "TREND_UP",
        "params": {"fast": 5, "slow": 20},
        "trade_count": 25,
        "expectancy": 250.0,
        "profit_factor": 1.5,
        "max_drawdown": 0.12,
        "win_rate": 0.55,
        "loss_streak": 3,
        "total_return": 6200.0,
        "paper_candidate_status": "READY_FOR_PAPER",
        "walk_forward_verdict": "HEALTHY",
        "stress_verdict": "PASS",
        "combo_verdict": "PASS",
        "regime_combo_verdict": "PASS",
        "combo_risk_verdict": "PASS",
        "confirmation_score": 3,
        "correlation_score": 0.3,
        "concentration_score": 0.4
      },
      ...
    ]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_inputs(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"inputs file must be a JSON list, got {type(raw).__name__}")
    return raw


def main(argv: list[str] | None = None) -> int:
    _here = Path(__file__).resolve().parent
    _backend = _here.parent / "backend"
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))

    from app.analytics.final_paper_candidates import (   # noqa: E402
        CandidateInput,
        FinalCandidateCriteria,
        select_paper_candidates,
        write_reports,
    )

    parser = argparse.ArgumentParser(description="Final Paper Candidate Selector")
    parser.add_argument("--inputs-file", type=Path, required=False)
    parser.add_argument("--output-dir", type=Path,
                        default=_here.parent / "reports" / "final_paper")
    parser.add_argument("--period", type=str, default="ad-hoc")
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--min-profit-factor", type=float, default=1.2)
    parser.add_argument("--max-drawdown-abs", type=float, default=0.20)
    parser.add_argument("--min-win-rate", type=float, default=0.0)
    parser.add_argument("--max-loss-streak", type=int, default=10)
    parser.add_argument("--max-candidates", type=int, default=3)
    args = parser.parse_args(argv)

    raw = _load_inputs(args.inputs_file) if args.inputs_file else []
    inputs: list[CandidateInput] = []
    for r in raw:
        try:
            inputs.append(CandidateInput(
                name=str(r["name"]),
                included_tactics=tuple(r.get("included_tactics") or ()),
                included_strategies=tuple(r.get("included_strategies") or ()),
                symbol=str(r.get("symbol") or "UNKNOWN"),
                params=dict(r.get("params") or {}),
                primary_regime=str(r.get("primary_regime") or "UNKNOWN"),
                trade_count=int(r.get("trade_count") or 0),
                expectancy=(
                    float(r["expectancy"])
                    if r.get("expectancy") is not None else None
                ),
                profit_factor=(
                    float(r["profit_factor"])
                    if r.get("profit_factor") is not None else None
                ),
                max_drawdown=(
                    float(r["max_drawdown"])
                    if r.get("max_drawdown") is not None else None
                ),
                win_rate=(
                    float(r["win_rate"])
                    if r.get("win_rate") is not None else None
                ),
                loss_streak=int(r.get("loss_streak") or 0),
                total_return=float(r.get("total_return") or 0.0),
                paper_candidate_status=str(
                    r.get("paper_candidate_status") or "INSUFFICIENT_DATA"
                ),
                walk_forward_verdict=str(
                    r.get("walk_forward_verdict") or "INSUFFICIENT_DATA"
                ),
                stress_verdict=str(r.get("stress_verdict") or "INSUFFICIENT_DATA"),
                combo_verdict=str(r.get("combo_verdict") or "INSUFFICIENT_DATA"),
                regime_combo_verdict=str(
                    r.get("regime_combo_verdict") or "INSUFFICIENT_DATA"
                ),
                combo_risk_verdict=str(
                    r.get("combo_risk_verdict") or "INSUFFICIENT_DATA"
                ),
                confirmation_score=int(r.get("confirmation_score") or 0),
                correlation_score=float(r.get("correlation_score") or 0.0),
                concentration_score=float(r.get("concentration_score") or 0.0),
            ))
        except (KeyError, TypeError, ValueError) as e:
            print(f"[warn] skip invalid input: {e}; row={r}", file=sys.stderr)

    criteria = FinalCandidateCriteria(
        min_trades=int(args.min_trades),
        min_profit_factor=float(args.min_profit_factor),
        max_drawdown_abs=float(args.max_drawdown_abs),
        min_win_rate=float(args.min_win_rate),
        max_loss_streak=int(args.max_loss_streak),
        max_candidates=int(args.max_candidates),
    )

    report = select_paper_candidates(
        inputs=inputs, criteria=criteria, period_label=args.period,
    )
    paths = write_reports(report, args.output_dir)
    print(f"summary_json: {paths['summary_json']}")
    print(f"report_md:    {paths['report_md']}")
    print(f"ranking_csv:  {paths['ranking_csv']}")
    print(f"status: {report.status.value}, "
          f"candidates: {len(report.candidates)}, "
          f"excluded: {len(report.excluded)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
