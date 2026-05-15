"""PaperReadinessAgent — 백테스트 + 스트레스 결과를 읽어 Paper 후보 추천.

CLAUDE.md 절대 원칙 (정적 grep 가드):
- 본 Agent 는 *advisory only* — 주문 추천이 아니라 **Paper 후보 추천**.
- broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
  `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` import 0건.
- DB write 0건. `OrderRequest` import / 생성 / annotation 0건.
- `PaperReadinessOutput.is_order_signal=False` / `auto_apply_allowed=False` 불변.
- 추천 결과는 *어떤 코드 / 파라미터에도 자동 반영되지 않는다* — 운영자가
  별도 PR 로 paper_trader 흐름에 plug 해야 함.

본 Agent 는 #55 StrategyResearcher 와 별개 — StrategyResearcher 는 *전략
개선 후보* 를 markdown 으로 제안하고, 본 Agent 는 *오늘 어떤 전략 조합을
Paper 로 돌릴지* 추천한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.optimization.paper_picker import PaperCandidate


@dataclass(frozen=True)
class PaperReadinessRecommendation:
    """전략별 Paper 운용 추천 결과.

    *주문 추천이 아니라 Paper 후보 추천* — 운영자가 PR / Approval 큐를
    통해서만 paper_trader 흐름에 활성화한다.
    """
    strategy_id:        str
    decision:           str         # "RECOMMEND_PAPER" / "EXCLUDE" / "REVIEW"
    score:              float       # 0-100
    reasons:            tuple[str, ...] = field(default_factory=tuple)
    suggested_params:   dict[str, Any]  = field(default_factory=dict)
    overfit_warning:    bool = False
    stress_concerns:    tuple[str, ...] = field(default_factory=tuple)
    is_order_signal:    bool = False
    auto_apply_allowed: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "PaperReadinessRecommendation.is_order_signal must be False"
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "PaperReadinessRecommendation.auto_apply_allowed must be False"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id":        self.strategy_id,
            "decision":           self.decision,
            "score":              self.score,
            "reasons":            list(self.reasons),
            "suggested_params":   dict(self.suggested_params),
            "overfit_warning":    self.overfit_warning,
            "stress_concerns":    list(self.stress_concerns),
            "is_order_signal":    self.is_order_signal,
            "auto_apply_allowed": self.auto_apply_allowed,
        }


def _compute_score(
    candidate: PaperCandidate, stress_scores: list[float]
) -> float:
    """0-100 종합 점수.

    rules:
    - 후보 미통과 → 0
    - 통과 + stress 평균이 70 이상 → 80~95
    - 통과 + stress 평균이 30~70 → 50~80
    - 통과 + stress 평균이 30 미만 → 20~50
    - overfit 의심 → 30 감점
    """
    if not candidate.passed:
        return 0.0
    avg_stress = sum(stress_scores) / len(stress_scores) if stress_scores else 50.0
    base = 50.0 + 0.45 * avg_stress
    if candidate.overfit_suspected:
        base -= 30.0
    # win_rate + expectancy 보정
    win_rate = float(candidate.metrics.get("win_rate", 0.0))
    base += 10.0 * (win_rate - 0.5)
    return float(max(0.0, min(100.0, base)))


def evaluate_paper_readiness(
    candidates: list[PaperCandidate],
    stress_scores_by_strategy: dict[str, list[float]] | None = None,
) -> list[PaperReadinessRecommendation]:
    """Paper 후보 + 스트레스 점수 → 전략별 추천.

    decision 라벨:
    - "RECOMMEND_PAPER": 통과 + 스트레스 양호 + overfit X (score >= 60)
    - "REVIEW":          통과지만 스트레스 우려 또는 overfit (score 30-60)
    - "EXCLUDE":         미통과 (score < 30 또는 candidate.passed=False)
    """
    stress_scores_by_strategy = stress_scores_by_strategy or {}
    out: list[PaperReadinessRecommendation] = []
    for c in candidates:
        scores = stress_scores_by_strategy.get(c.strategy_id, [])
        score = _compute_score(c, scores)
        if not c.passed:
            decision = "EXCLUDE"
        elif score >= 60.0:
            decision = "RECOMMEND_PAPER"
        elif score >= 30.0:
            decision = "REVIEW"
        else:
            decision = "EXCLUDE"

        # 스트레스 concerns 라벨 (점수 < 30 인 시나리오 이름은 caller 가 직접
        # mapping — 본 함수는 평균만 사용).
        stress_concerns: tuple[str, ...] = ()
        if scores and (sum(scores) / len(scores)) < 50.0:
            stress_concerns = (
                f"average stress_score={(sum(scores)/len(scores)):.1f} below 50",
            )

        out.append(
            PaperReadinessRecommendation(
                strategy_id=c.strategy_id,
                decision=decision,
                score=score,
                reasons=tuple(c.pass_reasons) + tuple(c.fail_reasons),
                suggested_params=dict(c.params),
                overfit_warning=c.overfit_suspected,
                stress_concerns=stress_concerns,
            )
        )
    return out


# ----------------------------------------------------------------------
# AgentBase 호환 wrapper
# ----------------------------------------------------------------------


class PaperReadinessAgent(AgentBase):
    """#51 AgentBase 호환 wrapper — STRATEGY_RESEARCHER 역할.

    `run(context)` 는 `context.extra` 에 다음 키 carry:
    - "paper_candidates":          list[PaperCandidate]
    - "stress_scores_by_strategy": dict[str, list[float]] (선택)

    *주문 신호 아님* — `is_order_intent=False` / `can_execute_order=False` 불변.
    """

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="paper_readiness",
            role=AgentRole.STRATEGY_RESEARCHER,
            description=(
                "백테스트 + 스트레스 결과를 읽어 Paper 운용 후보 추천. "
                "주문 추천이 아니라 Paper 후보 추천 — 실 paper 진입은 운영자 PR."
            ),
            inputs=[
                "paper_candidates (list[PaperCandidate])",
                "stress_scores_by_strategy (dict[str, list[float]])",
            ],
            outputs=[
                "PaperReadinessRecommendation[]",
                "summary text",
            ],
            forbidden=[
                "broker / OrderExecutor / route_order 호출 금지",
                "approval queue 직접 등록 금지",
                "paper_trader 직접 활성화 금지",
                "외부 AI / HTTP 호출 금지",
                "주문 신호 / 투자 조언 생성 금지 — Paper 후보 추천만",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        extra = context.extra or {}
        candidates: list[PaperCandidate] = extra.get("paper_candidates", [])
        stress_scores: dict[str, list[float]] = (
            extra.get("stress_scores_by_strategy", {}) or {}
        )
        recs = evaluate_paper_readiness(candidates, stress_scores)

        recommended = [r for r in recs if r.decision == "RECOMMEND_PAPER"]
        excluded    = [r for r in recs if r.decision == "EXCLUDE"]
        review      = [r for r in recs if r.decision == "REVIEW"]

        summary = (
            f"paper readiness review: {len(recommended)} recommended, "
            f"{len(review)} review, {len(excluded)} excluded."
        )

        reasons: list[str] = []
        for r in recommended:
            reasons.append(
                f"[RECOMMEND] {r.strategy_id} score={r.score:.1f}"
            )
        for r in review:
            reasons.append(
                f"[REVIEW] {r.strategy_id} score={r.score:.1f} "
                f"(overfit={r.overfit_warning})"
            )
        for r in excluded:
            reasons.append(
                f"[EXCLUDE] {r.strategy_id} score={r.score:.1f}"
            )

        return AgentOutput(
            role=AgentRole.STRATEGY_RESEARCHER,
            decision=AgentDecision.RECOMMEND if recommended else AgentDecision.REPORT,
            summary=summary,
            reasons=reasons,
            confidence=70 if recommended else 40,
            metadata={
                "recommendations": [r.to_dict() for r in recs],
                "advisory_only":   True,
                "is_order_signal": False,
            },
        )
