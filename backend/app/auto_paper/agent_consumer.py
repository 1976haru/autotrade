"""#4-Loop-09: Auto Loop → Agent recommendation consumer.

`AutoPaperLoop.tick()` 이 RUNNING 상태에서 본 모듈의 `consume_agent_recommendations()`
를 호출해 1 cycle 동안:

1. caller 가 주입한 *provider* 로 `PaperStartExplanation` 을 얻고 (4-05),
2. `bridge_explanation_to_paper_decisions(...)` 를 호출해 PaperDecision 을 만들고
   (4-07 / 4-08 / 4-09 통합),
3. 결과 `BridgeReport.decisions` 를 *Paper ledger* (2-09) 에 기록하고,
4. db_session 이 제공되면 `AgentDecisionLog` (4-10) 한 행을 INSERT한다.

## 본 모듈은 *오케스트레이션 어댑터*

- AI / LLM / 외부 HTTP 직접 호출 0건.
- broker / OrderExecutor / route_order import 0건.
- `provider` 함수가 *결정론적* (테스트는 deterministic stub 사용).
- 운영자가 explicit 한 `recommendation_provider` 를 주입하지 않으면
  consumer 는 `INSUFFICIENT_DATA` ConsumerResult 만 반환 — 자동매수가
  생성될 *경로 0건*.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. broker / OrderExecutor / route_order import 0건 (정적 grep + AST 가드).
2. Anthropic / OpenAI / httpx / requests import 0건 (정적 grep 가드).
3. `ConsumerResult.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` 영구.
4. RUNNING 이 아닐 때 함수가 호출되어도 *0 decisions / 0 ledger / 0 log row*
   — 본 모듈은 *읽기 + dataclass 변환 + ledger.add* 만 수행.
5. 안전 flag mutation 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.agents.paper_decision_bridge import (
    PositionSnapshot,
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.auto_paper.position_sizer import PositionSizingPolicy


CONSUMER_SCHEMA_VERSION = "1.0"


# Provider protocols — caller (실 운영자) 가 주입한다. 본 모듈은 OpenAI /
# Anthropic 을 *호출하지 않으며*, 운영자가 stub / 분석 결과 / strategy state
# 으로부터 구조화된 입력만 만들도록 분리한다.

# `RecommendationProvider(now) -> PaperStartExplanation | None` — 매 tick 호출.
# 반환값이 None 이면 본 cycle 은 "데이터 부족 — 0 decision". 안전한 fallback.
RecommendationProvider = Callable[
    [datetime], Optional[PaperStartExplanation],
]

# `PositionProvider() -> list[PositionSnapshot]` — 보유 포지션 read-only.
PositionProvider = Callable[[], list[PositionSnapshot]]


@dataclass(frozen=True)
class ConsumerResult:
    """1 cycle consumer 실행 결과 — *advisory*, broker 호출 0건."""

    cycle_at:           str                # ISO 8601 UTC
    schema_version:     str
    consumed:           bool               # provider 가 explanation 반환했는지
    explanation_verdict: str | None
    decision_count:     int                = 0
    ledger_events:      int                = 0
    ledger_blocked:     int                = 0
    decision_log_count: int                = 0
    by_action:          dict[str, int]     = field(default_factory=dict)
    block_reasons:      list[str]          = field(default_factory=list)
    summary:            str                = ""
    metadata:           dict[str, Any]     = field(default_factory=dict)

    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ConsumerResult.{name} must be False.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_at":           self.cycle_at,
            "schema_version":     self.schema_version,
            "consumed":           bool(self.consumed),
            "explanation_verdict": self.explanation_verdict,
            "decision_count":     int(self.decision_count),
            "ledger_events":      int(self.ledger_events),
            "ledger_blocked":     int(self.ledger_blocked),
            "decision_log_count": int(self.decision_log_count),
            "by_action":          dict(self.by_action),
            "block_reasons":      list(self.block_reasons),
            "summary":            self.summary,
            "metadata":           dict(self.metadata),
            "is_order_signal":    False,
            "auto_apply_allowed": False,
            "is_live_authorization": False,
        }


def consume_agent_recommendations(
    *,
    loop_state:               str,
    recommendation_provider:  RecommendationProvider | None = None,
    position_provider:        PositionProvider | None       = None,
    sizing_policy:            PositionSizingPolicy | None   = None,
    risk_officer_rejects:     dict[tuple[str, str], str] | None = None,
    extra_risk_flags:         dict[tuple[str, str], list[str]] | None = None,
    virtual_trade_size:       int                           = 1,
    auto_fill:                bool                          = True,
    db_session:               Any                           = None,
    chain_id:                 str | None                    = None,
    now:                      datetime | None               = None,
    # #4-RiskProfileApply: 운영자가 선택한 AI 운용 성향 — None / 알 수 없는
    # 값 → BALANCED (기본값). 명시 sizing_policy 가 주어지면 우선.
    risk_profile:             Any                           = None,
) -> ConsumerResult:
    """1 cycle 동안 Agent 추천 → PaperDecision → ledger + AgentDecisionLog.

    *broker 호출 0건* — bridge 가 모든 정책 (4-08 sizing / 4-09 risk veto /
    4-10 decision log) 을 통과한 결과를 반환할 뿐, 실 주문 경로 0건.

    RUNNING 이 아니거나 provider 가 None / explanation 미반환 시 0 decision
    으로 안전 종료.

    #4-RiskProfileApply:
        risk_profile 이 None / "" / unknown → BALANCED (4-08 default).
        명시 sizing_policy 와 risk_profile 둘 다 주어지면 **sizing_policy
        우선** (운영자 명시 override 보존). 성향이 적용되면 응답
        metadata.risk_profile / metadata.risk_veto_max_flags carry.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    iso_now = now.isoformat()

    # 1. RUNNING 이 아닐 때 — provider 호출도 하지 않음.
    if loop_state != "RUNNING":
        return ConsumerResult(
            cycle_at=iso_now,
            schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=False,
            explanation_verdict=None,
            summary=f"loop_state={loop_state} — consumer skipped (RUNNING 이 아님)",
            metadata={"reason": "non_running_state"},
        )

    # 2. provider 미주입 시 — 안전한 fallback, 자동 생성 0건.
    if recommendation_provider is None:
        return ConsumerResult(
            cycle_at=iso_now,
            schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=False,
            explanation_verdict=None,
            summary="recommendation_provider 미주입 — consumer skipped",
            metadata={"reason": "no_provider"},
        )

    # 3. provider 가 explanation 을 반환하지 않으면 — 데이터 부족.
    try:
        explanation = recommendation_provider(now)
    except Exception as exc:  # noqa: BLE001 — 외부 함수, 운영자 주입.
        return ConsumerResult(
            cycle_at=iso_now,
            schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=False,
            explanation_verdict=None,
            summary=(
                f"recommendation_provider error: {type(exc).__name__}: {exc!s}"
            ),
            metadata={"reason": "provider_error",
                      "error_type": type(exc).__name__},
        )

    if explanation is None:
        return ConsumerResult(
            cycle_at=iso_now,
            schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=False,
            explanation_verdict=None,
            summary="provider returned None — 데이터 부족, decisions 0건",
            metadata={"reason": "provider_returned_none"},
        )

    if not isinstance(explanation, PaperStartExplanation):
        return ConsumerResult(
            cycle_at=iso_now,
            schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=False,
            explanation_verdict=None,
            summary=(
                f"provider returned unexpected type "
                f"{type(explanation).__name__} — decisions 0건"
            ),
            metadata={"reason": "invalid_provider_type"},
        )

    # 4. position provider — 기본은 빈 리스트 (운영자가 명시 주입).
    positions: list[PositionSnapshot] = []
    if position_provider is not None:
        try:
            raw = position_provider() or []
            for p in raw:
                if isinstance(p, PositionSnapshot):
                    positions.append(p)
        except Exception:  # noqa: BLE001
            positions = []

    # #4-RiskProfileApply: 성향 → sizing_policy + risk_veto threshold 변환.
    # caller 가 명시 sizing_policy 를 준 경우 그대로 사용, 아니면 성향 기반 변환.
    effective_sizing_policy = sizing_policy
    profile_label: str | None = None
    risk_veto_max_flags: int = 0
    max_concurrent_candidates: int = 0
    if risk_profile is not None:
        try:
            from app.agents.risk_profile import (
                policy_for as _profile_policy_for,
                sizing_policy_for as _profile_sizing_for,
            )
            profile_policy = _profile_policy_for(risk_profile)
            profile_label = profile_policy.profile.value
            risk_veto_max_flags = int(profile_policy.risk_veto_max_flags)
            max_concurrent_candidates = int(profile_policy.max_concurrent_candidates)
            if effective_sizing_policy is None:
                effective_sizing_policy = _profile_sizing_for(risk_profile)
        except Exception:  # noqa: BLE001 — risk_profile import 실패 시 fallback.
            effective_sizing_policy = sizing_policy   # 그대로 유지.

    # 5. bridge 호출 — 4-07 / 4-08 / 4-09 / 4-10 통합.
    report = bridge_explanation_to_paper_decisions(
        explanation=explanation,
        loop_state=loop_state,
        positions=positions,
        virtual_trade_size=int(virtual_trade_size),
        auto_fill=bool(auto_fill),
        record=True,
        sizing_policy=effective_sizing_policy,
        risk_officer_rejects=risk_officer_rejects,
        extra_risk_flags=extra_risk_flags,
        risk_veto_max_flags=risk_veto_max_flags,
        db_session=db_session,
        chain_id=chain_id,
    )

    decision_log_count = (
        int(report.metadata.get("decision_log_count") or 0)
        if isinstance(report.metadata, dict) else 0
    )

    return ConsumerResult(
        cycle_at=iso_now,
        schema_version=CONSUMER_SCHEMA_VERSION,
        consumed=True,
        explanation_verdict=report.explanation_verdict,
        decision_count=len(report.decisions),
        ledger_events=int(report.events_recorded),
        ledger_blocked=int(report.events_blocked),
        decision_log_count=decision_log_count,
        by_action=dict(report.metadata.get("by_action") or {}),
        block_reasons=list(report.block_reasons),
        summary=report.summary,
        metadata={
            "input_entries":     report.metadata.get("input_entries"),
            "risk_veto_summary": (
                (report.metadata.get("risk_veto") or {}).get("summary", {})
            ),
            "sizing_applied":    bool(report.metadata.get("sizing_applied")),
            "decision_log_written": bool(
                report.metadata.get("decision_log_written")
            ),
            "risk_profile":      profile_label,
            "risk_veto_max_flags": risk_veto_max_flags,
            "max_concurrent_candidates": max_concurrent_candidates,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic null-provider (test / default) — 항상 None 반환.
# ─────────────────────────────────────────────────────────────────────────────


def null_recommendation_provider(_now: datetime) -> PaperStartExplanation | None:
    """기본 provider — 항상 None 반환 → 자동 BUY/SELL 생성 경로 0건.

    실 운영자가 명시적으로 다른 provider 를 주입하지 않으면 본 함수가
    consumer 의 fallback. 운영자는 자신만의 deterministic stub / 분석 함수를
    주입해야 한다 (예: `lambda now: build_paper_start_explanation(...)`).
    """
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Mock helper — *deterministic stub* for tests + dev. Not LLM-driven.
# ─────────────────────────────────────────────────────────────────────────────


def build_deterministic_explanation(
    *,
    strategy:          str = "sma_crossover",
    symbol:            str = "005930",
    market_regime:     str = "TREND_UP",
    risk_flags:        list[str] | None = None,
    verdict:           ExplanationVerdict = ExplanationVerdict.READY_TO_REVIEW,
    bucket:            str = "recommended",
    paper_status:      str = "READY_FOR_PAPER",
    rationale:         str = "deterministic stub for tests",
    generated_at:      str | None = None,
) -> PaperStartExplanation:
    """테스트 / dev 전용 결정론적 explanation — *LLM 호출 0건*.

    본 함수는 *운영용 권장 provider 가 아님* — 운영자가 자체적으로 분석 기반
    explanation 을 만들어야 한다.
    """
    entry = StrategyExplanation(
        strategy=strategy, symbol=symbol,
        bucket=bucket,
        paper_candidate_status=paper_status,
        rationale_lines=[rationale],
        risk_flags=list(risk_flags or []),
    )
    recommended = [entry] if bucket == "recommended" else []
    watchlist   = [entry] if bucket == "watchlist" else []
    excluded    = [entry] if bucket == "excluded" else []
    iso = generated_at or datetime.now(timezone.utc).isoformat()
    return PaperStartExplanation(
        generated_at=iso,
        schema_version="1.0",
        verdict=verdict,
        recommended_explanations=recommended,
        watchlist_explanations=watchlist,
        excluded_explanations=excluded,
        market_regime=market_regime,
        regime_confidence=0.85,
        regime_reasons=[],
        regime_risk_flags=[],
        regime_allowed_tactics=[],
        regime_blocked_tactics=[],
        overfit_count=0,
        overfit_strategies=[],
        headline="deterministic test",
        risk_summary=[],
        operator_note="",
        next_actions=[],
        can_start_paper=True,
        blocking_reasons=[],
    )


__all__ = [
    "CONSUMER_SCHEMA_VERSION",
    "ConsumerResult",
    "RecommendationProvider",
    "PositionProvider",
    "consume_agent_recommendations",
    "null_recommendation_provider",
    "build_deterministic_explanation",
]
