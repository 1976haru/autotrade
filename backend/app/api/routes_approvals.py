from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import BrokerAdapter, OrderResult
from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.permission.gate import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    ApprovalRiskCheckFailedError,
    PermissionGate,
)
from app.risk.risk_manager import RiskManager

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalOut(BaseModel):
    id:          int
    created_at:  datetime
    audit_id:    int
    symbol:      str
    side:        str
    quantity:    int
    order_type:  str
    limit_price: int | None = None
    mode:        str
    status:      str
    decided_at:  datetime | None = None
    decided_by:  str | None = None
    note:        str | None = None
    # RiskManager가 NEEDS_APPROVAL로 분류한 사유. 운영자가 승인/거부 결정 전
    # 컨텍스트를 즉시 보도록 audit row의 reasons를 join해 노출한다.
    reasons:     list[str] = []
    # 076: 070 재평가에서 거부된 시도 이력. 한 행이 여러 번 거부되면 그때마다
    # {at, decided_by, reasons} 항목이 누적된다. 운영자 인계/restart 후에도
    # "이 결재는 막혔던 적 있다" 단서가 보존된다.
    attempts:    list[dict] = []
    # 190: AI 출처 + signal quality + AI 결정 메타. audit row 동시 조회해서 채운다.
    # 운영자가 결재 카드만 보고 AI 라우팅 vs 수동 주문을 구분할 수 있어야 한다.
    requested_by_ai:   bool = False
    strategy:          str | None  = None
    signal_strength:   int | None  = None
    signal_confidence: int | None  = None
    ai_decision_meta:  dict | None = None


class ApprovalDecision(BaseModel):
    decided_by: str | None = None
    note:       str | None = None


class ApproveResponse(BaseModel):
    approval: ApprovalOut
    result:   OrderResult


def _to_out(approval, audit_meta: dict | None = None) -> ApprovalOut:
    audit_meta = audit_meta or {}
    return ApprovalOut(
        id=approval.id,
        created_at=approval.created_at,
        audit_id=approval.audit_id,
        symbol=approval.symbol,
        side=approval.side,
        quantity=approval.quantity,
        order_type=approval.order_type,
        limit_price=approval.limit_price,
        mode=approval.mode,
        status=approval.status,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        note=approval.note,
        reasons=list(audit_meta.get("reasons") or []),
        attempts=list(approval.attempts or []),
        # 190: AI 출처 정보 — audit row 한 번 조회로 함께 가져온다.
        requested_by_ai=  bool(audit_meta.get("requested_by_ai", False)),
        strategy=         audit_meta.get("strategy"),
        signal_strength=  audit_meta.get("signal_strength"),
        signal_confidence=audit_meta.get("signal_confidence"),
        ai_decision_meta= audit_meta.get("ai_decision_meta"),
    )


def _load_audit_meta(db: Session, approvals: list) -> dict[int, dict]:
    """audit_id → {reasons, requested_by_ai, strategy, signal_*, ai_decision_meta}.

    N+1 회피: 한 번의 IN 쿼리로 결재 목록 전체 audit row의 메타를 조회.
    pending은 보통 적지만 history는 limit 200까지 가므로 join 단일화가 중요.
    """
    if not approvals:
        return {}
    audit_ids = [a.audit_id for a in approvals]
    rows = db.execute(
        select(
            OrderAuditLog.id,
            OrderAuditLog.reasons,
            OrderAuditLog.requested_by_ai,
            OrderAuditLog.strategy,
            OrderAuditLog.signal_strength,
            OrderAuditLog.signal_confidence,
            OrderAuditLog.ai_decision_meta,
        ).where(OrderAuditLog.id.in_(audit_ids))
    ).all()
    return {
        row[0]: {
            "reasons":           list(row[1] or []),
            "requested_by_ai":   bool(row[2]),
            "strategy":          row[3],
            "signal_strength":   row[4],
            "signal_confidence": row[5],
            "ai_decision_meta":  row[6],
        }
        for row in rows
    }


