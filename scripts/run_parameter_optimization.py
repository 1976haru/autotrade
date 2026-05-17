#!/usr/bin/env python3
"""3-03 — 실제 OHLCV 데이터 기반 6 전략 parameter optimization CLI.

3-02 의 실제 데이터 백테스트 러너 위에 *제한된* parameter grid search 를
얹어 (strategy × symbol × params) 매트릭스를 1회 명령으로 실행한다.

산출물 (default ``reports/parameter_optimization/``):
- ``parameter_optimization_summary.json``  per_strategy × per_symbol × per_params 결과.
- ``parameter_optimization_ranking.csv``   PAPER_CANDIDATE 정렬.
- ``paper_candidate_config.json``          상위 N 후보 + 사유 (3-07 입력).
- ``parameter_optimization_report.md``     운영자 검토용 markdown 요약.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- ``ENABLE_LIVE_TRADING`` / ``ENABLE_AI_EXECUTION`` / ``ENABLE_FUTURES_LIVE_TRADING`` /
  ``KIS_IS_PAPER`` 변경 0건.
- 실제 매수 / 매도 / Place Order 0건 — 본 스크립트는 *분석 read-only*.
- PAPER_CANDIDATE 라벨은 *paper 운용 후보* — 자동 실거래 활성화 아님.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


# repo root 를 PYTHONPATH 에 추가.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.types import BacktestConfig  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY, build_strategy  # noqa: E402

from app.backtest.real_data import (  # noqa: E402
    OptimizationThresholds,
    OptimizationVerdict,
    PARAMETER_GRIDS,
    REPRESENTATIVE_SYMBOLS,
    classify_optimization_run,
    iter_param_grid,
    total_combinations,
)
from app.backtest.real_data.loader import (  # noqa: E402
    load_real_ohlcv,
    summarize_load_results,
)
from app.backtest.real_data.paper_candidate import (  # noqa: E402
    CandidateInput,
    build_paper_candidate_config,
    write_paper_candidate_config,
)


_log = logging.getLogger("autotrade.parameter_optimization")


# 비용 / 자본 default — 3-02 와 동일 보수값.
DEFAULT_COMMISSION_BPS = 15
DEFAULT_TAX_BPS        = 23
DEFAULT_SLIPPAGE_BPS   = 5
DEFAULT_INITIAL_CASH   = 10_000_000
DEFAULT_QUANTITY       = 10
DEFAULT_OUTPUT_DIR     = "reports/parameter_optimization"
DEFAULT_START          = "2025-01-01"
DEFAULT_END            = "2026-05-01"
DEFAULT_TOP_K          = 2


def _build_config(commission_bps: int, slippage_bps: int, tax_bps: int) -> BacktestConfig:
    return BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
    )


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _extract_metrics(*, result, initial_cash: int) -> dict[str, Any]:
    """BacktestResult → metric dict (verdict 분류기 + 리포트용)."""
    trades = list(getattr(result, "trades", []) or [])
    pnls: list[float] = []
    for t in trades:
        pnl = getattr(t, "pnl", None)
        if pnl is None and isinstance(t, dict):
            pnl = t.get("pnl")
        pnls.append(_safe_float(pnl))

    trade_count = len(pnls)
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_win  = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = (total_win / total_loss) if total_loss > 0 else (
        None if total_win == 0 else float("inf")
    )
    profit_factor_json: Any = (
        None if (profit_factor is None or profit_factor == float("inf"))
        else _safe_float(profit_factor)
    )

    longest_losing = 0
    current = 0
    for p in pnls:
        if p < 0:
            current += 1
            longest_losing = max(longest_losing, current)
        else:
            current = 0

    raw_return = _safe_float(getattr(result, "total_return", 0.0))
    fees       = _safe_float(getattr(result, "fees_paid", 0.0))
    taxes      = _safe_float(getattr(result, "taxes_paid", 0.0))
    slippage   = _safe_float(getattr(result, "slippage_paid", 0.0))
    max_dd     = _safe_float(getattr(result, "max_drawdown", 0.0))

    initial = max(_safe_float(initial_cash), 1.0)
    avg_trade_pnl = (sum(pnls) / trade_count) if trade_count > 0 else 0.0
    win_rate      = (len(wins) / trade_count) if trade_count > 0 else 0.0
    fee_adjusted_return      = raw_return - (fees + taxes) / initial
    slippage_adjusted_return = fee_adjusted_return - slippage / initial

    return {
        "trade_count":              int(trade_count),
        "profit_factor":            profit_factor_json,
        "max_drawdown":             _safe_float(max_dd),
        "total_return":             raw_return,
        "win_rate":                 _safe_float(win_rate),
        "expectancy":               _safe_float(avg_trade_pnl),
        "avg_trade_pnl":            _safe_float(avg_trade_pnl),
        "loss_streak":              int(longest_losing),
        "fees_paid":                fees,
        "taxes_paid":               taxes,
        "slippage_paid":            slippage,
        "fee_adjusted_return":      _safe_float(fee_adjusted_return),
        "slippage_adjusted_return": _safe_float(slippage_adjusted_return),
    }


def _single_run(
    *,
    strategy_name: str,
    params: dict[str, Any],
    bars,
    initial_cash: int,
    quantity: int,
    config: BacktestConfig,
) -> dict[str, Any]:
    """단일 (strategy, params, symbol) backtest. 실패는 dict carry."""
    try:
        strategy = build_strategy(strategy_name, params=dict(params))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"strategy_build_failed: {type(exc).__name__}: {exc}",
        }

    engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
    try:
        result = engine.run(bars, strategy, config=config)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"engine_run_failed: {type(exc).__name__}: {exc}",
        }

    metrics = _extract_metrics(result=result, initial_cash=initial_cash)
    return {"ok": True, "metrics": metrics}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3-03 — 실제 데이터 기반 6 전략 parameter optimization",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--symbol", action="append", default=None,
                   help="대상 종목 (--symbol 반복). 생략 시 대표 10종.")
    p.add_argument("--strategies", nargs="*", default=None,
                   help="실행 전략 이름. 생략 시 6개 전체.")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end",   default=DEFAULT_END)
    p.add_argument("--initial-cash",   type=int, default=DEFAULT_INITIAL_CASH)
    p.add_argument("--quantity",       type=int, default=DEFAULT_QUANTITY)
    p.add_argument("--commission-bps", type=int, default=DEFAULT_COMMISSION_BPS)
    p.add_argument("--tax-bps",        type=int, default=DEFAULT_TAX_BPS)
    p.add_argument("--slippage-bps",   type=int, default=DEFAULT_SLIPPAGE_BPS)
    p.add_argument("--enable-yfinance", action="store_true",
                   help="CSV 없는 symbol 에 한해 yfinance fetch (graceful).")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                   help="paper_candidate 상한 (default 2, 0~5 권장).")
    p.add_argument("--min-trade-count",    type=int,   default=10)
    p.add_argument("--min-profit-factor",  type=float, default=1.10)
    p.add_argument("--max-drawdown-pct",   type=float, default=0.15)
    p.add_argument("--min-expectancy-krw", type=float, default=0.0)
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 X — stdout 요약만.")
    return p.parse_args(argv)


def run_optimization(args: argparse.Namespace) -> dict[str, Any]:
    """전체 grid search 매트릭스 실행 + 결과 dict 반환."""
    symbols = list(args.symbol) if args.symbol else [
        s.symbol for s in REPRESENTATIVE_SYMBOLS
    ]
    requested_strategies = (
        list(args.strategies) if args.strategies else list(STRATEGY_REGISTRY.keys())
    )
    unknown = [s for s in requested_strategies if s not in STRATEGY_REGISTRY]
    if unknown:
        raise SystemExit(
            f"unknown strategies: {unknown}. "
            f"registered: {sorted(STRATEGY_REGISTRY.keys())}"
        )

    config = _build_config(args.commission_bps, args.slippage_bps, args.tax_bps)
    start_dt = datetime.fromisoformat(args.start)
    end_dt   = datetime.fromisoformat(args.end)

    thresholds = OptimizationThresholds(
        min_trade_count=int(args.min_trade_count),
        min_profit_factor=float(args.min_profit_factor),
        max_drawdown_pct=float(args.max_drawdown_pct),
        min_expectancy_krw=float(args.min_expectancy_krw),
    )

    # 1) 데이터 로드.
    load_results = [
        load_real_ohlcv(
            sym, start=start_dt, end=end_dt,
            enable_yfinance=bool(args.enable_yfinance),
        )
        for sym in symbols
    ]

    # 2) (strategy, symbol, params) 매트릭스 실행.
    all_runs: list[dict[str, Any]] = []
    candidate_inputs: list[CandidateInput] = []
    per_strategy_top: dict[str, list[dict[str, Any]]] = {
        s: [] for s in requested_strategies
    }
    verdict_counts: dict[str, int] = {}

    for sname in requested_strategies:
        grid = list(iter_param_grid(sname))
        if not grid:
            _log.warning("[opt] no grid defined for strategy %s — skipped", sname)
            continue
        for load_res in load_results:
            if load_res.bars is None:
                continue
            for params in grid:
                single = _single_run(
                    strategy_name=sname,
                    params=params,
                    bars=load_res.bars,
                    initial_cash=int(args.initial_cash),
                    quantity=int(args.quantity),
                    config=config,
                )
                if not single.get("ok"):
                    entry = {
                        "strategy": sname,
                        "symbol":   load_res.symbol,
                        "params":   params,
                        "verdict":  OptimizationVerdict.INSUFFICIENT_DATA.value,
                        "reasons":  ["engine_or_strategy_error"],
                        "error":    single.get("error", "unknown"),
                    }
                    all_runs.append(entry)
                    verdict_counts[entry["verdict"]] = verdict_counts.get(
                        entry["verdict"], 0
                    ) + 1
                    continue

                metrics = single["metrics"]
                classification = classify_optimization_run(metrics, thresholds=thresholds)
                entry = {
                    "strategy": sname,
                    "symbol":   load_res.symbol,
                    "params":   params,
                    "verdict":  classification.verdict.value,
                    "reasons":  classification.reasons,
                    "metrics":  metrics,
                }
                all_runs.append(entry)
                verdict_counts[entry["verdict"]] = verdict_counts.get(
                    entry["verdict"], 0
                ) + 1
                # 모든 run 을 candidate_inputs 으로 carry — build_paper_candidate_config
                # 가 PAPER_CANDIDATE 만 필터링 후, 그 외 verdict 분포는
                # reasons_no_candidate 에 집계.
                score = float(metrics.get("expectancy", 0.0) or 0.0)
                candidate_inputs.append(CandidateInput(
                    strategy=sname,
                    symbol=load_res.symbol,
                    params=dict(params),
                    risk_metrics=metrics,
                    validation_status=classification.verdict,
                    reasons=list(classification.reasons),
                    score=score,
                    extra={
                        "data_source": load_res.source,
                        "data_status": load_res.status.value,
                        "bar_count":   load_res.bar_count,
                    },
                ))
                # 전략별 상위 정렬용 (verdict 무관 — 운영자가 LOW_QUALITY 도 검토).
                per_strategy_top[sname].append(entry)

    # 전략별 상위 (verdict 우선 PAPER_CANDIDATE → 그 외, score 내림차순).
    def _sort_key(e):
        m = e.get("metrics") or {}
        verdict_rank = {
            OptimizationVerdict.PAPER_CANDIDATE.value:     0,
            OptimizationVerdict.LOW_QUALITY.value:         1,
            OptimizationVerdict.HIGH_DRAWDOWN.value:       2,
            OptimizationVerdict.NEGATIVE_EXPECTANCY.value: 3,
            OptimizationVerdict.INSUFFICIENT_DATA.value:   4,
        }.get(e["verdict"], 5)
        score = -float(m.get("expectancy", 0.0) or 0.0)
        return (verdict_rank, score)

    for sname in per_strategy_top:
        per_strategy_top[sname] = sorted(per_strategy_top[sname], key=_sort_key)

    # 3) paper_candidate config 빌드.
    metadata = {
        "pipeline": "step3-real-data-parameter-optimization",
        "config": {
            "initial_cash":     args.initial_cash,
            "quantity":         args.quantity,
            "commission_bps":   args.commission_bps,
            "tax_bps":          args.tax_bps,
            "slippage_bps":     args.slippage_bps,
            "start":            args.start,
            "end":              args.end,
        },
        "thresholds":      thresholds.to_dict(),
        "strategies":      requested_strategies,
        "symbols":         symbols,
        "data_summary":    summarize_load_results(load_results),
        "grid_total":      total_combinations(),
        "run_total":       len(all_runs),
    }
    paper_config = build_paper_candidate_config(
        candidate_inputs, top_k=int(args.top_k), metadata=metadata,
    )

    return {
        "load_results": [
            {
                "symbol":    r.symbol,
                "status":    r.status.value,
                "reason":    r.reason,
                "source":    r.source,
                "bar_count": r.bar_count,
            }
            for r in load_results
        ],
        "all_runs":              all_runs,
        "per_strategy_top":      per_strategy_top,
        "verdict_counts":        verdict_counts,
        "candidate_count":       paper_config.candidate_count,
        "paper_candidate_config": paper_config,
        # 최상위 invariant — JSON consumer 측에서도 안전.
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
    }


def _write_outputs(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    # paper_candidate_config.json — 가장 중요한 산출물.
    paper_path = output_dir / "paper_candidate_config.json"
    written["paper_candidate_config"] = write_paper_candidate_config(
        payload["paper_candidate_config"], paper_path,
    )

    # 전체 summary.
    summary_path = output_dir / "parameter_optimization_summary.json"
    summary_payload = {
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
        "load_results":          payload["load_results"],
        "all_runs":              payload["all_runs"],
        "per_strategy_top":      payload["per_strategy_top"],
        "verdict_counts":        payload["verdict_counts"],
        "candidate_count":       payload["candidate_count"],
    }
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    written["summary"] = summary_path

    # CSV ranking — PAPER_CANDIDATE / 전체.
    csv_path = output_dir / "parameter_optimization_ranking.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "strategy", "symbol", "params_json", "verdict", "trade_count",
            "profit_factor", "max_drawdown", "expectancy",
            "fee_adjusted_return", "slippage_adjusted_return",
        ])
        for entry in payload["all_runs"]:
            m = entry.get("metrics") or {}
            w.writerow([
                entry["strategy"], entry["symbol"],
                json.dumps(entry["params"], ensure_ascii=False, sort_keys=True),
                entry["verdict"],
                m.get("trade_count"), m.get("profit_factor"),
                m.get("max_drawdown"), m.get("expectancy"),
                m.get("fee_adjusted_return"), m.get("slippage_adjusted_return"),
            ])
    written["ranking_csv"] = csv_path

    # Markdown — 운영자 검토용.
    md_path = output_dir / "parameter_optimization_report.md"
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    written["report_md"] = md_path

    return written


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Step 3-03 — Parameter optimization report")
    lines.append("")
    lines.append(
        "> 본 리포트는 *분석 자료* 입니다. **투자 조언이 아닙니다.** "
        "PAPER_CANDIDATE 라벨은 paper 운용 *검토 가능* 표시 — 자동 실거래 활성화 X."
    )
    lines.append("")
    lines.append("## Verdict 분포")
    for verdict, count in sorted(payload["verdict_counts"].items()):
        lines.append(f"- {verdict}: {count}")
    lines.append("")
    lines.append(f"## paper 후보 ({payload['candidate_count']}건)")
    paper_dict = payload["paper_candidate_config"].to_dict()
    if paper_dict["candidate_count"] > 0:
        for c in paper_dict["candidates"]:
            lines.append(
                f"- {c['strategy']} / {c['symbol']}  "
                f"params={c['params']}  score={c['score']:.2f}"
            )
    else:
        lines.append("(none)")
        if paper_dict["reasons_no_candidate"]:
            lines.append("**사유:**")
            for r in paper_dict["reasons_no_candidate"]:
                lines.append(f"- {r}")
    lines.append("")
    lines.append("## 전략별 상위 결과")
    for sname, entries in payload["per_strategy_top"].items():
        lines.append(f"### {sname}")
        if not entries:
            lines.append("(no runs — 데이터 없음 또는 grid 미정의)")
            continue
        top5 = entries[:5]
        lines.append("| symbol | params | verdict | trades | PF | MDD | expectancy |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in top5:
            m = e.get("metrics") or {}
            params_str = ", ".join(f"{k}={v}" for k, v in e["params"].items())
            lines.append(
                f"| {e['symbol']} | {params_str} | {e['verdict']} | "
                f"{m.get('trade_count', '-')} | {m.get('profit_factor', '-')} | "
                f"{m.get('max_drawdown', 0.0):.4f} | "
                f"{m.get('expectancy', 0.0):.2f} |"
            )
    lines.append("")
    lines.append("## 다음 단계")
    lines.append(
        "- 3-04 Walk-forward 검증 (별도 PR) — train / validation 분리, "
        "OVERFIT_RISK 라벨된 전략은 후보 자격 박탈.\n"
        "- 3-05 Stress test (별도 PR) — 6 시나리오별 데이터 변형 / 비용 가중.\n"
        "- 운영자 검토 → paper_candidate_config.json → Paper Auto Loop "
        "*수동* 입력 (자동 적용 금지)."
    )
    return "\n".join(lines)


def _stdout_summary(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3-03] parameter optimization summary")
    lines.append("=" * 72)
    lines.append("verdict counts:")
    for v, c in sorted(payload["verdict_counts"].items()):
        lines.append(f"  {v:22s}: {c}")
    lines.append("")
    lines.append(f"paper_candidate_count: {payload['candidate_count']}")
    paper_dict = payload["paper_candidate_config"].to_dict()
    if paper_dict["candidate_count"] > 0:
        for c in paper_dict["candidates"]:
            params_str = ", ".join(f"{k}={v}" for k, v in c["params"].items())
            lines.append(
                f"  + {c['strategy']:20s} / {c['symbol']}  "
                f"params=[{params_str}]  score={c['score']:.4f}"
            )
    else:
        lines.append("  (no PAPER_CANDIDATE -- see reasons_no_candidate)")
        for r in paper_dict["reasons_no_candidate"]:
            lines.append(f"    - {r}")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    payload = run_optimization(args)
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
