"""#56: Execution Recommender API.

`/api/agents/execution-recommender/*` — ExecutionProposal 생성 + RiskManager
사전검사 + 기존 AI Assist 흐름으로의 위임.

**절대 원칙 준수** (정적 grep 가드는 `tests/test_execution_recommender.py`):
- 본 모듈은 broker.place_order / cancel_order를 *직접* 호출하지 않는다 —
  사전검사는 `precheck_proposal`(broker는 read-only로 사용), 큐 등록은
  `submit_proposal` 위임 (그 helper도 `app.ai.assist.submit_candidate`만 호출).
- `submit_proposal` 흐름 안에서만 audit row가 생성됨 — `recommend` /
  `precheck` endpoint는 DB write 0건.
- ExecutionProposal은 OrderRequest가 *아니다*; OrderRequest 변환은 ai.assist
  내부에서만 발생한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.execution_recommender import (
    ExecutionProposal,
    ProposalOrderType,
    ProposalSide,
    RecommendInput,
    RecommendResult,
    RiskPrecheckResult,
    precheck_proposal,
    recommend_proposals,
    submit_proposal,
)
from app.ai.assist import (
    AiAssistModeError,
    AiAssistPermissionDeniedError,
)
from app.api.deps import get_broker, get_risk_manager
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.session import get_db


router = APIRouter(prefix="/agents/execution-recommender", tags=["agents"])


# ====================================================================
# Pydantic schemas (request/response — 본 모듈에서 ExecutionProposal과 1:1)
# ====================================================================


class _CandidateIn(BaseModel):
    symbol:             str
    side:               Literal["BUY", "SELL"]
    latest_price:       int = Field(gt=0)
    quantity:           int = Field(default=1, gt=0)
    confidence:         int = Field(default=50, ge=0, le=100)
    quality_score:      int | None = Field(default=None, ge=0, le=100)
    target_price:       int | None = Field(default=None, ge=0)
    stop_price:         int | None = Field(default=None, ge=0)
    supporting_reasons: list[str] = Field(default_factory=list)
    opposing_reasons:   list[str] = Field(default_factory=list)
    risk_note:          str | None = None
    model:              str | None = None
    analysis_log_id:    int | None = None
    market_regime:      str | None = None


class RecommendIn(BaseModel):
    candidates:        list[_CandidateIn] = Field(default_factory=list)
    expiry_seconds:    int = Field(default=3600, ge=60, le=86400)
    proposal_strategy: str = "ai_assist:execution_recommender"


class ProposalOut(BaseModel):
    proposal_id:        str
    symbol:             str
    side:               str
    quantity:           int
    order_type:         str
    limit_price:        int | None
    confidence:         int
    quality_score:      int | None
    supporting_reasons: list[str]
    opposing_reasons:   list[str]
    risk_note:          str | None
    target_price:       int | None
    stop_price:         int | None
    expected_reward:    int | None
    expected_risk:      int | None
    risk_reward_ratio:  float | None
    strategy:           str | None
    model:              str | None
    analysis_log_id:    int | None
    market_regime:      str | None
    expires_at:         str
    created_at:         str
    is_order_intent:    bool
    can_execute_order:  bool


class RecommendOut(BaseModel):
    proposals:          list[ProposalOut]
    skipped:            list[dict[str, str]]
    auto_apply_allowed: bool
    is_order_signal:    bool
    created_at:         str
    notice:             str = (
        "본 응답은 advisory 제안입니다. 실제 주문은 위험 사전검사 + 승인 큐 "
        "결재를 모두 통과해야만 발생합니다 (자동 주문 0건)."
    )


class PrecheckIn(BaseModel):
    proposal: ProposalOut


class PrecheckOut(BaseModel):
    outcome:         str            # APPROVED / NEEDS_APPROVAL / REJECTED / BLOCKED / REDUCED
    reasons:         list[str]
    warnings:        list[str]
    risk_score:      int | None
    blocked_by:      str | None
    required_action: str | None
    evaluated_at:    str
    proposal_id:     str
    notice:          str = (
        "사전검사는 audit row를 작성하지 않습니다. 실제 큐 등록 시 RiskManager 가 "
        "다시 평가하며 audit row가 그때 작성됩니다."
    )


class SubmitIn(BaseModel):
    proposal: ProposalOut


class SubmitOut(BaseModel):
    decision:        str            # APPROVED / NEEDS_APPROVAL / REJECTED / BLOCKED
    reasons:         list[str]
    audit_id:        int | None
    approval_id:     int | None
    permission_note: str
    candidate_meta:  dict[str, Any]
    proposal_id:     str
    submitted_at:    str
    notice:          str = (
        "본 호출은 ai.assist.submit_candidate에 위임됩니다. 본 모듈은 broker / "
        "OrderExecutor / route_order를 직접 호출하지 않습니다."
    )


# ====================================================================
# helpers
# ====================================================================


def _serialize_proposal(p: ExecutionProposal) -> ProposalOut:
    return ProposalOut(
        proposal_id=p.proposal_id,
        symbol=p.symbol,
        side=p.side.value,
        quantity=p.quantity,
        order_type=p.order_type.value,
        limit_price=p.limit_price,
        confidence=p.confidence,
        quality_score=p.quality_score,
        supporting_reasons=list(p.supporting_reasons),
        opposing_reasons=list(p.opposing_reasons),
        risk_note=p.risk_note,
        target_price=p.target_price,
        stop_price=p.stop_price,
        expected_reward=p.expected_reward,
        expected_risk=p.expected_risk,
        risk_reward_ratio=p.risk_reward_ratio,
        strategy=p.strategy,
        model=p.model,
        analysis_log_id=p.analysis_log_id,
        market_regime=p.market_regime,
        expires_at=p.expires_at.isoformat(),
        created_at=p.created_at.isoformat(),
        is_order_intent=p.is_order_intent,
        can_execute_order=p.can_execute_order,
    )


def _deserialize_proposal(po: ProposalOut) -> ExecutionProposal:
    return ExecutionProposal(
        proposal_id=po.proposal_id,
        symbol=po.symbol,
        side=ProposalSide(po.side),
        quantity=po.quantity,
        confidence=po.confidence,
        expires_at=datetime.fromisoformat(po.expires_at),
        order_type=ProposalOrderType(po.order_type),
        limit_price=po.limit_price,
        target_price=po.target_price,
        stop_price=po.stop_price,
        quality_score=po.quality_score,
        supporting_reasons=tuple(po.supporting_reasons),
        opposing_reasons=tuple(po.opposing_reasons),
        risk_note=po.risk_note,
        expected_reward=po.expected_reward,
        expected_risk=po.expected_risk,
        risk_reward_ratio=po.risk_reward_ratio,
        strategy=po.strategy,
        model=po.model,
        analysis_log_id=po.analysis_log_id,
        market_regime=po.market_regime,
    )


# ====================================================================
# Endpoints
# ====================================================================


@router.post("/recommend", response_model=RecommendOut)
def post_recommend(req: RecommendIn) -> RecommendOut:
    """후보 목록 → ExecutionProposal 목록. **DB write 0건, broker 호출 0건**.

    본 endpoint는 *순수* — 같은 입력이면 같은 출력 (`created_at` 제외).
    """
    inp = RecommendInput(
        candidates=tuple(
            RecommendInput.Candidate(
                symbol=c.symbol,
                side=ProposalSide(c.side),
                latest_price=c.latest_price,
                target_price=c.target_price,
                stop_price=c.stop_price,
                quantity=c.quantity,
                confidence=c.confidence,
                quality_score=c.quality_score,
                supporting_reasons=tuple(c.supporting_reasons),
                opposing_reasons=tuple(c.opposing_reasons),
                risk_note=c.risk_note,
                model=c.model,
                analysis_log_id=c.analysis_log_id,
                market_regime=c.market_regime,
            )
            for c in req.candidates
        ),
        expiry_seconds=req.expiry_seconds,
        proposal_strategy=req.proposal_strategy,
    )
    result: RecommendResult = recommend_proposals(inp)
    return RecommendOut(
        proposals=[_serialize_proposal(p) for p in result.proposals],
        skipped=[{"symbol": s, "reason": r} for s, r in result.skipped],
        auto_apply_allowed=result.auto_apply_allowed,
        is_order_signal=result.is_order_signal,
        created_at=result.created_at.isoformat(),
    )


@router.post("/precheck", response_model=PrecheckOut)
async def post_precheck(
    req:    PrecheckIn,
    broker = Depends(get_broker),
    risk   = Depends(get_risk_manager),
) -> PrecheckOut:
    """제안에 대한 RiskManager 사전검사. **audit row 0건, broker.place_order 0건**.

    실제 큐 등록 시(`/submit`) RiskManager가 *다시* 평가하므로 본 결과는
    advisory dry-run.
    """
    settings = get_settings()
    proposal = _deserialize_proposal(req.proposal)
    result: RiskPrecheckResult = await precheck_proposal(
        proposal,
        risk=risk,
        broker=broker,
        mode=settings.default_mode,
        requested_by_ai=True,
    )
    return PrecheckOut(
        outcome=result.outcome.value,
        reasons=list(result.reasons),
        warnings=list(result.warnings),
        risk_score=result.risk_score,
        blocked_by=result.blocked_by,
        required_action=result.required_action,
        evaluated_at=result.evaluated_at.isoformat(),
        proposal_id=result.proposal_id,
    )


@router.post("/submit", response_model=SubmitOut)
async def post_submit(
    req:    SubmitIn,
    broker = Depends(get_broker),
    risk   = Depends(get_risk_manager),
    db:     Session = Depends(get_db),
) -> SubmitOut:
    """제안을 기존 sanctioned approval 흐름(`ai.assist.submit_candidate`)에 위임.

    응답 status code 매핑:
    - 200: routing.decision 모두 (NEEDS_APPROVAL / REJECTED / BLOCKED / APPROVED)
    - 403: AI Permission Gate 차단 또는 mode mismatch (`ai.assist`가 raise)
    - 410: 제안이 이미 만료 (`is_expired()`)
    """
    settings = get_settings()
    mode = settings.default_mode

    if mode != OperationMode.LIVE_AI_ASSIST:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "ai_assist_mode_required",
                "current_mode": mode.value,
                "message": (
                    "Execution Recommender 큐 등록은 LIVE_AI_ASSIST 모드 전용입니다. "
                    "다른 모드에서는 `/recommend` (read-only) + `/precheck` (audit X)만 "
                    "사용하세요."
                ),
            },
        )

    proposal = _deserialize_proposal(req.proposal)
    if proposal.is_expired():
        raise HTTPException(
            status_code=410,
            detail={
                "error": "proposal_expired",
                "proposal_id": proposal.proposal_id,
                "expires_at": proposal.expires_at.isoformat(),
                "message": "제안이 만료되었습니다. 재추천 후 다시 제출하세요.",
            },
        )

    try:
        result = await submit_proposal(
            proposal,
            risk=risk,
            broker=broker,
            db=db,
            mode=mode,
            enable_live_trading=settings.enable_live_trading,
            enable_ai_execution=settings.enable_ai_execution,
            enable_futures_live_trading=settings.enable_futures_live_trading,
        )
    except AiAssistPermissionDeniedError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "ai_permission_denied",
                "level": e.decision.level.value,
                "reasons": list(e.decision.reasons),
                "audit_note": e.decision.audit_note,
            },
        )
    except AiAssistModeError as e:
        raise HTTPException(status_code=403, detail=str(e))

    routing = result.routing
    return SubmitOut(
        decision=routing.decision.value,
        reasons=list(routing.reasons),
        audit_id=routing.audit.id if routing.audit else None,
        approval_id=routing.approval.id if routing.approval else None,
        permission_note=result.permission.audit_note,
        candidate_meta=result.candidate_meta,
        proposal_id=proposal.proposal_id,
        submitted_at=datetime.now(timezone.utc).isoformat(),
    )
