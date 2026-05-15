"""Paper Candidate Picker — 백테스트 결과에서 Paper 운용 후보 추출.

본 모듈은 *advisory* — 통과한 후보를 paper_candidate_config.json 에 carry할
뿐, *실거래 자동 활성화 / promotion 자동 적용* 0건. 실 paper 진입은 운영자가
PR / Approval 큐를 통해 수행한다 (CLAUDE.md #41 Manual Approval).

CLAUDE.md 정적 grep 가드: broker / OrderExecutor / route_order / paper_trader /
`app.ai.assist` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.optimization.optimizer import OptimizationResult


# ----------------------------------------------------------------------
# 후보 기준
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PaperCandidateCriteria:
    """Paper 후보 진입 임계값.

    기본값은 *보수적* — 모두 통과해야 후보 자격. 운영자가 별도 PR 에서만 임계
    완화 가능.
    """
    min_trade_count:            int   = 5
    min_win_rate:               float = 0.40
    min_expectancy:             float = 0.0
    min_profit_factor:          float = 1.20
    max_consecutive_losses:     int   = 5
    max_loss_concentration:     float = 0.80
    # max_drawdown 절대값 임계 — 거래 quantity / cost basis 에 의존하므로
    # 0 으로 두면 검사 skip (운영자가 명시적으로 입력).
    max_drawdown_threshold:     int   = 0


@dataclass(frozen=True)
class PaperCandidate:
    """단일 후보의 평가 결과 + 통과/탈락 사유.

    *주문 신호 아님* — 단순히 paper_trader 후보로 *선정 가능*함을 표시.
    """
    strategy_id:        str
    params:             dict[str, Any]
    passed:             bool
    pass_reasons:       tuple[str, ...] = field(default_factory=tuple)
    fail_reasons:       tuple[str, ...] = field(default_factory=tuple)
    overfit_suspected:  bool = False
    # 원본 metrics carry (read-only — 후속 분석용).
    metrics:            dict[str, Any] = field(default_factory=dict)
    # invariants
    is_order_signal:    bool = False
    auto_apply_allowed: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("PaperCandidate.is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("PaperCandidate.auto_apply_allowed must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id":        self.strategy_id,
            "params":             dict(self.params),
            "passed":             self.passed,
            "pass_reasons":       list(self.pass_reasons),
            "fail_reasons":       list(self.fail_reasons),
            "overfit_suspected":  self.overfit_suspected,
            "metrics":            dict(self.metrics),
            "is_order_signal":    self.is_order_signal,
            "auto_apply_allowed": self.auto_apply_allowed,
        }


# ----------------------------------------------------------------------
# 평가
# ----------------------------------------------------------------------


def _evaluate(
    r: OptimizationResult,
    criteria: PaperCandidateCriteria,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """단일 OptimizationResult 가 criteria 를 통과하는지 평가.

    반환: (passed, pass_reasons, fail_reasons).
    """
    pass_reasons: list[str] = []
    fail_reasons: list[str] = []

    if r.trade_count < criteria.min_trade_count:
        fail_reasons.append(
            f"trade_count={r.trade_count} < min={criteria.min_trade_count}"
        )
    else:
        pass_reasons.append(f"trade_count OK ({r.trade_count})")

    if r.win_rate < criteria.min_win_rate:
        fail_reasons.append(
            f"win_rate={r.win_rate:.2f} < min={criteria.min_win_rate}"
        )
    else:
        pass_reasons.append(f"win_rate OK ({r.win_rate:.2f})")

    if r.expectancy <= criteria.min_expectancy:
        fail_reasons.append(
            f"expectancy={r.expectancy:.1f} <= min={criteria.min_expectancy}"
        )
    else:
        pass_reasons.append(f"expectancy positive ({r.expectancy:.1f})")

    if r.profit_factor is None or r.profit_factor < criteria.min_profit_factor:
        pf_str = "n/a" if r.profit_factor is None else f"{r.profit_factor:.2f}"
        fail_reasons.append(
            f"profit_factor={pf_str} < min={criteria.min_profit_factor}"
        )
    else:
        pass_reasons.append(f"profit_factor OK ({r.profit_factor:.2f})")

    if r.max_consecutive_losses > criteria.max_consecutive_losses:
        fail_reasons.append(
            f"max_consecutive_losses={r.max_consecutive_losses} "
            f"> max={criteria.max_consecutive_losses}"
        )
    else:
        pass_reasons.append(
            f"max_consecutive_losses OK ({r.max_consecutive_losses})"
        )

    if r.loss_concentration > criteria.max_loss_concentration:
        fail_reasons.append(
            f"loss_concentration={r.loss_concentration:.2f} "
            f"> max={criteria.max_loss_concentration}"
        )
    else:
        pass_reasons.append(
            f"loss_concentration OK ({r.loss_concentration:.2f})"
        )

    if criteria.max_drawdown_threshold > 0:
        if abs(r.max_drawdown) > criteria.max_drawdown_threshold:
            fail_reasons.append(
                f"max_drawdown={r.max_drawdown} "
                f"> max={criteria.max_drawdown_threshold}"
            )
        else:
            pass_reasons.append(f"max_drawdown OK ({r.max_drawdown})")

    return (len(fail_reasons) == 0, tuple(pass_reasons), tuple(fail_reasons))


def _detect_overfit_suspicion(
    sid_results: list[OptimizationResult],
    winner: OptimizationResult,
) -> bool:
    """같은 strategy_id 내 winner 가 cluster 평균보다 지나치게 우월한지.

    단일 param 조합만 양호하고 *주변 조합* 들이 모두 부진하면 과최적화 의심.
    기준: winner.expectancy > 2.0 × median(others.expectancy) 이고
          winner.profit_factor > 2.0 × median(others.profit_factor).
    """
    others = [r for r in sid_results if r is not winner]
    if not others:
        return False
    exps = [r.expectancy for r in others]
    pfs = [r.profit_factor or 0.0 for r in others]
    if not exps or not pfs:
        return False

    med_exp = sorted(exps)[len(exps) // 2]
    med_pf = sorted(pfs)[len(pfs) // 2]

    return (
        winner.expectancy > 2.0 * max(med_exp, 1.0)
        and (winner.profit_factor or 0.0) > 2.0 * max(med_pf, 0.5)
    )


def pick_paper_candidates(
    results_by_strategy: dict[str, list[OptimizationResult]],
    criteria: PaperCandidateCriteria | None = None,
) -> list[PaperCandidate]:
    """각 strategy 의 winner (expectancy 최대) 를 후보로 평가.

    winner 1개 / strategy. 동률이면 trade_count 가 큰 것 선택.
    """
    c = criteria or PaperCandidateCriteria()
    out: list[PaperCandidate] = []
    for sid, results in results_by_strategy.items():
        if not results:
            continue
        winner = max(
            results,
            key=lambda r: (r.expectancy, r.trade_count, r.profit_factor or 0),
        )
        passed, prs, frs = _evaluate(winner, c)
        overfit = _detect_overfit_suspicion(results, winner)
        out.append(
            PaperCandidate(
                strategy_id=sid,
                params=dict(winner.params),
                passed=passed and not overfit,
                pass_reasons=prs,
                fail_reasons=(
                    frs + (("overfit_suspected",) if overfit else ())
                ),
                overfit_suspected=overfit,
                metrics={
                    "trade_count":            winner.trade_count,
                    "win_rate":               winner.win_rate,
                    "expectancy":             winner.expectancy,
                    "profit_factor":          winner.profit_factor,
                    "total_pnl":              winner.total_pnl,
                    "max_drawdown":           winner.max_drawdown,
                    "max_consecutive_losses": winner.max_consecutive_losses,
                    "loss_concentration":     winner.loss_concentration,
                },
            )
        )
    return out


def rank_results(
    results_by_strategy: dict[str, list[OptimizationResult]],
) -> list[dict[str, Any]]:
    """전체 결과를 평탄화해 expectancy 내림차순 순위표 생성.

    CSV 직렬화용 — strategy_ranking.csv 입력.
    """
    rows: list[dict[str, Any]] = []
    for sid, results in results_by_strategy.items():
        for r in results:
            rows.append({
                "strategy_id":            sid,
                "params":                 r.params,
                "trade_count":            r.trade_count,
                "win_rate":               r.win_rate,
                "expectancy":             r.expectancy,
                "profit_factor":          r.profit_factor,
                "total_pnl":              r.total_pnl,
                "max_drawdown":           r.max_drawdown,
                "max_consecutive_losses": r.max_consecutive_losses,
                "loss_concentration":     r.loss_concentration,
            })
    rows.sort(
        key=lambda row: (
            row["expectancy"],
            row["trade_count"],
            row["profit_factor"] or 0,
        ),
        reverse=True,
    )
    return rows
