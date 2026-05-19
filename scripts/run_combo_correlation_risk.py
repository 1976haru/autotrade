#!/usr/bin/env python3
"""#3-14 CLI: combo correlation / overlap / concentration risk analysis.

signals JSON → 15 combo 위험 분석 → reports/strategy_combo/ 에 3 파일 생성.

본 CLI 는 *분석 전용* — broker / OrderExecutor / route_order import 0건.

사용:
    python scripts/run_combo_correlation_risk.py \\
        --signals-file path/to/signals.json \\
        --symbol 005930 \\
        --output-dir reports/strategy_combo \\
        --min-signals 5

signals.json 형식 (3-12 와 동일):
    [
      {
        "strategy_id":  "sma_crossover",
        "symbol":       "005930",
        "day_key":      "2026-05-19",
        "direction":    "BUY",
        "score":        0.85,
        "realized_pnl": 1200.0
      }, ...
    ]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_signals(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"signals file must be a JSON list, got {type(raw).__name__}")
    return raw


def main(argv: list[str] | None = None) -> int:
    _here = Path(__file__).resolve().parent
    _backend = _here.parent / "backend"
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))

    from app.analytics.combo_correlation_risk import (   # noqa: E402
        RiskCriteria,
        run_combo_risk_analysis,
        write_reports,
    )
    from app.analytics.strategy_combo_backtest import StrategySignal   # noqa: E402

    parser = argparse.ArgumentParser(description="Combo Correlation Risk runner")
    parser.add_argument("--signals-file", type=Path, required=False)
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--output-dir", type=Path,
                        default=_here.parent / "reports" / "strategy_combo")
    parser.add_argument("--min-signals", type=int, default=5)
    parser.add_argument("--pass-overlap", type=float, default=0.20)
    parser.add_argument("--pass-same-dir", type=float, default=0.40)
    parser.add_argument("--pass-conflict", type=float, default=0.10)
    parser.add_argument("--pass-concentration", type=float, default=0.50)
    parser.add_argument("--watch-overlap", type=float, default=0.40)
    parser.add_argument("--watch-same-dir", type=float, default=0.60)
    parser.add_argument("--watch-concentration", type=float, default=0.65)
    parser.add_argument("--block-same-dir", type=float, default=0.85)
    parser.add_argument("--block-conflict", type=float, default=0.40)
    parser.add_argument("--block-concentration", type=float, default=0.85)
    args = parser.parse_args(argv)

    raw = _load_signals(args.signals_file) if args.signals_file else []
    signals: list[StrategySignal] = []
    for r in raw:
        try:
            signals.append(StrategySignal(
                strategy_id=str(r["strategy_id"]),
                symbol=str(r["symbol"]),
                day_key=str(r["day_key"]),
                direction=str(r.get("direction") or "BUY"),
                score=float(r.get("score") or 0.0),
                realized_pnl=float(r.get("realized_pnl") or 0.0),
            ))
        except (KeyError, TypeError, ValueError) as e:
            print(f"[warn] skip invalid signal: {e}; row={r}", file=sys.stderr)

    criteria = RiskCriteria(
        min_signals=int(args.min_signals),
        pass_overlap_ratio=float(args.pass_overlap),
        pass_same_dir_ratio=float(args.pass_same_dir),
        pass_conflict_ratio=float(args.pass_conflict),
        pass_concentration=float(args.pass_concentration),
        watch_overlap_ratio=float(args.watch_overlap),
        watch_same_dir_ratio=float(args.watch_same_dir),
        watch_concentration=float(args.watch_concentration),
        block_same_dir_ratio=float(args.block_same_dir),
        block_conflict_ratio=float(args.block_conflict),
        block_concentration=float(args.block_concentration),
    )

    report = run_combo_risk_analysis(
        signals=signals, symbol=args.symbol, criteria=criteria,
    )
    paths = write_reports(report, args.output_dir)
    print(f"summary_json: {paths['summary_json']}")
    print(f"report_md:    {paths['report_md']}")
    print(f"ranking_csv:  {paths['ranking_csv']}")
    print(f"combos: {len(report.results)}, signals: {len(signals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
