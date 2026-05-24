#!/usr/bin/env python3
"""STRATEGY-VALIDATION-01 — 전략 가능성 종합 평가 CLI (advisory, read-only).

백테스트 / Walk-forward / Stress / Paper 성과 결과를 종합해 전략 가능성을 5단계
(STRONG_CANDIDATE / CAUTIOUS_CANDIDATE / RESEARCH_ONLY / NOT_READY / BLOCKED) 로
*평가* 한다.

- `--run-sample` (기본): sample fixture 로 backtest + walk-forward + stress 를 in-process
  실행 후 종합 (실데이터 아님 → STRONG 불가, 기능 확인용).
- 또는 사전 생성된 리포트 JSON 을 `--backtest-json` / `--walk-forward-json` /
  `--stress-json` / `--paper-json` / `--order-quality-json` / `--feedback-json` 로 주입.

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS API 호출 0건. 실/모의 주문 0건.
- 결과가 좋아도 자동 적용 / 실전 전환 / live authorization 0건. secret/계좌 원문 0건.

exit code:
    0: 평가 완료 (STRONG/CAUTIOUS/RESEARCH_ONLY/NOT_READY)
    1: BLOCKED (치명/안전 문제)
    2: 실행 오류
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.system.strategy_potential import (  # noqa: E402
    BLOCKED,
    StrategyPotentialInputs,
    evaluate_strategy_potential,
    render_markdown,
    to_dict,
)

DEFAULT_OUTPUT_DIR = "reports/strategy_validation"
DEFAULT_SAMPLE_CSV = str(
    _REPO_ROOT / "backend" / "tests" / "fixtures" / "backtest" / "sample_ohlcv.csv")


def _load_json(path: str | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run_sample_reports(csv_path: str) -> tuple[dict, dict, dict]:
    """sample fixture 로 backtest / walk-forward / stress 를 in-process 실행."""
    from app.backtest.strategy_council_backtest import (
        BacktestInput,
        load_ohlcv_from_csv,
        run_strategy_council_backtest,
        summarize_backtest_report,
    )
    from app.backtest.walk_forward_validation import (
        WalkForwardInput,
        run_walk_forward_validation,
        summarize_walk_forward_report,
    )
    from app.stress_test.agent_stress_test import (
        run_agent_stress_test,
        summarize_stress_report,
    )

    bars = load_ohlcv_from_csv(csv_path)
    bt = summarize_backtest_report(
        run_strategy_council_backtest(BacktestInput(bars=tuple(bars))))
    try:
        wf = summarize_walk_forward_report(
            run_walk_forward_validation(WalkForwardInput(bars=tuple(bars))))
    except Exception:  # noqa: BLE001  (sample 표본 부족 시 insufficient 처리)
        wf = {"insufficient_data": True, "reason_code": "WALK_FORWARD_INSUFFICIENT_DATA"}
    st = summarize_stress_report(run_agent_stress_test())
    return bt, wf, st


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="전략 가능성 종합 평가 (advisory, 실전 아님).")
    p.add_argument("--run-sample", action="store_true",
                   help="sample fixture 로 backtest/WF/stress in-process 실행 후 종합")
    p.add_argument("--sample-csv", default=DEFAULT_SAMPLE_CSV)
    p.add_argument("--backtest-json", default=None)
    p.add_argument("--walk-forward-json", default=None)
    p.add_argument("--stress-json", default=None)
    p.add_argument("--paper-json", default=None,
                   help="Paper 성과 stats JSON (evaluated_trades/trading_days/win_rate/expectancy 등)")
    p.add_argument("--order-quality-json", default=None)
    p.add_argument("--feedback-json", default=None)
    p.add_argument("--has-real-data", action="store_true",
                   help="입력이 실/준실제 데이터임 (미지정 = sample fixture, STRONG 불가)")
    p.add_argument("--output", default=None, help="JSON 리포트 경로")
    p.add_argument("--markdown", default=None, help="Markdown 리포트 경로")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        args = _parse_args(argv)

        bt = _load_json(args.backtest_json)
        wf = _load_json(args.walk_forward_json)
        st = _load_json(args.stress_json)

        # 아무 JSON 도 없고 --run-sample 면 (혹은 기본) sample 실행.
        if bt is None and wf is None and st is None and (
                args.run_sample or not any([args.paper_json, args.order_quality_json])):
            bt, wf, st = _run_sample_reports(args.sample_csv)

        inp = StrategyPotentialInputs(
            backtest=bt,
            walk_forward=wf,
            stress=st,
            paper=_load_json(args.paper_json),
            order_quality=_load_json(args.order_quality_json),
            feedback=_load_json(args.feedback_json),
            has_real_data=bool(args.has_real_data),
        )
        report = evaluate_strategy_potential(
            inp, generated_at=datetime.now(timezone.utc).isoformat())

        data = to_dict(report)
        md = render_markdown(report)

        out = Path(args.output) if args.output else (
            Path(DEFAULT_OUTPUT_DIR) / "strategy_potential.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] JSON 리포트: {out}")
        if args.markdown:
            mdp = Path(args.markdown)
            mdp.parent.mkdir(parents=True, exist_ok=True)
            mdp.write_text(md, encoding="utf-8")
            print(f"[OK] Markdown 리포트: {mdp}")

        if not args.quiet:
            print(f"overall_verdict={report.overall_verdict} "
                  f"score={report.overall_strategy_potential_score} "
                  f"paper_sample={report.paper_sample_class}")
            print(f"agent_value={report.agent_value_verdict} "
                  f"sample_fixture_only={report.sample_fixture_only}")
            print("NOTE: 전략 가능성 평가 전용 — 자동 적용 / 실전 전환 / 주문 0건. 수익 보장 아님.")

        return 1 if report.overall_verdict == BLOCKED else 0
    except FileNotFoundError as e:
        print(f"[ERROR] 파일 없음: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
