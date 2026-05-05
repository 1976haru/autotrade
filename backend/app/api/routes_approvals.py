from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_broker
from app.brokers.base import BrokerAdapter, OrderResult
from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.permission.gate import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_REJECTED,
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    PermissionGate,
)

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


class ApprovalDecision(BaseModel):
    decided_by: str | None = None
    note:       str | None = None


class ApproveResponse(BaseModel):
    approval: ApprovalOut
    result:   OrderResult


def _to_out(approval, reasons: list[str] | None = None) -> ApprovalOut:
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
        reasons=list(reasons or []),
    )


def _load_reasons(db: Session, approvals: list) -> dict[int, list]:
    """결재 목록의 audit_id를 IN으로 한 번에 조회해 dict로 돌려준다.

    N+1을 피하기 위함 — pending은 보통 적지만 history는 50건까지 갈 수 있다.
    """
    if not approvals:
        return {}
    audit_ids = [a.audit_id for a in approvals]
    rows = db.execute(
        select(OrderAuditLog.id, OrderAuditLog.reasons)
        .where(OrderAuditLog.id.in_(audit_ids))
    ).all()
    return {row[0]: list(row[1] or []) for row in rows}


@router.get("", response_model=list[ApprovalOut])
def list_pending(db: Session = Depends(get_db)) -> list[ApprovalOut]:
    approvals = PermissionGate(db).list_pending()
    reasons_by_audit = _load_reasons(db, approvals)
    return [_to_out(a, reasons_by_audit.get(a.audit_id)) for a in approvals]


_DECIDED_STATUSES = {STATUS_APPROVED, STATUS_REJECTED, STATUS_CANCELLED}


@router.get("/history", response_model=list[ApprovalOut])
def list_history(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Literal["APPROVED", "REJECTED", "CANCELLED"] | None = Query(None),
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
    reasons_by_audit = _load_reasons(db, rows)
    return [_to_out(a, reasons_by_audit.get(a.audit_id)) for a in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: int, db: Session = Depends(get_db)) -> ApprovalOut:
    try:
        approval = PermissionGate(db).get(approval_id)
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    reasons = _load_reasons(db, [approval]).get(approval.audit_id)
    return _to_out(approval, reasons)


@router.post("/{approval_id}/approve", response_model=ApproveResponse)
async def approve_route(
    approval_id: int,
    payload: ApprovalDecision | None = None,
    broker:  BrokerAdapter = Depends(get_broker),
    db:      Session = Depends(get_db),
) -> ApproveResponse:
    decision = payload or ApprovalDecision()
    try:
        approval, result = await PermissionGate(db).approve(
            approval_id, broker,
            decided_by=decision.decided_by, note=decision.note,
        )
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    except ApprovalAlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e))
    reasons = _load_reasons(db, [approval]).get(approval.audit_id)
    return ApproveResponse(approval=_to_out(approval, reasons), result=result)


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
