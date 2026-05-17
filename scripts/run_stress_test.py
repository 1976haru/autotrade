#!/usr/bin/env python3
"""3-05 — Stress test CLI.

입력 모드:
1. ``--from-walk-forward <PATH>`` — 3-04 walk_forward_summary.json (HEALTHY 만).
2. ``--strategy --symbol``        — 단일 (strategy, symbol).
3. 인자 생략                       — 대표 10종 × 6 전략 default 매트릭스.

10 시나리오 (default 전체) × 후보 매트릭스 → StressResult 산출.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 실거래 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- 안전 flag default 변경 0건.
- secret / API key / 계좌번호 / `.env` 노출 0건.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


from app.analytics.stress_test import (  # noqa: E402
    StressCandidateInput,
    StressScenario,
    StressTestConfig,
    StressVerdict,
    evaluate_stress,
    read_candidates_from_walk_forward,
)
from app.backtest.real_data import REPRESENTATIVE_SYMBOLS  # noqa: E402
from app.backtest.real_data.loader import load_real_ohlcv, summarize_load_results  # noqa: E402
from app.backtest.types import BacktestConfig  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY  # noqa: E402


_log = logging.getLogger("autotrade.stress_test_cli")


DEFAULT_OUTPUT_DIR = "reports/stress_test"
DEFAULT_START      = "2025-01-01"
DEFAULT_END        = "2026-05-01"


def _build_bt_config(commission_bps: int, slippage_bps: int, tax_bps: int) -> BacktestConfig:
    return BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3-05 — Stress test (10 시나리오) CLI",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--from-walk-forward", default=None,
                   help="3-04 walk_forward_summary.json 경로 (HEALTHY 만 추출).")
    p.add_argument("--strategy", default=None,
                   help="단일 전략 (--symbol 와 함께 사용).")
    p.add_argument("--symbol", action="append", default=None,
                   help="대상 종목 (반복 가능). 생략 + walk-forward 미사용 시 대표 10종.")
    p.add_argument("--scenarios", nargs="*", default=None,
                   help="실행 시나리오 (default 10 전체).")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end",   default=DEFAULT_END)
    p.add_argument("--initial-cash",   type=int, default=10_000_000)
    p.add_argument("--quantity",       type=int, default=10)
    p.add_argument("--commission-bps", type=int, default=15)
    p.add_argument("--tax-bps",        type=int, default=23)
    p.add_argument("--slippage-bps",   type=int, default=5)
    p.add_argument("--enable-yfinance", action="store_true")
    # StressTestConfig
    p.add_argument("--min-trade-count",   type=int,   default=5)
    p.add_argument("--pass-max-dd",       type=float, default=0.20)
    p.add_argument("--warn-max-dd",       type=float, default=0.15)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _resolve_candidates(args: argparse.Namespace) -> list[StressCandidateInput]:
    """입력 모드별 후보 산정."""
    if args.from_walk_forward:
        path = Path(args.from_walk_forward)
        if not path.exists():
            raise SystemExit(f"walk_forward summary not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return read_candidates_from_walk_forward(payload)

    symbols = list(args.symbol) if args.symbol else [s.symbol for s in REPRESENTATIVE_SYMBOLS]
    if args.strategy:
        if args.strategy not in STRATEGY_REGISTRY:
            raise SystemExit(
                f"unknown strategy: {args.strategy}. "
                f"registered: {sorted(STRATEGY_REGISTRY.keys())}"
            )
        return [
            StressCandidateInput(strategy=args.strategy, symbol=s, params={}, verdict="HEALTHY")
            for s in symbols
        ]
    out: list[StressCandidateInput] = []
    for sname in STRATEGY_REGISTRY.keys():
        for sym in symbols:
            out.append(StressCandidateInput(
                strategy=sname, symbol=sym, params={}, verdict="HEALTHY",
            ))
    return out


def _resolve_scenarios(args: argparse.Namespace) -> list[StressScenario]:
    if not args.scenarios:
        return list(StressScenario)
    valid = {s.value for s in StressScenario}
    out: list[StressScenario] = []
    for name in args.scenarios:
        if name not in valid:
            raise SystemExit(
                f"unknown scenario: {name}. valid: {sorted(valid)}"
            )
        out.append(StressScenario(name))
    return out


def run_stress(args: argparse.Namespace) -> dict[str, Any]:
    """모든 (candidate × scenario) 매트릭스 실행."""
    bt_cfg = _build_bt_config(args.commission_bps, args.slippage_bps, args.tax_bps)
    stress_cfg = StressTestConfig(
        min_trade_count=int(args.min_trade_count),
        pass_max_drawdown=float(args.pass_max_dd),
        warn_max_drawdown=float(args.warn_max_dd),
    )
    start_dt = datetime.fromisoformat(args.start)
    end_dt   = datetime.fromisoformat(args.end)

    candidates = _resolve_candidates(args)
    scenarios  = _resolve_scenarios(args)

    # 데이터 로드 캐시.
    symbol_to_load: dict[str, Any] = {}
    for c in candidates:
        if c.symbol in symbol_to_load:
            continue
        symbol_to_load[c.symbol] = load_real_ohlcv(
            c.symbol, start=start_dt, end=end_dt,
            enable_yfinance=bool(args.enable_yfinance),
        )

    results: list[dict[str, Any]] = []
    verdict_counts: dict[str, int] = {}

    for c in candidates:
        load_res = symbol_to_load.get(c.symbol)
        if load_res is None or load_res.bars is None:
            for scenario in scenarios:
                entry = {
                    "scenario_name":               scenario.value,
                    "strategy":                    c.strategy,
                    "symbol":                      c.symbol,
                    "stress_verdict":              StressVerdict.INSUFFICIENT_DATA.value,
                    "stress_score":                0.0,
                    "trade_count":                 0,
                    "total_return":                0.0,
                    "expectancy":                  0.0,
                    "profit_factor":               None,
                    "max_drawdown":                0.0,
                    "win_rate":                    0.0,
                    "loss_streak":                 0,
                    "rejected_order_count":        0,
                    "stale_data_violation_count":  0,
                    "duplicate_signal_count":      0,
                    "slippage_cost":               0.0,
                    "reasons":                     [
                        f"data_status={load_res.status.value if load_res else 'NO_DATA'}"
                    ],
                    "is_order_signal":         False,
                    "auto_apply_allowed":      False,
                    "is_live_authorization":   False,
                }
                results.append(entry)
                verdict_counts[entry["stress_verdict"]] = \
                    verdict_counts.get(entry["stress_verdict"], 0) + 1
            continue

        for scenario in scenarios:
            result = evaluate_stress(
                bars=load_res.bars,
                strategy_name=c.strategy,
                symbol=c.symbol,
                scenario=scenario,
                params=c.params,
                config=stress_cfg,
                bt_config=bt_cfg,
                initial_cash=int(args.initial_cash),
                quantity=int(args.quantity),
            )
            d = result.to_dict()
            results.append(d)
            verdict_counts[d["stress_verdict"]] = verdict_counts.get(d["stress_verdict"], 0) + 1

    payload = {
        "config": {
            "initial_cash":   args.initial_cash,
            "quantity":       args.quantity,
            "commission_bps": args.commission_bps,
            "tax_bps":        args.tax_bps,
            "slippage_bps":   args.slippage_bps,
            "start":          args.start,
            "end":            args.end,
        },
        "stress_test_config": stress_cfg.to_dict(),
        "scenarios":          [s.value for s in scenarios],
        "data_summary":       summarize_load_results(list(symbol_to_load.values())),
        "candidate_count":    len(candidates),
        "scenario_run_count": len(results),
        "verdict_counts":     verdict_counts,
        "results":            results,
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
    }
    return payload


def _write_outputs(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    json_path = output_dir / "stress_test_summary.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    written["summary"] = json_path

    csv_path = output_dir / "stress_test_ranking.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "scenario_name", "strategy", "symbol", "stress_verdict",
            "stress_score", "trade_count", "expectancy",
            "profit_factor", "max_drawdown", "win_rate", "loss_streak",
            "rejected_order_count", "stale_data_violation_count",
            "duplicate_signal_count", "slippage_cost",
        ])
        rank = {
            StressVerdict.PASS.value: 0,
            StressVerdict.WARN.value: 1,
            StressVerdict.FAIL.value: 2,
            StressVerdict.INSUFFICIENT_DATA.value: 3,
        }
        sorted_results = sorted(
            payload["results"],
            key=lambda r: (rank.get(r["stress_verdict"], 9),
                            -float(r.get("stress_score", 0) or 0)),
        )
        for r in sorted_results:
            w.writerow([
                r["scenario_name"], r["strategy"], r["symbol"],
                r["stress_verdict"], r["stress_score"], r["trade_count"],
                r["expectancy"], r["profit_factor"], r["max_drawdown"],
                r["win_rate"], r["loss_streak"], r["rejected_order_count"],
                r["stale_data_violation_count"], r["duplicate_signal_count"],
                r["slippage_cost"],
            ])
    written["ranking_csv"] = csv_path

    md_path = output_dir / "stress_test_report.md"
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    written["report_md"] = md_path
    return written


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Step 3-05 -- Stress test report")
    lines.append("")
    lines.append(
        "> 본 리포트는 *분석 자료* 입니다. **투자 조언이 아닙니다.** "
        "FAIL / WARN / PASS 라벨은 *분석 라벨* — paper 운용 / 실거래 활성화 / "
        "자동 promotion 변경 의미 X."
    )
    lines.append("")
    lines.append("## Stress config")
    for k, v in payload["stress_test_config"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Scenarios")
    for s in payload["scenarios"]:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## Verdict 분포")
    for verdict, count in sorted(payload["verdict_counts"].items()):
        lines.append(f"- {verdict}: {count}")
    lines.append("")
    lines.append("## PASS verdict 결과")
    pass_results = [r for r in payload["results"] if r["stress_verdict"] == "PASS"]
    if pass_results:
        lines.append("| scenario | strategy | symbol | score | trades | expectancy | MDD |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in pass_results[:20]:
            lines.append(
                f"| {r['scenario_name']} | {r['strategy']} | {r['symbol']} | "
                f"{r['stress_score']:.1f} | {r['trade_count']} | "
                f"{r['expectancy']:.2f} | {r['max_drawdown']:.4f} |"
            )
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## 다음 단계 (3-06 성과 지표 통합)")
    lines.append(
        "- PASS verdict 가 모든 시나리오에서 유지되는 후보만 paper 후보 검토.\n"
        "- FAIL / WARN 다수 시나리오 후보는 *별도 PR* 로 grid 재정의 또는 "
        "후보 박탈.\n"
        "- 본 리포트는 운영자 검토 자료 — 자동 적용 / 자동 promotion 없음."
    )
    return "\n".join(lines)


def _stdout_summary(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3-05] stress test summary")
    lines.append("=" * 72)
    lines.append(
        f"candidates={payload['candidate_count']}  "
        f"scenarios={len(payload['scenarios'])}  "
        f"runs={payload['scenario_run_count']}"
    )
    lines.append("")
    lines.append("verdict counts:")
    for v, c in sorted(payload["verdict_counts"].items()):
        lines.append(f"  {v:22s}: {c}")
    lines.append("")
    pass_count = payload["verdict_counts"].get("PASS", 0)
    fail_count = payload["verdict_counts"].get("FAIL", 0)
    if pass_count > 0:
        lines.append(f"PASS runs: {pass_count}")
    if fail_count > 0:
        lines.append(f"FAIL runs: {fail_count}")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    payload = run_stress(args)
    print(_stdout_summary(payload))

    if args.dry_run:
        _log.info("dry-run mode -- file output skipped.")
        return 0

    out_dir = Path(args.output_dir)
    written = _write_outputs(payload, out_dir)
    for k, p in written.items():
        _log.info("wrote %s -> %s", k, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
