#!/usr/bin/env python3
"""#3-12 CLI: strategy combo backtest runner.

JSON 파일로부터 signals 를 읽어 15개 조합 backtest 실행 → reports/strategy_combo/
에 JSON / Markdown / CSV 3 파일 생성.

**실거래 호출 0건** — 본 CLI 는 분석 전용. broker / OrderExecutor /
route_order 어떤 것도 import 하지 않는다.

사용:

    python scripts/run_strategy_combo_backtest.py \\
        --signals-file path/to/signals.json \\
        --symbol 005930 \\
        --output-dir reports/strategy_combo \\
        --min-trades 10

signals.json 형식 (list of dicts):

    [
      {
        "strategy_id":  "sma_crossover",
        "symbol":       "005930",
        "day_key":      "2026-05-19",
        "direction":    "BUY",
        "score":        0.85,
        "realized_pnl": 1200.0
      },
      ...
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
    # Ensure repo's backend/ is importable.
    _here = Path(__file__).resolve().parent
    _backend = _here.parent / "backend"
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))

    from app.analytics.strategy_combo_backtest import (   # noqa: E402
        ComboCriteria,
        StrategySignal,
        run_combo_backtest,
        write_reports,
    )

    parser = argparse.ArgumentParser(description="Strategy Combo Backtest runner")
    parser.add_argument("--signals-file", type=Path, required=False,
                        help="signals JSON (omit → run with empty input).")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--output-dir", type=Path,
                        default=_here.parent / "reports" / "strategy_combo")
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--pass-pf", type=float, default=1.2)
    parser.add_argument("--fail-pf", type=float, default=1.0)
    parser.add_argument("--pass-mdd", type=float, default=0.20)
    parser.add_argument("--fail-mdd", type=float, default=0.30)
    parser.add_argument("--pass-conflict", type=float, default=0.30)
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

    criteria = ComboCriteria(
        min_trades=int(args.min_trades),
        pass_profit_factor=float(args.pass_pf),
        fail_profit_factor=float(args.fail_pf),
        pass_max_drawdown_abs=float(args.pass_mdd),
        fail_max_drawdown_abs=float(args.fail_mdd),
        pass_conflict_ratio=float(args.pass_conflict),
    )

    report = run_combo_backtest(
        signals=signals,
        symbol=args.symbol,
        criteria=criteria,
    )
    paths = write_reports(report, args.output_dir)
    print(f"summary_json: {paths['summary_json']}")
    print(f"report_md:    {paths['report_md']}")
    print(f"ranking_csv:  {paths['ranking_csv']}")
    print(f"combos: {len(report.results)}, signals: {len(signals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
