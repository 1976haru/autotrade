#!/usr/bin/env python3
"""3-02 — 실제 / 준실제 OHLCV 데이터 기반 6 전략 백테스트 실행 CLI.

본 스크립트는 `MockMarketData` 가 *아니라* 실제 / 준실제 OHLCV 데이터로
``STRATEGY_REGISTRY`` 의 6개 전략을 한 번에 백테스트한다.

데이터 소스 (`app.backtest.real_data.loader.load_real_ohlcv`):
1. 로컬 CSV (repo) 우선.
2. ``--enable-yfinance`` 시 yfinance fallback (read-only, graceful).
3. 데이터 없음 → 해당 symbol skip + 사유 carry.

산출물 (default ``reports/backtest_real/``):
- ``real_data_backtest_summary.json``  per_symbol × per_strategy 결과 + verdict.
- ``real_data_backtest_ranking.csv``   BACKTEST_PASS verdict 정렬.
- ``real_data_backtest_report.md``     운영자 검토용 markdown 요약.

본 PR (3-02) 의 책임은 *백테스트 실행* + 결과 저장 + verdict 분류 까지.
3-03 파라미터 최적화 / 3-04 walk-forward / 3-05 stress test / 3-07 paper
candidate export 는 *별도 PR*.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- ``ENABLE_LIVE_TRADING`` / ``ENABLE_AI_EXECUTION`` /
  ``ENABLE_FUTURES_LIVE_TRADING`` / ``KIS_IS_PAPER`` 변경 0건.
- 실제 매수 / 매도 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- secret / API key / 계좌번호 / ``.env`` 노출 0건.

사용:
    # 1) repo CSV 만으로 실행 (CI / 자동 테스트 안전).
    python scripts/run_backtest_real_data.py

    # 2) yfinance fetch 옵트인 (네트워크 필요, 실패해도 graceful).
    python scripts/run_backtest_real_data.py --enable-yfinance

    # 3) 특정 symbol / strategy 만.
    python scripts/run_backtest_real_data.py \\
        --symbol 005930 --strategies sma_crossover rsi_reversion
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


# repo root 를 PYTHONPATH 에 추가 — ``python scripts/...`` 단독 실행 호환.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.types import BacktestConfig  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY, build_strategy  # noqa: E402

from app.backtest.real_data import (  # noqa: E402
    BacktestVerdict,
    FilterThresholds,
    REPRESENTATIVE_SYMBOLS,
    classify_backtest_metrics,
)
from app.backtest.real_data.loader import (  # noqa: E402
    LoadStatus,
    load_real_ohlcv,
    summarize_load_results,
)


_log = logging.getLogger("autotrade.real_data_runner")


# 비용 / 자본 default — `scripts/run_backtest_all_strategies.py` 와 동일 보수값.
DEFAULT_COMMISSION_BPS = 15
DEFAULT_TAX_BPS        = 23
DEFAULT_SLIPPAGE_BPS   = 5
DEFAULT_INITIAL_CASH   = 10_000_000
DEFAULT_QUANTITY       = 10
DEFAULT_OUTPUT_DIR     = "reports/backtest_real"
DEFAULT_START          = "2025-01-01"
DEFAULT_END            = "2026-05-01"


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


def _extract_metrics(
    *, result, initial_cash: int,
) -> dict[str, Any]:
    """BacktestResult → 백테스트 metric dict.

    필수 키 (verdict 분류기가 사용): trade_count / profit_factor / max_drawdown.
    부가 키: total_return / win_rate / expectancy / fees_paid / taxes_paid /
            slippage_paid / avg_trade_pnl / loss_streak.
    """
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

    total_win   = sum(wins)
    total_loss  = abs(sum(losses))
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
    fee_adjusted_return      = raw_return - (fees + taxes) / initial
    slippage_adjusted_return = fee_adjusted_return - slippage / initial

    avg_trade_pnl = (sum(pnls) / trade_count) if trade_count > 0 else 0.0
    win_rate      = (len(wins) / trade_count) if trade_count > 0 else 0.0

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
    bars,
    initial_cash: int,
    quantity: int,
    config: BacktestConfig,
) -> dict[str, Any]:
    """단일 (strategy, symbol) backtest 실행. 실패는 dict 에 reason 담아 carry."""
    try:
        strategy = build_strategy(strategy_name, params=None)
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
    return {"ok": True, "metrics": metrics, "params": {}}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3-02 — 실제 OHLCV 데이터 기반 6 전략 백테스트 runner",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--symbol", action="append", default=None,
                   help="대상 종목 (--symbol 여러 번 반복). 생략 시 대표 10종.")
    p.add_argument("--strategies", nargs="*", default=None,
                   help="실행할 전략 이름. 생략 시 6개 전체.")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end",   default=DEFAULT_END)
    p.add_argument("--initial-cash",   type=int, default=DEFAULT_INITIAL_CASH)
    p.add_argument("--quantity",       type=int, default=DEFAULT_QUANTITY)
    p.add_argument("--commission-bps", type=int, default=DEFAULT_COMMISSION_BPS)
    p.add_argument("--tax-bps",        type=int, default=DEFAULT_TAX_BPS)
    p.add_argument("--slippage-bps",   type=int, default=DEFAULT_SLIPPAGE_BPS)
    p.add_argument("--enable-yfinance", action="store_true",
                   help="CSV 없는 symbol 에 한해 yfinance fetch 시도 (graceful).")
    p.add_argument("--min-trade-count",    type=int,   default=10)
    p.add_argument("--min-profit-factor",  type=float, default=1.10)
    p.add_argument("--max-drawdown-pct",   type=float, default=0.15)
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 X — stdout 요약만.")
    return p.parse_args(argv)


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    """전체 백테스트 매트릭스 실행 + 결과 dict 반환. caller 가 파일 작성 분기."""
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

    thresholds = FilterThresholds(
        min_trade_count=int(args.min_trade_count),
        min_profit_factor=float(args.min_profit_factor),
        max_drawdown_pct=float(args.max_drawdown_pct),
    )

    # 1) 데이터 로드.
    load_results = [
        load_real_ohlcv(
            sym, start=start_dt, end=end_dt,
            enable_yfinance=bool(args.enable_yfinance),
        )
        for sym in symbols
    ]

    # 2) 데이터 있는 symbol 만 backtest 매트릭스 실행 + verdict 분류.
    per_symbol_records: list[dict[str, Any]] = []
    pass_runs: list[dict[str, Any]] = []
    insufficient_set: set[str] = set()   # 전략 단위 INSUFFICIENT 추적

    for load_res in load_results:
        record = {
            "symbol":      load_res.symbol,
            "data_status": load_res.status.value,
            "reason":      load_res.reason,
            "source":      load_res.source,
            "bar_count":   load_res.bar_count,
            "runs":        [],
        }
        if load_res.bars is None:
            per_symbol_records.append(record)
            continue

        for sname in requested_strategies:
            single = _single_run(
                strategy_name=sname,
                bars=load_res.bars,
                initial_cash=int(args.initial_cash),
                quantity=int(args.quantity),
                config=config,
            )
            if not single.get("ok"):
                record["runs"].append({
                    "strategy": sname,
                    "verdict":  BacktestVerdict.INSUFFICIENT_DATA.value,
                    "error":    single.get("error", "unknown"),
                    "reasons":  ["engine_or_strategy_error"],
                })
                insufficient_set.add(sname)
                continue

            metrics = single["metrics"]
            classification = classify_backtest_metrics(metrics, thresholds=thresholds)
            entry = {
                "strategy":   sname,
                "verdict":    classification.verdict.value,
                "reasons":    classification.reasons,
                "metrics":    metrics,
                "params":     single["params"],
            }
            record["runs"].append(entry)
            if classification.verdict == BacktestVerdict.BACKTEST_PASS:
                pass_runs.append({
                    "strategy": sname,
                    "symbol":   load_res.symbol,
                    "metrics":  metrics,
                    "params":   single["params"],
                })
            elif classification.verdict == BacktestVerdict.INSUFFICIENT_DATA:
                insufficient_set.add(sname)

        per_symbol_records.append(record)

    summary_payload = {
        "config": {
            "initial_cash":     args.initial_cash,
            "quantity":         args.quantity,
            "commission_bps":   args.commission_bps,
            "tax_bps":          args.tax_bps,
            "slippage_bps":     args.slippage_bps,
            "start":            args.start,
            "end":              args.end,
        },
        "thresholds":     thresholds.to_dict(),
        "strategies":     requested_strategies,
        "symbols":        symbols,
        "data_summary":   summarize_load_results(load_results),
        "per_symbol":     per_symbol_records,
        "pass_runs":      pass_runs,
        "insufficient_strategies": sorted(insufficient_set),
        # 본 결과는 *분석 read-only*. 자동 적용 / 실거래 활성화 / 주문 신호 X.
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
    }
    return summary_payload


def _write_outputs(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """JSON / CSV / Markdown 산출물 작성."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    # JSON — 전체 결과.
    json_path = output_dir / "real_data_backtest_summary.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    written["summary_json"] = json_path

    # CSV — BACKTEST_PASS 정렬.
    csv_path = output_dir / "real_data_backtest_ranking.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "strategy", "symbol", "verdict", "trade_count", "profit_factor",
            "max_drawdown", "total_return", "fee_adjusted_return",
            "slippage_adjusted_return",
        ])
        for run in payload["pass_runs"]:
            m = run["metrics"]
            w.writerow([
                run["strategy"], run["symbol"], "BACKTEST_PASS",
                m.get("trade_count"), m.get("profit_factor"),
                m.get("max_drawdown"), m.get("total_return"),
                m.get("fee_adjusted_return"), m.get("slippage_adjusted_return"),
            ])
    written["ranking_csv"] = csv_path

    # Markdown — 운영자 검토용 요약.
    md_path = output_dir / "real_data_backtest_report.md"
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    written["report_md"] = md_path

    return written


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Step 3-02 — Real-data backtest report")
    lines.append("")
    lines.append(
        "> 본 리포트는 *분석 자료* 입니다. **투자 조언이 아닙니다.** "
        "실거래 활성화 / 주문 신호로 사용 금지."
    )
    lines.append("")
    lines.append(f"- generated: backtest matrix run")
    lines.append(f"- strategies: {', '.join(payload['strategies'])}")
    lines.append(f"- symbols:    {', '.join(payload['symbols'])}")
    lines.append(f"- period:     {payload['config']['start']} ~ {payload['config']['end']}")
    lines.append("")
    lines.append("## 데이터 로드 status")
    for status, syms in payload["data_summary"].items():
        lines.append(f"- {status}: {', '.join(syms)}")
    lines.append("")
    lines.append("## BACKTEST_PASS runs")
    if payload["pass_runs"]:
        lines.append("| strategy | symbol | trades | PF | MDD | total_return | fee_adj | slip_adj |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for run in payload["pass_runs"]:
            m = run["metrics"]
            lines.append(
                f"| {run['strategy']} | {run['symbol']} | "
                f"{m.get('trade_count')} | {m.get('profit_factor')} | "
                f"{m.get('max_drawdown'):.4f} | {m.get('total_return'):.4f} | "
                f"{m.get('fee_adjusted_return'):.4f} | "
                f"{m.get('slippage_adjusted_return'):.4f} |"
            )
    else:
        lines.append("(none — all runs failed at least one filter)")
    lines.append("")
    lines.append("## INSUFFICIENT_DATA strategies")
    if payload["insufficient_strategies"]:
        for s in payload["insufficient_strategies"]:
            lines.append(f"- {s}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## 다음 단계")
    lines.append(
        "- 3-03 파라미터 최적화 (별도 PR): BACKTEST_PASS / 경계선 전략의 grid search.\n"
        "- 3-04 Walk-forward 검증 (별도 PR).\n"
        "- 3-05 Stress test (별도 PR).\n"
        "- 3-07 paper_candidate_config (별도 PR — 운영자 검토 후)."
    )
    return "\n".join(lines)


def _stdout_summary(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3-02] real-data backtest summary")
    lines.append("=" * 72)
    lines.append(f"strategies: {', '.join(payload['strategies'])}")
    lines.append(f"symbols:    {', '.join(payload['symbols'])}")
    lines.append("")
    lines.append("data load status:")
    for rec in payload["per_symbol"]:
        lines.append(
            f"  {rec['symbol']}: {rec['data_status']:14s} bars={rec['bar_count']:6d}  "
            f"src={rec['source']}  reason={rec['reason']}"
        )
    lines.append("")
    lines.append(f"BACKTEST_PASS count: {len(payload['pass_runs'])}")
    for run in payload["pass_runs"][:10]:
        m = run["metrics"]
        lines.append(
            f"  + {run['strategy']:20s} / {run['symbol']}  "
            f"trades={m.get('trade_count'):4d}  pf={m.get('profit_factor')}  "
            f"mdd={m.get('max_drawdown'):.4f}"
        )
    lines.append("")
    if payload["insufficient_strategies"]:
        lines.append(
            "INSUFFICIENT_DATA strategies: " + ", ".join(payload["insufficient_strategies"])
        )
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    payload = run_pipeline(args)
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
