#!/usr/bin/env python3
"""3-04 — Walk-forward 검증 CLI.

입력 모드 (mutually exclusive):
1. ``--from-paper-config <PATH>`` — 3-03 의 paper_candidate_config.json 읽기.
   각 candidate 의 (strategy, symbol, params) 조합을 walk-forward 평가.
2. ``--strategy <NAME> --symbol <CODE>`` — 단일 (strategy, symbol) 평가 (params 는
   strategy default).
3. 인자 모두 생략 — 대표 종목 10종 × 6 전략 default 파라미터로 평가.

각 (strategy, symbol, params) 에 대해 :func:`evaluate_walk_forward` 가:
- N folds train/validation 백테스트 실행.
- 4단계 verdict (HEALTHY / OVERFIT_RISK / UNDERFIT / INSUFFICIENT_DATA) 분류.
- 평균 expectancy + per-fold metric carry.

산출물 (default ``reports/walk_forward/``):
- ``walk_forward_summary.json``  per_candidate 결과 + verdict + folds.
- ``walk_forward_ranking.csv``   verdict 정렬 (HEALTHY 위, OVERFIT_RISK 아래).
- ``walk_forward_report.md``     운영자 검토용 markdown.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 실거래 / Place Order 0건.
- ``ENABLE_LIVE_TRADING`` / ``ENABLE_AI_EXECUTION`` /
  ``ENABLE_FUTURES_LIVE_TRADING`` / ``KIS_IS_PAPER`` 변경 0건.
- secret / API key / 계좌번호 / ``.env`` 노출 0건.
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


from app.analytics.walk_forward import (  # noqa: E402
    CandidateInputRecord,
    WalkForwardConfig,
    WalkForwardMode,
    WalkForwardVerdict,
    evaluate_walk_forward,
    read_candidates_from_paper_config,
)
from app.backtest.real_data import REPRESENTATIVE_SYMBOLS  # noqa: E402
from app.backtest.real_data.loader import (  # noqa: E402
    LoadStatus, load_real_ohlcv, summarize_load_results,
)
from app.backtest.types import BacktestConfig  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY  # noqa: E402


_log = logging.getLogger("autotrade.walk_forward_cli")


DEFAULT_OUTPUT_DIR = "reports/walk_forward"
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


def _build_wf_config(args: argparse.Namespace) -> WalkForwardConfig:
    mode_map = {"rolling": WalkForwardMode.ROLLING, "expanding": WalkForwardMode.EXPANDING}
    return WalkForwardConfig(
        mode=mode_map[args.mode],
        train_days=int(args.train_days),
        validation_days=int(args.validation_days),
        holdout_days=int(args.holdout_days),
        step_days=int(args.step_days),
        min_folds=int(args.min_folds),
        overfit_ratio=float(args.overfit_ratio),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3-04 — Walk-forward 검증 (train/validation 분리 + OVERFIT_RISK 탐지)",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--from-paper-config", default=None,
                   help="3-03 paper_candidate_config.json 경로 (있으면 우선).")
    p.add_argument("--strategy", default=None,
                   help="단일 전략 평가 (--symbol 와 함께 사용).")
    p.add_argument("--symbol", action="append", default=None,
                   help="대상 종목 (반복 가능). 생략 + paper-config 미사용 시 대표 10종.")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end",   default=DEFAULT_END)
    p.add_argument("--initial-cash",   type=int, default=10_000_000)
    p.add_argument("--quantity",       type=int, default=10)
    p.add_argument("--commission-bps", type=int, default=15)
    p.add_argument("--tax-bps",        type=int, default=23)
    p.add_argument("--slippage-bps",   type=int, default=5)
    p.add_argument("--enable-yfinance", action="store_true")
    # WalkForwardConfig
    p.add_argument("--mode", choices=["rolling", "expanding"], default="rolling")
    p.add_argument("--train-days",      type=int,   default=60)
    p.add_argument("--validation-days", type=int,   default=20)
    p.add_argument("--holdout-days",    type=int,   default=0)
    p.add_argument("--step-days",       type=int,   default=20)
    p.add_argument("--min-folds",       type=int,   default=3)
    p.add_argument("--overfit-ratio",   type=float, default=0.5)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _resolve_candidates(args: argparse.Namespace) -> list[CandidateInputRecord]:
    """입력 모드에 따라 평가 대상 후보 리스트 산정."""
    if args.from_paper_config:
        path = Path(args.from_paper_config)
        if not path.exists():
            raise SystemExit(f"paper config not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return read_candidates_from_paper_config(payload)

    symbols = list(args.symbol) if args.symbol else [s.symbol for s in REPRESENTATIVE_SYMBOLS]
    if args.strategy:
        if args.strategy not in STRATEGY_REGISTRY:
            raise SystemExit(
                f"unknown strategy: {args.strategy}. "
                f"registered: {sorted(STRATEGY_REGISTRY.keys())}"
            )
        return [
            CandidateInputRecord(strategy=args.strategy, symbol=s, params={}, score=0.0)
            for s in symbols
        ]
    # 기본: 6 전략 × N symbol (default params).
    out: list[CandidateInputRecord] = []
    for sname in STRATEGY_REGISTRY.keys():
        for sym in symbols:
            out.append(CandidateInputRecord(
                strategy=sname, symbol=sym, params={}, score=0.0,
            ))
    return out


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    """walk-forward 매트릭스 실행."""
    bt_cfg = _build_bt_config(args.commission_bps, args.slippage_bps, args.tax_bps)
    wf_cfg = _build_wf_config(args)
    start_dt = datetime.fromisoformat(args.start)
    end_dt   = datetime.fromisoformat(args.end)

    candidates = _resolve_candidates(args)

    # 데이터 로드 — symbol 별 1회만 (캐시).
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
            entry = {
                "strategy": c.strategy,
                "symbol":   c.symbol,
                "params":   c.params,
                "score":    c.score,
                "verdict":  WalkForwardVerdict.INSUFFICIENT_DATA.value,
                "reasons":  [f"data_status={load_res.status.value if load_res else 'NO_DATA'}"],
                "data_status": load_res.status.value if load_res else "NO_DATA",
                "fold_count":  0,
                "folds":       [],
            }
            results.append(entry)
            verdict_counts[entry["verdict"]] = verdict_counts.get(entry["verdict"], 0) + 1
            continue

        try:
            wf_result = evaluate_walk_forward(
                bars=load_res.bars,
                strategy_name=c.strategy,
                params=c.params,
                config=wf_cfg,
                initial_cash=int(args.initial_cash),
                quantity=int(args.quantity),
                bt_config=bt_cfg,
            )
        except Exception as exc:  # noqa: BLE001 — strategy build 실패 등.
            entry = {
                "strategy": c.strategy,
                "symbol":   c.symbol,
                "params":   c.params,
                "score":    c.score,
                "verdict":  WalkForwardVerdict.INSUFFICIENT_DATA.value,
                "reasons":  [f"evaluation_failed: {type(exc).__name__}: {exc}"],
                "data_status": load_res.status.value,
                "fold_count":  0,
                "folds":       [],
            }
            results.append(entry)
            verdict_counts[entry["verdict"]] = verdict_counts.get(entry["verdict"], 0) + 1
            continue

        d = wf_result.to_dict()
        d["symbol"]      = c.symbol
        d["score"]       = c.score
        d["data_status"] = load_res.status.value
        results.append(d)
        verdict_counts[d["verdict"]] = verdict_counts.get(d["verdict"], 0) + 1

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
        "walk_forward_config": wf_cfg.to_dict(),
        "data_summary":   summarize_load_results(list(symbol_to_load.values())),
        "candidate_count": len(candidates),
        "verdict_counts": verdict_counts,
        "results":        results,
        # 최상위 invariant — 분석 라벨, 자동 promotion 의미 X.
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
    }
    return payload


def _write_outputs(payload: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    json_path = output_dir / "walk_forward_summary.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    written["summary"] = json_path

    csv_path = output_dir / "walk_forward_ranking.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "strategy", "symbol", "params_json", "verdict",
            "fold_count", "train_expectancy_avg", "val_expectancy_avg",
        ])
        # verdict 정렬 — HEALTHY > OVERFIT_RISK > UNDERFIT > INSUFFICIENT_DATA.
        rank = {
            WalkForwardVerdict.HEALTHY.value:           0,
            WalkForwardVerdict.OVERFIT_RISK.value:      1,
            WalkForwardVerdict.UNDERFIT.value:          2,
            WalkForwardVerdict.INSUFFICIENT_DATA.value: 3,
        }
        sorted_results = sorted(
            payload["results"],
            key=lambda r: (rank.get(r["verdict"], 9), -float(r.get("val_expectancy_avg", 0) or 0)),
        )
        for r in sorted_results:
            params_str = json.dumps(r.get("params", {}), ensure_ascii=False, sort_keys=True)
            w.writerow([
                r.get("strategy"), r.get("symbol"), params_str, r.get("verdict"),
                r.get("fold_count", 0),
                r.get("train_expectancy_avg", 0.0),
                r.get("val_expectancy_avg", 0.0),
            ])
    written["ranking_csv"] = csv_path

    md_path = output_dir / "walk_forward_report.md"
    md_path.write_text(_build_markdown(payload), encoding="utf-8")
    written["report_md"] = md_path

    return written


def _build_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Step 3-04 -- Walk-forward validation report")
    lines.append("")
    lines.append(
        "> 본 리포트는 *분석 자료* 입니다. **투자 조언이 아닙니다.** "
        "OVERFIT_RISK 라벨은 *후보 자격 검토 박탈* 권고 — 자동 promotion 변경 / "
        "자동 비활성 의미 X."
    )
    lines.append("")
    lines.append("## Walk-forward config")
    cfg = payload["walk_forward_config"]
    for k, v in cfg.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Verdict 분포")
    for verdict, count in sorted(payload["verdict_counts"].items()):
        lines.append(f"- {verdict}: {count}")
    lines.append("")
    lines.append("## Verdict 별 결과")
    rank = {
        WalkForwardVerdict.HEALTHY.value:           0,
        WalkForwardVerdict.OVERFIT_RISK.value:      1,
        WalkForwardVerdict.UNDERFIT.value:          2,
        WalkForwardVerdict.INSUFFICIENT_DATA.value: 3,
    }
    sorted_results = sorted(
        payload["results"],
        key=lambda r: (rank.get(r["verdict"], 9), -float(r.get("val_expectancy_avg", 0) or 0)),
    )
    lines.append("| strategy | symbol | params | verdict | folds | train_avg | val_avg |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in sorted_results:
        params_str = ", ".join(f"{k}={v}" for k, v in r.get("params", {}).items()) or "(default)"
        lines.append(
            f"| {r.get('strategy')} | {r.get('symbol')} | {params_str} | "
            f"{r.get('verdict')} | {r.get('fold_count', 0)} | "
            f"{float(r.get('train_expectancy_avg', 0.0) or 0.0):.2f} | "
            f"{float(r.get('val_expectancy_avg', 0.0) or 0.0):.2f} |"
        )
    lines.append("")
    lines.append("## 다음 단계 (3-05 Stress test)")
    lines.append(
        "- HEALTHY verdict 후보만 stress test 진입 권장.\n"
        "- OVERFIT_RISK 후보는 *별도 PR* 로 grid 재정의 또는 후보 박탈.\n"
        "- INSUFFICIENT_DATA 후보는 데이터 추가 수집 / holdout / step 조정."
    )
    return "\n".join(lines)


def _stdout_summary(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3-04] walk-forward validation summary")
    lines.append("=" * 72)
    cfg = payload["walk_forward_config"]
    lines.append(
        f"mode={cfg['mode']} train_days={cfg['train_days']} "
        f"validation_days={cfg['validation_days']} "
        f"step_days={cfg['step_days']} min_folds={cfg['min_folds']}"
    )
    lines.append(f"total candidates: {payload['candidate_count']}")
    lines.append("")
    lines.append("verdict counts:")
    for v, c in sorted(payload["verdict_counts"].items()):
        lines.append(f"  {v:22s}: {c}")
    lines.append("")
    healthy = [r for r in payload["results"] if r["verdict"] == "HEALTHY"]
    if healthy:
        lines.append(f"HEALTHY candidates ({len(healthy)}):")
        for r in healthy[:10]:
            params_str = ", ".join(f"{k}={v}" for k, v in r.get("params", {}).items()) or "(default)"
            lines.append(
                f"  + {r['strategy']:20s} / {r['symbol']}  params=[{params_str}]  "
                f"train_avg={float(r['train_expectancy_avg'] or 0.0):.2f}  "
                f"val_avg={float(r['val_expectancy_avg'] or 0.0):.2f}"
            )
    else:
        lines.append("HEALTHY candidates: (none)")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    payload = run_validation(args)
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
