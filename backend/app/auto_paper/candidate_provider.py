"""#PaperCandidateWire: active_candidate → PaperStartExplanation provider.

Auto Paper Loop 가 RUNNING 중일 때 `agent_consumer.consume_agent_recommendations`
에 주입할 *deterministic provider* 를 생성한다. registry 에 APPROVED 후보가
없으면 None 반환 → consumer 가 skip.

본 모듈은 *adapter* — LLM / Anthropic / OpenAI 호출 0건, broker 호출 0건.
"""

from __future__ import annotations

from datetime import datetime

from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.auto_paper.candidate_registry import (
    CandidateRegistry,
    ManagedCandidate,
    get_candidate_registry,
)


def _build_explanation_from_candidate(
    managed: ManagedCandidate,
    now:     datetime,
) -> PaperStartExplanation:
    """`ManagedCandidate` → recommended PaperStartExplanation.

    rank=1 의 후보를 BUY recommended 로 carry. risk_flags 도 함께 전달해
    4-09 risk veto 가 동작.
    """
    c = managed.candidate
    # 첫 included_strategies 를 대표 strategy 로 사용 (combo 인 경우).
    strategy = (c.included_strategies[0] if c.included_strategies else c.name)
    risk_flags = list(c.risk_flags or [])
    rationale = (
        f"#PaperCandidateWire: 운영자({managed.approved_by})가 승인한 후보 "
        f"`{c.name}` — composite_score={c.composite_score:.4f}, "
        f"primary_regime={c.primary_regime}."
    )
    entry = StrategyExplanation(
        strategy=strategy,
        symbol=c.symbol,
        bucket="recommended",
        paper_candidate_status=c.paper_candidate_status,
        rationale_lines=[rationale] + list(c.recommended_reasons),
        risk_flags=risk_flags,
        regime_policy_role="preferred",
    )
    return PaperStartExplanation(
        generated_at=now.isoformat(),
        schema_version="1.0",
        verdict=ExplanationVerdict.READY_TO_REVIEW,
        recommended_explanations=[entry],
        watchlist_explanations=[],
        excluded_explanations=[],
        market_regime=c.primary_regime,
        regime_confidence=0.80,
        regime_reasons=[
            f"primary_regime={c.primary_regime} carried from 3-15 selection",
        ],
        regime_risk_flags=[],
        regime_allowed_tactics=list(c.included_tactics),
        regime_blocked_tactics=[],
        overfit_count=0,
        overfit_strategies=[],
        headline=(
            f"운영자 승인 후보 `{c.name}` — 운영자 명시 승인 후만 사용"
        ),
        risk_summary=list(c.risk_flags or []),
        operator_note=c.operator_note,
        next_actions=[
            "Paper Auto Loop tick — ledger / AgentDecisionLog 기록 advisory only",
            "실거래 활성화는 별도 PR + Live Manual Gate (#73) 통과 필요",
        ],
        can_start_paper=True,
        blocking_reasons=[],
    )


def build_candidate_provider(
    registry: CandidateRegistry | None = None,
):
    """`recommendation_provider` 시그니처로 반환.

    `consume_agent_recommendations(recommendation_provider=...)` 에 그대로
    주입 가능. registry 에 APPROVED 후보가 없으면 None 반환 → consumer skip.
    """
    if registry is None:
        registry = get_candidate_registry()

    def _provider(now: datetime) -> PaperStartExplanation | None:
        active = registry.active_candidate()
        if active is None:
            return None
        return _build_explanation_from_candidate(active, now)

    return _provider


__all__ = [
    "build_candidate_provider",
]
