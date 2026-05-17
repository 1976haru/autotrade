#!/usr/bin/env python3
"""3단계 — 실제 OHLCV 데이터 기반 6 전략 백테스트 파이프라인 CLI.

본 스크립트는 `app.backtest.real_data` 파이프라인을 1회 명령으로 실행한다:
1. 대표 종목 10종 (또는 --symbol 인자) 에 대해 *실제* OHLCV 데이터 로드 시도.
   - CSV (repo 내) → yfinance (옵션) → 데이터 없음 graceful.
2. 6개 전략 × 데이터 있는 symbol 매트릭스로 백테스트.
3. 13개 표준 지표 (`compute_extended_metrics`) 산출.
4. 5단계 verdict 분류 (`classify_backtest_result`).
5. 상위 1~2 PAPER_CANDIDATE → `reports/paper_candidate_config.json` export.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS API / AI SDK 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 변경 0건.
- 실제 매수 / 매도 / Place Order 0건. 본 스크립트는 *분석 read-only*.
- 후보가 없으면 *빈 후보 + 사유* 만 기록 — 억지로 만들지 않음.
- secret / API key / 계좌번호 / `.env` 노출 0건.

사용:
    # 1) 로컬 CSV 만으로 실행 (CI / 자동 테스트에 안전).
    python scripts/run_real_data_backtest.py

    # 2) yfinance fetch 옵트인 (네트워크 필요, 실패해도 graceful).
    python scripts/run_real_data_backtest.py --enable-yfinance

    # 3) 특정 symbol / strategy 만 실행.
    python scripts/run_real_data_backtest.py --symbol 005930 \\
        --strategies sma_crossover rsi_reversion
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# repo root 를 PYTHONPATH 에 추가 — `python scripts/...` 단독 실행 호환.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.types import BacktestConfig  # noqa: E402
from app.market.base import Interval  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY, build_strategy  # noqa: E402

from app.backtest.real_data import (  # noqa: E402
    REPRESENTATIVE_SYMBOLS,
    REQUIRED_METRIC_KEYS,
    BacktestVerdict,
    classify_backtest_result,
    compute_extended_metrics,
)
from app.backtest.real_data.data_source import (  # noqa: E402
    DataLoadStatus,
    load_real_ohlcv,
    summarize_load_results,
)
from app.backtest.real_data.filters import FilterThresholds  # noqa: E402
from app.backtest.real_data.paper_candidate import (  # noqa: E402
    CandidateInput,
    build_paper_candidate_config,
    write_paper_candidate_config,
)
from app.backtest.real_data.stress_test_connector import list_stress_scenarios  # noqa: E402


_log = logging.getLogger("autotrade.real_data_cli")


# 비용 모델 default — `scripts/run_backtest_all_strategies.py` 와 동일 보수값.
DEFAULT_COMMISSION_BPS = 15
DEFAULT_TAX_BPS        = 23
DEFAULT_SLIPPAGE_BPS   = 5

DEFAULT_INITIAL_CASH = 10_000_000
DEFAULT_QUANTITY     = 10
DEFAULT_OUTPUT_DIR   = "reports/backtest_real"

# 본 PR 시점에 기본 기간 — 실제 CSV / yfinance 가 커버 가능한 보수적 범위.
DEFAULT_START = "2025-01-01"
DEFAULT_END   = "2026-05-01"


def _build_config(
    commission_bps: int, slippage_bps: int, tax_bps: int,
) -> BacktestConfig:
    """비용 / 체결 모델. next_open 권장 + 비용 반영."""
    return BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
    )


def _build_strategy(name: str):
    # build_strategy(name, params=None) enforces strategy contract metadata —
    # 기존 backtest 흐름과 동일한 helper.
    return build_strategy(name, params=None)


def _result_to_extended_metrics(
    *,
    result,
    initial_cash: int,
    bars,
) -> dict[str, Any]:
    """BacktestResult → 13개 표준 지표 dict."""
    trades = list(getattr(result, "trades", []) or [])
    raw_return = float(getattr(result, "total_return", 0.0) or 0.0)
    fees       = float(getattr(result, "fees_paid", 0.0) or 0.0)
    taxes      = float(getattr(result, "taxes_paid", 0.0) or 0.0)
    slippage   = float(getattr(result, "slippage_paid", 0.0) or 0.0)
    max_dd     = float(getattr(result, "max_drawdown", 0.0) or 0.0)

    # 거래일 — bar 개수 추정 (일봉 가정).
    trading_days = max(len(bars), 1)

    return compute_extended_metrics(
        trades=trades,
        initial_cash=initial_cash,
        trading_days=trading_days,
        raw_return=raw_return,
        fees_paid=fees,
        taxes_paid=taxes,
        slippage_paid=slippage,
        max_drawdown=max_dd,
    )


def _single_run(
    *,
    strategy_name: str,
    symbol: str,
    bars,
    initial_cash: int,
    quantity: int,
    config: BacktestConfig,
) -> dict[str, Any]:
    """단일 (strategy, symbol) 백테스트 실행. 실패는 dict 에 사유 담아 carry."""
    try:
        strategy = _build_strategy(strategy_name)
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

    metrics = _result_to_extended_metrics(
        result=result, initial_cash=initial_cash, bars=bars,
    )
    return {
        "ok":      True,
        "metrics": metrics,
        "params":  {},   # 본 PR 시점 default 파라미터 — grid search 는 후속 PR.
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3단계 — 실제 OHLCV 데이터 기반 6 전략 backtest 파이프라인",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--symbol", action="append", default=None,
                   help="대상 종목 (--symbol 여러 번 반복 가능). 생략 시 대표 10종.")
    p.add_argument("--strategies", nargs="*", default=None,
                   help="실행할 전략 이름 (생략 시 6개 전체).")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end",   default=DEFAULT_END)
    p.add_argument("--initial-cash",   type=int, default=DEFAULT_INITIAL_CASH)
    p.add_argument("--quantity",       type=int, default=DEFAULT_QUANTITY)
    p.add_argument("--commission-bps", type=int, default=DEFAULT_COMMISSION_BPS)
    p.add_argument("--tax-bps",        type=int, default=DEFAULT_TAX_BPS)
    p.add_argument("--slippage-bps",   type=int, default=DEFAULT_SLIPPAGE_BPS)
    p.add_argument("--enable-yfinance", action="store_true",
                   help="CSV 없는 symbol 에 한해 yfinance fetch 시도 (graceful).")
    p.add_argument("--top-k", type=int, default=2,
                   help="paper_candidate 상한 (default 2, 0~5 권장).")
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 X — stdout 에 요약만.")
    return p.parse_args(argv)


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    """전체 파이프라인 실행 + 결과 dict 반환. caller 가 파일 작성 분기."""
    symbols = list(args.symbol) if args.symbol else [
        s.symbol for s in REPRESENTATIVE_SYMBOLS
    ]
    requested_strategies = list(args.strategies) if args.strategies else list(STRATEGY_REGISTRY.keys())
    unknown = [s for s in requested_strategies if s not in STRATEGY_REGISTRY]
    if unknown:
        raise SystemExit(
            f"unknown strategies: {unknown}. "
            f"registered: {sorted(STRATEGY_REGISTRY.keys())}"
        )

    config = _build_config(args.commission_bps, args.slippage_bps, args.tax_bps)
    start_dt = datetime.fromisoformat(args.start)
    end_dt   = datetime.fromisoformat(args.end)

    # 1) 데이터 로드 단계.
    load_results = []
    for sym in symbols:
        r = load_real_ohlcv(
            sym, start=start_dt, end=end_dt,
            enable_yfinance=bool(args.enable_yfinance),
        )
        load_results.append(r)

    # 2) 데이터 있는 symbol 만 백테스트 매트릭스 실행.
    candidate_inputs: list[CandidateInput] = []
    per_run_records: list[dict[str, Any]] = []

    for load_res in load_results:
        if load_res.bars is None:
            per_run_records.append({
                "symbol":      load_res.symbol,
                "data_status": load_res.status.value,
                "reason":      load_res.reason,
                "runs":        [],
            })
            continue

        runs_for_symbol: list[dict[str, Any]] = []
        for sname in requested_strategies:
            single = _single_run(
                strategy_name=sname,
                symbol=load_res.symbol,
                bars=load_res.bars,
                initial_cash=int(args.initial_cash),
                quantity=int(args.quantity),
                config=config,
            )
            if not single.get("ok"):
                runs_for_symbol.append({
                    "strategy":    sname,
                    "error":       single.get("error", "unknown"),
                    "verdict":     BacktestVerdict.INSUFFICIENT_DATA.value,
                    "reasons":     ["engine_or_strategy_error"],
                })
                continue

            metrics = single["metrics"]
            classification = classify_backtest_result(
                metrics,
                thresholds=FilterThresholds(),
            )

            runs_for_symbol.append({
                "strategy":   sname,
                "metrics":    metrics,
                "verdict":    classification.verdict.value,
                "reasons":    classification.reasons,
                "params":     single["params"],
            })

            candidate_inputs.append(CandidateInput(
                strategy=sname,
                symbol=load_res.symbol,
                params=single["params"],
                risk_metrics=metrics,
                validation_status=classification.verdict,
                reasons=classification.reasons,
                score=float(metrics.get("risk_adjusted_score", 0.0) or 0.0),
                extra={
                    "data_source": load_res.source,
                    "data_status": load_res.status.value,
                    "bar_count":   load_res.bar_count,
                },
            ))

        per_run_records.append({
            "symbol":      load_res.symbol,
            "data_status": load_res.status.value,
            "reason":      load_res.reason,
            "runs":        runs_for_symbol,
        })

    # 3) paper_candidate config 빌드.
    metadata = {
        "pipeline":       "step3-real-data-backtest",
        "config": {
            "initial_cash":     args.initial_cash,
            "quantity":         args.quantity,
            "commission_bps":   args.commission_bps,
            "tax_bps":          args.tax_bps,
            "slippage_bps":     args.slippage_bps,
            "start":            args.start,
            "end":              args.end,
        },
        "data_summary":   summarize_load_results(load_results),
        "strategies":     requested_strategies,
        "symbols":        symbols,
        "stress_scenarios_prepared": list_stress_scenarios(),
    }
    paper_config = build_paper_candidate_config(
        candidate_inputs,
        top_k=int(args.top_k),
        metadata=metadata,
    )

    return {
        "load_results":  [
            {
                "symbol":     r.symbol,
                "status":     r.status.value,
                "reason":     r.reason,
                "source":     r.source,
                "bar_count":  r.bar_count,
            }
            for r in load_results
        ],
        "per_symbol":     per_run_records,
        "candidate_count": paper_config.candidate_count,
        "paper_candidate_config": paper_config,
    }


def _write_outputs(
    result: dict[str, Any], output_dir: Path,
) -> dict[str, Path]:
    """JSON / markdown 산출물 작성. paper_candidate_config.json 우선."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    # paper_candidate_config.json — 가장 중요한 산출물.
    paper_path = output_dir / "paper_candidate_config.json"
    paper_config = result["paper_candidate_config"]
    written["paper_candidate_config"] = write_paper_candidate_config(
        paper_config, paper_path,
    )

    # 전체 백테스트 결과 — 운영자 검토용.
    full_path = output_dir / "real_data_backtest_summary.json"
    summary_payload = {
        "load_results": result["load_results"],
        "per_symbol":   result["per_symbol"],
        "candidate_count":  result["candidate_count"],
    }
    full_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    written["summary"] = full_path

    return written


