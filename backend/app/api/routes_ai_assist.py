"""#44: AI Assisted Trading 라우터.

`/api/ai/assist/submit` — AI candidate를 RiskManager 사전검사 + AI Permission
Gate를 통과시켜 PendingApproval 큐에 등록.
`/api/ai/assist/pending` — 현재 PENDING이며 AI Assist 출처인 결재만 필터.
`/api/ai/assist/summary` — 운영자 Dashboard 카드용 요약.

**절대 원칙 준수:**
- 본 모듈은 broker.place_order / cancel_order / OrderExecutor를 *직접* 호출
  하지 않는다 — `app.ai.assist.submit_candidate` 위임 (그 함수도 route_order
  만 호출). 정적 grep 가드는 `tests/test_ai_assist.py`.
- AI Permission Gate가 차단하면 PendingApproval row가 만들어지지 않는다.
- RiskManager가 REJECTED를 반환하면 audit row만 남고 approval은 None — UI는
  "AI 제안 거부됨"으로 surface한다 (큐 등록 X).
- `request_source=AI` + `trade_reason=ai_assist`가 audit row에 carry되어
  routes_approvals._derive_request_source가 자동으로 AI 라벨을 붙인다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.assist import (
    AI_ASSIST_TRADE_REASON,
    AICandidate,
    AiAssistModeError,
    AiAssistPermissionDeniedError,
    submit_candidate,
)
from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import BrokerAdapter, OrderSide, OrderType
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.db.session import get_db
from app.permission.gate import STATUS_PENDING
from app.risk.risk_manager import RiskDecision, RiskManager


router = APIRouter(prefix="/ai/assist", tags=["ai", "approvals"])


# ====================================================================
# Request / Response schemas
# ====================================================================


class AICandidateIn(BaseModel):
    """frontend에서 보내는 AI candidate. AICandidate dataclass와 1:1."""

    symbol:             str
    side:               Literal["BUY", "SELL"]
    quantity:           int = Field(gt=0)
    order_type:         Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price:        int | None = Field(default=None, ge=0)

    confidence:         int = Field(default=50, ge=0, le=100)
    quality_score:      int | None = Field(default=None, ge=0, le=100)

    supporting_reasons: list[str] = Field(default_factory=list)
    opposing_reasons:   list[str] = Field(default_factory=list)
    risk_note:          str | None = None

    model:              str | None = None
    analysis_log_id:    int | None = None
    strategy:           str | None = "ai_assist"

    target_price:       int | None = Field(default=None, ge=0)
    stop_price:         int | None = Field(default=None, ge=0)
    client_order_id:    str | None = None

    def to_dataclass(self) -> AICandidate:
        return AICandidate(
            symbol=self.symbol,
            side=OrderSide(self.side),
            quantity=self.quantity,
            order_type=OrderType(self.order_type),
            limit_price=self.limit_price,
            confidence=self.confidence,
            quality_score=self.quality_score,
            supporting_reasons=list(self.supporting_reasons),
            opposing_reasons=list(self.opposing_reasons),
            risk_note=self.risk_note,
            model=self.model,
            analysis_log_id=self.analysis_log_id,
            strategy=self.strategy,
            target_price=self.target_price,
            stop_price=self.stop_price,
            client_order_id=self.client_order_id,
        )


class AiAssistSubmitOut(BaseModel):
    """submit 응답. routing.decision에 따라 approval_id가 None일 수 있다."""

    decision:        str            # APPROVED / NEEDS_APPROVAL / REJECTED / BLOCKED
    reasons:         list[str]
    audit_id:        int | None     # audit row id (REJECTED여도 row는 남는다)
    approval_id:     int | None     # NEEDS_APPROVAL인 경우만 채워짐
    permission_note: str            # AI Permission Gate audit_note
    candidate_meta:  dict           # ai_decision_meta — UI가 그대로 표시
    submitted_at:    datetime


class AiAssistSummaryOut(BaseModel):
    """Dashboard 카드용 요약 — AI Assist 출처 audit 기반."""

    pending_count:        int
    approved_count_24h:   int
    rejected_count_24h:   int
    total_24h:            int
    last_submitted_at:    datetime | None
    notice:               str


# ====================================================================
# Endpoints
# ====================================================================


@router.post("/submit", response_model=AiAssistSubmitOut)
async def submit_route(
    payload: AICandidateIn,
    broker:  BrokerAdapter = Depends(get_broker),
    risk:    RiskManager   = Depends(get_risk_manager),
    db:      Session       = Depends(get_db),
) -> AiAssistSubmitOut:
    """AI candidate를 RiskManager 사전검사 + PendingApproval 큐에 등록.

    응답 status code 매핑:
    - 200: NEEDS_APPROVAL (큐 등록 성공) 또는 REJECTED (audit row만)
    - 403: AI Permission Gate가 차단
    - 409: client_order_id가 이미 처리된 경우 (DuplicateOrderError)
    - 422: payload validation 실패 (FastAPI 자동)

    REJECTED라도 200을 반환하는 이유: audit row가 작성되었고, 호출자(frontend)
    가 reasons를 표시해야 하기 때문. 200 응답에 `decision=REJECTED`로 명시.
    """
    settings = get_settings()
    mode = settings.default_mode

    if mode != OperationMode.LIVE_AI_ASSIST:
        # 본 흐름은 LIVE_AI_ASSIST 전용. 다른 mode에선 AI Permission Gate가
        # SUBMIT_FOR_APPROVAL을 차단하지만, mode 자체를 미리 가드해 명시적으로.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "ai_assist_mode_required",
                "current_mode": mode.value,
                "message": (
                    "AI Assist submit은 LIVE_AI_ASSIST 모드 전용입니다. "
                    "다른 모드에서는 /api/ai/analyze (read-only)를 사용하세요."
                ),
            },
        )

    candidate = payload.to_dataclass()

    try:
        result = await submit_candidate(
            candidate=candidate,
            mode=mode,
            broker=broker,
            risk=risk,
            db=db,
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
    return AiAssistSubmitOut(
        decision=routing.decision.value,
        reasons=list(routing.reasons),
        audit_id=routing.audit.id if routing.audit else None,
        approval_id=routing.approval.id if routing.approval else None,
        permission_note=result.permission.audit_note,
        candidate_meta=result.candidate_meta,
        submitted_at=datetime.now(timezone.utc),
    )


@router.get("/pending")
def list_pending_route(db: Session = Depends(get_db)) -> list[dict]:
    """현재 PENDING이며 AI Assist 출처인 결재만 필터.

    Approvals 라우터의 `_to_out` 응답을 그대로 사용하지 않고 요약 dict만
    반환 — frontend Dashboard 카드는 audit_id / symbol / confidence /
    supporting_reasons만 필요. 전체 결재 데이터는 `/api/approvals`로 조회.
    """
    rows = db.execute(
        select(PendingApproval, OrderAuditLog)
        .join(OrderAuditLog, PendingApproval.audit_id == OrderAuditLog.id)
        .where(
            PendingApproval.status == STATUS_PENDING,
            OrderAuditLog.requested_by_ai.is_(True),
            OrderAuditLog.trade_reason == AI_ASSIST_TRADE_REASON,
        )
        .order_by(PendingApproval.created_at.desc())
    ).all()

    out: list[dict] = []
    for approval, audit in rows:
        meta = audit.ai_decision_meta or {}
        out.append({
            "approval_id":         approval.id,
            "audit_id":            audit.id,
            "created_at":          approval.created_at.isoformat() if approval.created_at else None,
            "symbol":              approval.symbol,
            "side":                approval.side,
            "quantity":            approval.quantity,
            "order_type":          approval.order_type,
            "limit_price":         approval.limit_price,
            "mode":                approval.mode,
            "confidence":          audit.signal_confidence,
            "quality_score":       audit.signal_strength,
            "supporting_reasons":  list(meta.get("supporting_reasons") or []),
            "opposing_reasons":    list(meta.get("opposing_reasons") or []),
            "risk_note":           meta.get("risk_note"),
            "model":               meta.get("model"),
            "target_price":        meta.get("target_price"),
            "stop_price":          meta.get("stop_price"),
            "reasons":             list(audit.reasons or []),
            "request_source":      "AI",
        })
    return out


_DAY_SECONDS = 24 * 60 * 60


@router.get("/summary", response_model=AiAssistSummaryOut)
def summary_route(db: Session = Depends(get_db)) -> AiAssistSummaryOut:
    """Dashboard 카드용 요약. AI Assist 출처 audit row 기반 24시간 카운트.

    - `pending_count`: 현재 PENDING + AI Assist 출처
    - `approved_count_24h`: AI Assist 출처 + audit.executed=True (broker로 갔음)
    - `rejected_count_24h`: AI Assist 출처 + decision=REJECTED
    - `total_24h`: AI Assist 출처 audit row 전체 (24시간)
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    since = now - timedelta(seconds=_DAY_SECONDS)

    is_assist = (
        OrderAuditLog.requested_by_ai.is_(True),
        OrderAuditLog.trade_reason == AI_ASSIST_TRADE_REASON,
    )

    pending_count = db.execute(
        select(func.count(PendingApproval.id))
        .join(OrderAuditLog, PendingApproval.audit_id == OrderAuditLog.id)
        .where(
            PendingApproval.status == STATUS_PENDING,
            *is_assist,
        )
    ).scalar() or 0

    approved_24h = db.execute(
        select(func.count(OrderAuditLog.id)).where(
            *is_assist,
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.created_at >= since,
        )
    ).scalar() or 0

    rejected_24h = db.execute(
        select(func.count(OrderAuditLog.id)).where(
            *is_assist,
            OrderAuditLog.decision == RiskDecision.REJECTED.value,
            OrderAuditLog.created_at >= since,
        )
    ).scalar() or 0

    total_24h = db.execute(
        select(func.count(OrderAuditLog.id)).where(
            *is_assist,
            OrderAuditLog.created_at >= since,
        )
    ).scalar() or 0

    last_submitted_at = db.execute(
        select(func.max(OrderAuditLog.created_at)).where(*is_assist)
    ).scalar()

    return AiAssistSummaryOut(
        pending_count=int(pending_count),
        approved_count_24h=int(approved_24h),
        rejected_count_24h=int(rejected_24h),
        total_24h=int(total_24h),
        last_submitted_at=last_submitted_at,
        notice=(
            "AI는 매수/매도 후보 *제안*만 합니다. 모든 주문은 사람 승인 후에만 "
            "broker로 진행됩니다. 자동매매 활성화는 별도 옵트인이 필요합니다."
        ),
    )
