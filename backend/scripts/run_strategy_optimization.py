"""Strategy Optimization & Paper Readiness CLI.

본 스크립트는 6개 주식 전략에 대해 그리드 서치 + 스트레스 테스트 + Paper
후보 선정을 수행하고 5개 출력 파일을 reports/ 디렉터리에 생성한다.

산출물:
1. strategy_optimization_summary.json
2. strategy_optimization_report.md
3. strategy_ranking.csv
4. paper_candidate_config.json
5. agent_strategy_recommendation.md

CLAUDE.md 절대 원칙:
- 실 주문 / 한투 API / broker.place_order() 호출 0건.
- 외부 데이터 fetch 0건 — deterministic synthetic bars 사용.
- LIVE / AI / FUTURES enable flag 변경 0건.

사용:
    python -m scripts.run_strategy_optimization
    python -m scripts.run_strategy_optimization --output-dir reports/2026-05-15
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 본 스크립트는 backend/scripts/ 에서 실행되므로 backend/ 를 path 에 추가.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.agents.paper_readiness_agent import (  # noqa: E402
    PaperReadinessAgent,
    evaluate_paper_readiness,
)
from app.agents.base import AgentContext  # noqa: E402
from app.backtest.types import Bar  # noqa: E402
from app.optimization.optimizer import OptimizationResult, grid_search_all  # noqa: E402
from app.optimization.paper_picker import (  # noqa: E402
    PaperCandidateCriteria,
    pick_paper_candidates,
    rank_results,
)
from app.optimization.param_space import supported_strategy_ids  # noqa: E402
from app.stress_test.runner import run_all_scenarios  # noqa: E402


# ----------------------------------------------------------------------
# 합성 데이터 — deterministic, 외부 fetch 0건
# ----------------------------------------------------------------------


def _synthetic_bars(symbol: str, n: int = 120, seed: int = 1) -> list[Bar]:
    """deterministic 합성 일중 봉. 외부 데이터 fetch 0건.

    `seed` 별로 가격 패턴이 약간 다름 — 전략별로 다른 시퀀스를 시뮬할 수 있다.
    추세 + 변동 + 가벼운 mean-reversion 혼합.
    """
    base_ts = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    price = 50_000 + (seed * 200)
    for i in range(n):
        # deterministic 패턴.
        trend = ((i % 30) - 15) * 20
        noise = ((i * 7 + seed * 3) % 11 - 5) * 30
        new_price = max(1000, price + trend + noise)
        open_p = price
        close_p = new_price
        high_p = max(open_p, close_p) + 50
        low_p = max(1, min(open_p, close_p) - 50)
        vol = 1000 + (i * 13 + seed * 17) % 500
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=base_ts + timedelta(minutes=i),
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=vol,
            )
        )
        price = new_price
    return bars


def _bars_for_strategies() -> dict[str, list[Bar]]:
    """6개 전략 각각에 deterministic bar 시퀀스 부여."""
    sids = supported_strategy_ids()
    return {
        sid: _synthetic_bars(f"S{i:02d}", n=120, seed=i + 1)
        for i, sid in enumerate(sids)
    }


# ----------------------------------------------------------------------
# 출력
# ----------------------------------------------------------------------


def _result_to_row(strategy_id: str, r: OptimizationResult) -> dict[str, Any]:
    return {
        "strategy_id":            strategy_id,
        "params":                 json.dumps(r.params, ensure_ascii=False, sort_keys=True),
        "trade_count":            r.trade_count,
        "win_rate":               round(r.win_rate, 4),
        "expectancy":             round(r.expectancy, 2),
        "profit_factor":          (
            None if r.profit_factor is None else round(r.profit_factor, 4)
        ),
        "total_pnl":              r.total_pnl,
        "max_drawdown":           r.max_drawdown,
        "max_consecutive_losses": r.max_consecutive_losses,
        "loss_concentration":     round(r.loss_concentration, 4),
    }


def write_ranking_csv(
    results_by_strategy: dict[str, list[OptimizationResult]],
    path: Path,
) -> None:
    rows = rank_results(results_by_strategy)
    # ensure header order
    field_order = [
        "strategy_id", "params", "trade_count", "win_rate", "expectancy",
        "profit_factor", "total_pnl", "max_drawdown",
        "max_consecutive_losses", "loss_concentration",
    ]
    # rank_results returns rows where "params" is dict — serialize for CSV.
    csv_rows = []
    for row in rows:
        new_row = dict(row)
        new_row["params"] = json.dumps(
            row["params"], ensure_ascii=False, sort_keys=True
        )
        csv_rows.append(new_row)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=field_order)
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)


def write_summary_json(
    results_by_strategy: dict[str, list[OptimizationResult]],
    candidates: list,
    stress_by_strategy: dict[str, list[dict]],
    path: Path,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisory_only": True,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "summary": {
            "total_strategies":   len(results_by_strategy),
            "total_combinations": sum(len(rs) for rs in results_by_strategy.values()),
            "recommended_count":  sum(1 for c in candidates if c.passed),
            "excluded_count":     sum(1 for c in candidates if not c.passed),
        },
        "results_by_strategy": {
            sid: [r.to_dict() for r in rs]
            for sid, rs in results_by_strategy.items()
        },
        "paper_candidates": [c.to_dict() for c in candidates],
        "stress_results": stress_by_strategy,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_optimization_report_md(
    results_by_strategy: dict[str, list[OptimizationResult]],
    candidates: list,
    stress_by_strategy: dict[str, list[dict]],
    path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Strategy Optimization Report")
    lines.append("")
    lines.append(
        f"_생성: {datetime.now(timezone.utc).isoformat()}_"
    )
    lines.append("")
    lines.append("> **중요 고지**: 본 리포트는 *시스템 운영 / 검증 / 개선* 자료이며 ")
    lines.append("> *투자 조언이 아니라* advisory 분석입니다. 실 paper 진입은 운영자 ")
    lines.append("> 명시 PR + Approval 큐를 거쳐야 하며, 본 리포트의 어떤 항목도 ")
    lines.append("> 자동으로 코드 / 파라미터에 반영되지 않습니다.")
    lines.append("")
    lines.append("## 1. 전략별 winner (expectancy 최대)")
    lines.append("")
    lines.append(
        "| strategy_id | params | trade_count | win_rate | expectancy | "
        "profit_factor | MDD | loss_conc |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in candidates:
        m = c.metrics
        pf = "n/a" if m.get("profit_factor") is None else f"{m['profit_factor']:.2f}"
        lines.append(
            f"| {c.strategy_id} | `{json.dumps(c.params, sort_keys=True)}` | "
            f"{m['trade_count']} | {m['win_rate']:.2f} | "
            f"{m['expectancy']:.1f} | {pf} | {m['max_drawdown']} | "
            f"{m['loss_concentration']:.2f} |"
        )
    lines.append("")
    lines.append("## 2. Paper 후보 판정")
    lines.append("")
    for c in candidates:
        status = "✅ PASS" if c.passed else "❌ EXCLUDE"
        overfit = " (⚠️ overfit 의심)" if c.overfit_suspected else ""
        lines.append(f"### {c.strategy_id} — {status}{overfit}")
        lines.append("")
        if c.pass_reasons:
            lines.append("**통과 항목:**")
            for r in c.pass_reasons:
                lines.append(f"- {r}")
            lines.append("")
        if c.fail_reasons:
            lines.append("**미통과 항목:**")
            for r in c.fail_reasons:
                lines.append(f"- {r}")
            lines.append("")

    lines.append("## 3. 스트레스 테스트 요약")
    lines.append("")
    lines.append(
        "| strategy_id | scenario | baseline_exp | stressed_exp | score | label |"
    )
    lines.append("|---|---|---|---|---|---|")
    for sid, results in stress_by_strategy.items():
        for r in results:
            lines.append(
                f"| {sid} | {r['scenario']} | {r['baseline_expectancy']:.1f} | "
                f"{r['stressed_expectancy']:.1f} | {r['stress_score']:.1f} | "
                f"{r['degradation_label']} |"
            )
    lines.append("")
    lines.append("## 4. 안전 invariant")
    lines.append("")
    lines.append("- 본 리포트는 *advisory* — 주문 신호 / 자동 적용 트리거 아님.")
    lines.append("- 실 paper 진입은 운영자 명시 PR + Approval 큐 (#41).")
    lines.append("- 외부 거래소 / 코인 거래 0건 — 주식 단타만.")
    lines.append("- broker / OrderExecutor / route_order 호출 0건.")
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_paper_candidate_config(
    candidates: list,
    path: Path,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisory_only": True,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "candidates": [
            {
                "strategy_id":      c.strategy_id,
                "suggested_params": c.params,
                "passed":           c.passed,
                "overfit_suspected": c.overfit_suspected,
                "notes": (
                    "운영자가 별도 PR + Approval 큐로 paper_trader 흐름에 plug. "
                    "본 파일을 직접 import 해 자동 활성화 금지."
                ),
            }
            for c in candidates if c.passed
        ],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_agent_recommendation_md(
    recommendations: list, path: Path
) -> None:
    lines: list[str] = []
    lines.append("# Agent Strategy Recommendation")
    lines.append("")
    lines.append(
        f"_생성: {datetime.now(timezone.utc).isoformat()}_"
    )
    lines.append("")
    lines.append("> **중요 고지**: 본 추천은 *Paper 운용 후보* 추천이며 *주문 ")
    lines.append("> 추천이 아닙니다*. 본 Agent 는 paper_trader / broker 어느 ")
    lines.append("> 것도 직접 호출하지 않으며, 추천 결과가 자동으로 paper 흐름 ")
    lines.append("> 에 반영되지 않습니다 (운영자 PR + Approval 큐 필요).")
    lines.append("")
    recommend = [r for r in recommendations if r.decision == "RECOMMEND_PAPER"]
    review    = [r for r in recommendations if r.decision == "REVIEW"]
    exclude   = [r for r in recommendations if r.decision == "EXCLUDE"]

    lines.append(f"## 추천 (RECOMMEND_PAPER) — {len(recommend)}건")
    lines.append("")
    for r in recommend:
        lines.append(f"### {r.strategy_id} — score {r.score:.1f}")
        lines.append("")
        lines.append(f"- 제안 params: `{json.dumps(r.suggested_params, sort_keys=True)}`")
        if r.overfit_warning:
            lines.append("- ⚠️ overfit 의심")
        if r.stress_concerns:
            lines.append("- 스트레스 우려:")
            for s in r.stress_concerns:
                lines.append(f"  - {s}")
        lines.append("")

    lines.append(f"## 검토 필요 (REVIEW) — {len(review)}건")
    lines.append("")
    for r in review:
        lines.append(
            f"- **{r.strategy_id}** score={r.score:.1f} "
            f"(overfit={r.overfit_warning})"
        )

    lines.append("")
    lines.append(f"## 제외 (EXCLUDE) — {len(exclude)}건")
    lines.append("")
    for r in exclude:
        lines.append(
            f"- **{r.strategy_id}** score={r.score:.1f}"
        )

    lines.append("")
    lines.append("## 안전 invariant")
    lines.append("")
    lines.append("- 본 추천은 Paper 후보 추천 — 주문 추천 아님.")
    lines.append("- `is_order_signal=False` / `auto_apply_allowed=False` 불변.")
    lines.append("- 실 paper 활성화는 운영자 PR + Approval 큐 필수.")
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strategy Optimization & Paper Readiness CLI"
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="출력 디렉터리 (기본 reports/, .gitignore 등록됨)",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. grid search
    bars_by_strategy = _bars_for_strategies()
    results_by_strategy = grid_search_all(bars_by_strategy)

    # 2. paper picker
    candidates = pick_paper_candidates(results_by_strategy)

    # 3. stress test (winner params 만)
    stress_results: dict[str, list[dict[str, Any]]] = {}
    stress_scores: dict[str, list[float]] = {}
    for c in candidates:
        sid = c.strategy_id
        bars = bars_by_strategy.get(sid, [])
        if not bars:
            continue
        srs = run_all_scenarios(sid, c.params, bars)
        stress_results[sid] = [sr.to_dict() for sr in srs]
        stress_scores[sid] = [sr.stress_score for sr in srs]

    # 4. agent recommendation
    recommendations = evaluate_paper_readiness(candidates, stress_scores)

    # 5. agent run (for completeness — output is also captured via metadata)
    agent = PaperReadinessAgent()
    agent_output = agent.run(
        AgentContext(
            extra={
                "paper_candidates":          candidates,
                "stress_scores_by_strategy": stress_scores,
            }
        )
    )

    # 6. write output files
    write_summary_json(
        results_by_strategy, candidates, stress_results,
        output_dir / "strategy_optimization_summary.json",
    )
    write_optimization_report_md(
        results_by_strategy, candidates, stress_results,
        output_dir / "strategy_optimization_report.md",
    )
    write_ranking_csv(
        results_by_strategy,
        output_dir / "strategy_ranking.csv",
    )
    write_paper_candidate_config(
        candidates,
        output_dir / "paper_candidate_config.json",
    )
    write_agent_recommendation_md(
        recommendations,
        output_dir / "agent_strategy_recommendation.md",
    )

    # 7. stdout summary
    recommend_count = sum(1 for r in recommendations if r.decision == "RECOMMEND_PAPER")
    review_count    = sum(1 for r in recommendations if r.decision == "REVIEW")
    exclude_count   = sum(1 for r in recommendations if r.decision == "EXCLUDE")
    print(f"output: {output_dir}/")
    print(f"  recommend: {recommend_count}")
    print(f"  review:    {review_count}")
    print(f"  exclude:   {exclude_count}")
    print(f"  agent_output.is_order_intent: {agent_output.is_order_intent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