def _stdout_summary(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3] real-data backtest pipeline summary")
    lines.append("=" * 72)
    lines.append(f"candidate_count: {result['candidate_count']}")
    lines.append("")
    lines.append("data load status:")
    for r in result["load_results"]:
        lines.append(
            f"  {r['symbol']}: {r['status']:14s} bars={r['bar_count']:6d}  "
            f"src={r['source']}  reason={r['reason']}"
        )
    paper_dict = result["paper_candidate_config"].to_dict()
    lines.append("")
    if paper_dict["candidate_count"] == 0:
        lines.append("paper candidate: NONE. reasons:")
        for r in paper_dict["reasons_no_candidate"]:
            lines.append(f"  - {r}")
    else:
        lines.append("paper candidates:")
        for c in paper_dict["candidates"]:
            lines.append(
                f"  - {c['strategy']} / {c['symbol']}  "
                f"score={c['score']:.4f}  verdict={c['validation_status']}"
            )
    lines.append("")
    lines.append("REQUIRED metric keys (13):")
    lines.append("  " + ", ".join(REQUIRED_METRIC_KEYS))
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)

    result = run_pipeline(args)
    print(_stdout_summary(result))

    if args.dry_run:
        _log.info("dry-run — file output skipped.")
        return 0

    out_dir = Path(args.output_dir)
    written = _write_outputs(result, out_dir)
    for k, p in written.items():
        _log.info("wrote %s → %s", k, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