# Backwards-compat alias for tests / imports that may have referenced the
# old narrower helper. Returns reasons-only dict for callers that need it.
def _load_reasons(db: Session, approvals: list) -> dict[int, list]:
    return {k: v["reasons"] for k, v in _load_audit_meta(db, approvals).items()}


@router.get("", response_model=list[ApprovalOut])
def list_pending(db: Session = Depends(get_db)) -> list[ApprovalOut]:
    approvals = PermissionGate(db).list_pending()
    meta_by_audit = _load_audit_meta(db, approvals)
    return [_to_out(a, meta_by_audit.get(a.audit_id)) for a in approvals]


_DECIDED_STATUSES = {STATUS_APPROVED, STATUS_REJECTED,
                     STATUS_CANCELLED, STATUS_EXPIRED}


@router.get("/history", response_model=list[ApprovalOut])
def list_history(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Literal["APPROVED", "REJECTED", "CANCELLED", "EXPIRED"] | None = Query(None),
    db:     Session = Depends(get_db),
) -> list[ApprovalOut]:
    """Decided approvals (APPROVED / REJECTED / CANCELLED), most recent first.

    Complements `GET /api/approvals` which returns PENDING only. The status
    filter is whitelisted to the three terminal states so callers cannot
    accidentally retrieve PENDING rows through this route.
    """
    if status is not None and status not in _DECIDED_STATUSES:
        raise HTTPException(status_code=400, detail=f"unsupported status: {status}")
    rows = PermissionGate(db).list_decided(limit=limit, offset=offset, status=status)
    meta_by_audit = _load_audit_meta(db, rows)
    return [_to_out(a, meta_by_audit.get(a.audit_id)) for a in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: int, db: Session = Depends(get_db)) -> ApprovalOut:
    try:
        approval = PermissionGate(db).get(approval_id)
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    meta = _load_audit_meta(db, [approval]).get(approval.audit_id)
    return _to_out(approval, meta)


@router.post("/{approval_id}/approve", response_model=ApproveResponse)
async def approve_route(
    approval_id: int,
    payload: ApprovalDecision | None = None,
    broker:  BrokerAdapter = Depends(get_broker),
    risk:    RiskManager   = Depends(get_risk_manager),
    db:      Session = Depends(get_db),
) -> ApproveResponse:
    decision = payload or ApprovalDecision()
    try:
        approval, result = await PermissionGate(db).approve(
            approval_id, broker, risk,
            decided_by=decision.decided_by, note=decision.note,
        )
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    except ApprovalAlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ApprovalRiskCheckFailedError as e:
        # 070: re-eval surfaced violations that didn't exist at submit. The
        # approval row is intentionally left PENDING so the operator can
        # retry once the broker state recovers.
        raise HTTPException(status_code=409, detail={
            "error": "risk_check_failed_at_approve",
            "reasons": e.reasons,
        })
    meta = _load_audit_meta(db, [approval]).get(approval.audit_id)
    return ApproveResponse(approval=_to_out(approval, meta), result=result)


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
def reject_route(
    approval_id: int,
    payload: ApprovalDecision | None = None,
    db:      Session = Depends(get_db),
) -> ApprovalOut:
    decision = payload or ApprovalDecision()
    try:
        approval = PermissionGate(db).reject(
            approval_id,
            decided_by=decision.decided_by, note=decision.note,
        )
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    except ApprovalAlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_out(approval)


@router.post("/{approval_id}/cancel", response_model=ApprovalOut)
def cancel_route(
    approval_id: int,
    payload: ApprovalDecision | None = None,
    db:      Session = Depends(get_db),
) -> ApprovalOut:
    """Dismiss a pending approval without approving or rejecting.

    Distinct from reject: CANCELLED is a neutral disposition — used when the
    signal is stale or the order is no longer relevant, not when the operator
    actively refuses it. Same 404/409 contract as reject.
    """
    decision = payload or ApprovalDecision()
    try:
        approval = PermissionGate(db).cancel(
            approval_id,
            decided_by=decision.decided_by, note=decision.note,
        )
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    except ApprovalAlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_out(approval)
