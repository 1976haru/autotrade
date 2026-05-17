#!/usr/bin/env python3
"""3-07 — Paper 후보 통합 export CLI.

3-02 ~ 3-05 의 산출물을 종합해 모든 단계를 통과한 후보 상위 N (default 2) 를
단일 ``paper_candidate_config.json`` 으로 export.

입력 경로 (모두 optional — 없으면 해당 단계 skip):
- ``--from-backtest <PATH>``       3-02 ``real_data_backtest_summary.json``
- ``--from-optimization <PATH>``   3-03 ``parameter_optimization_summary.json``
- ``--from-walk-forward <PATH>``   3-04 ``walk_forward_summary.json``
- ``--from-stress-test <PATH>``    3-05 ``stress_test_summary.json``

산출물 (default ``reports/strategy_optimization/``):
- ``paper_candidate_config.json``  — 최종 paper 후보 (0건이어도 파일 생성).

후보 0건일 때:
- ``candidates: []`` + ``reasons_no_candidate`` 채워서 파일 생성.
- **억지로 후보 만들지 않음**.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 실거래 / Place Order 0건.
- 안전 flag default 변경 0건.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- PAPER 후보 export 가 자동 paper trader 시작 / 자동 실거래 활성화를
  *의미하지 않는다* — 운영자가 검토 후 *수동* 입력 (Paper Auto Loop).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


from app.analytics.paper_candidate_aggregator import (  # noqa: E402
    AggregationInputs,
    aggregate_candidates,
    build_paper_candidate_config,
    write_paper_candidate_config,
)


_log = logging.getLogger("autotrade.paper_candidate_aggregator")


DEFAULT_OUTPUT_DIR = "reports/strategy_optimization"
DEFAULT_TOP_K      = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3-07 -- Paper 후보 통합 export CLI",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--from-backtest", default=None,
                   help="3-02 real_data_backtest_summary.json 경로.")
    p.add_argument("--from-optimization", default=None,
                   help="3-03 parameter_optimization_summary.json 경로.")
    p.add_argument("--from-walk-forward", default=None,
                   help="3-04 walk_forward_summary.json 경로.")
    p.add_argument("--from-stress-test", default=None,
                   help="3-05 stress_test_summary.json 경로.")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                   help="paper 후보 상한 (default 2).")
    p.add_argument("--required-stages", nargs="*", default=None,
                   help="필수 통과 단계 (default 3-02 3-03 3-04 3-05).")
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 X — stdout 요약만.")
    return p.parse_args(argv)


def run_aggregation(args: argparse.Namespace) -> dict[str, Any]:
    """전체 단계 통합 + paper_candidate_config 빌드."""
    inputs = AggregationInputs(
        backtest_summary_path=args.from_backtest,
        optimization_summary_path=args.from_optimization,
        walk_forward_summary_path=args.from_walk_forward,
        stress_test_summary_path=args.from_stress_test,
    )
    required_stages = (
        set(args.required_stages) if args.required_stages
        else {"3-02", "3-03", "3-04", "3-05"}
    )

    aggregated = aggregate_candidates(inputs, required_stages=required_stages)

    metadata = {
        "pipeline":         "step3-07-paper-candidate-aggregator",
        "required_stages":  sorted(required_stages),
        "input_paths": {
            "backtest":      args.from_backtest,
            "optimization":  args.from_optimization,
            "walk_forward":  args.from_walk_forward,
            "stress_test":   args.from_stress_test,
        },
        "total_aggregated": len(aggregated),
    }

    config = build_paper_candidate_config(
        aggregated,
        required_stages=required_stages,
        top_k=int(args.top_k),
        metadata=metadata,
    )

    return {
        "config": config,
        "aggregated_count": len(aggregated),
        "candidate_count":  config.candidate_count,
    }


def _stdout_summary(result: dict[str, Any]) -> str:
    config = result["config"]
    d = config.to_dict()
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3-07] paper candidate aggregator summary")
    lines.append("=" * 72)
    lines.append(f"aggregated candidates (all stages combined): {result['aggregated_count']}")
    lines.append(f"final paper_candidate_count:                  {result['candidate_count']}")
    lines.append("")
    if d["candidate_count"] > 0:
        lines.append("Paper candidates (passed all required stages):")
        for c in d["candidates"]:
            params_str = ", ".join(f"{k}={v}" for k, v in c["params"].items()) or "(default)"
            lines.append(
                f"  + {c['strategy']:20s} / {c['symbol']}  params=[{params_str}]  "
                f"score={c['score']:.4f}  stages={c['passed_stages']}"
            )
    else:
        lines.append("(no paper candidate -- see reasons_no_candidate)")
        for r in d["reasons_no_candidate"]:
            lines.append(f"  - {r}")
    lines.append("=" * 72)
    return "\n".join(lines)


def _write_outputs(result: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "paper_candidate_config.json"
    written_path = write_paper_candidate_config(result["config"], out_path)
    return {"paper_candidate_config": written_path}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    result = run_aggregation(args)
    print(_stdout_summary(result))

    if args.dry_run:
        _log.info("dry-run mode -- file output skipped.")
        return 0

    out_dir = Path(args.output_dir)
    written = _write_outputs(result, out_dir)
    for k, p in written.items():
        _log.info("wrote %s -> %s", k, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
