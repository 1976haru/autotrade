#!/usr/bin/env python3
"""3-08 — 운영자(비개발자)용 전략 최적화 Markdown 리포트 CLI.

3-02 ~ 3-07 산출물을 종합해 2개 markdown 생성:
- ``reports/strategy_optimization/strategy_optimization_report.md``  (12 섹션)
- ``reports/strategy_optimization/operator_summary.md``              (1 페이지 요약)

입력 경로 (모두 optional — 없으면 해당 단계 skip):
- ``--from-paper-candidate``  3-07 paper_candidate_config.json (우선)
- ``--from-backtest``         3-02 real_data_backtest_summary.json
- ``--from-optimization``     3-03 parameter_optimization_summary.json
- ``--from-walk-forward``     3-04 walk_forward_summary.json
- ``--from-stress-test``      3-05 stress_test_summary.json

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 실거래 / Place Order 0건.
- 안전 flag default 변경 0건.
- secret / API key / 계좌번호 / `.env` 노출 0건.
- 본 CLI 는 *advisory 리포트* 작성기 — paper trader 자동 시작 / 자동 실거래
  활성화를 *수행하지 않는다*. 모의투자 시작은 운영자가 BotControl /
  LiveEngine 흐름에서 *명시 수행*.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


from app.analytics.strategy_optimization_report import (  # noqa: E402
    ReportInputs,
    build_operator_report,
    render_full_markdown,
    render_summary_markdown,
    write_report_files,
)


_log = logging.getLogger("autotrade.strategy_optimization_report")


DEFAULT_OUTPUT_DIR = "reports/strategy_optimization"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="3-08 — 운영자용 전략 최적화 Markdown 리포트",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--from-paper-candidate", default=None,
                   help="3-07 paper_candidate_config.json 경로.")
    p.add_argument("--from-backtest", default=None,
                   help="3-02 real_data_backtest_summary.json 경로.")
    p.add_argument("--from-optimization", default=None,
                   help="3-03 parameter_optimization_summary.json 경로.")
    p.add_argument("--from-walk-forward", default=None,
                   help="3-04 walk_forward_summary.json 경로.")
    p.add_argument("--from-stress-test", default=None,
                   help="3-05 stress_test_summary.json 경로.")
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 X — stdout 요약만.")
    return p.parse_args(argv)


def run_report(args: argparse.Namespace) -> dict[str, Any]:
    """5 단계 산출물 종합 + OperatorReport 빌드 (DB write 0건)."""
    inputs = ReportInputs(
        paper_candidate_config_path=args.from_paper_candidate,
        backtest_summary_path=args.from_backtest,
        optimization_summary_path=args.from_optimization,
        walk_forward_summary_path=args.from_walk_forward,
        stress_test_summary_path=args.from_stress_test,
    )
    metadata = {
        "pipeline":       "step3-08-operator-report",
        "input_paths": {
            "paper_candidate":  args.from_paper_candidate,
            "backtest":         args.from_backtest,
            "optimization":     args.from_optimization,
            "walk_forward":     args.from_walk_forward,
            "stress_test":      args.from_stress_test,
        },
    }
    report = build_operator_report(inputs, metadata=metadata)
    return {
        "report":             report,
        "entry_count":        len(report.entries),
        "paper_ready_count":  report.paper_ready_count,
        "excluded_count":     report.excluded_count,
        "overall_status":     report.overall_status.value,
    }


def _stdout_summary(result: dict[str, Any]) -> str:
    report = result["report"]
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("[Step 3-08] strategy optimization operator report")
    lines.append("=" * 72)
    lines.append(f"overall_status:     {result['overall_status']}")
    lines.append(f"evaluated:          {result['entry_count']} strategy/symbol/params combos")
    lines.append(f"paper_ready_count:  {result['paper_ready_count']}")
    lines.append(f"excluded_count:     {result['excluded_count']}")
    lines.append("")
    if report.paper_candidates:
        lines.append("Paper candidates (모의투자 검토 가능):")
        for c in report.paper_candidates:
            lines.append(
                f"  + {c.display_name} ({c.strategy_id}) / {c.symbol} score={c.score:.4f}"
            )
    else:
        lines.append("(no paper candidate -- see reasons_no_candidate)")
        for r in report.reasons_no_candidate:
            lines.append(f"  - {r}")
    if report.ai_agent_risk_signals:
        lines.append("")
        lines.append("AI Agent 위험 신호 (참고용):")
        for sig in report.ai_agent_risk_signals[:10]:
            lines.append(f"  ! {sig}")
    lines.append("=" * 72)
    return "\n".join(lines)


def _write_outputs(result: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    return write_report_files(result["report"], output_dir)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    args = _parse_args(argv)
    result = run_report(args)
    print(_stdout_summary(result))

    if args.dry_run:
        _log.info("dry-run mode -- file output skipped.")
        # 추가로 dry-run 시 markdown 미리보기 stdout 출력.
        report = result["report"]
        print("\n--- summary.md preview ---\n")
        print(render_summary_markdown(report))
        return 0

    out_dir = Path(args.output_dir)
    written = _write_outputs(result, out_dir)
    for k, p in written.items():
        _log.info("wrote %s -> %s", k, p)
    # 미사용 import 회피용 — markdown 생성 함수가 본 모듈에서 호출 안 되지만
    # CLI 사용자가 import 해서 사용할 수 있게 re-export 의미로 명시 참조.
    _ = render_full_markdown
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
