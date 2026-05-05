from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_broker
from app.brokers.base import BrokerAdapter, OrderResult
from app.db.session import get_db
from app.permission.gate import (
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


class ApprovalDecision(BaseModel):
    decided_by: str | None = None
    note:       str | None = None


class ApproveResponse(BaseModel):
    approval: ApprovalOut
    result:   OrderResult


def _to_out(approval) -> ApprovalOut:
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
    )


@router.get("", response_model=list[ApprovalOut])
def list_pending(db: Session = Depends(get_db)) -> list[ApprovalOut]:
    return [_to_out(a) for a in PermissionGate(db).list_pending()]


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: int, db: Session = Depends(get_db)) -> ApprovalOut:
    try:
        return _to_out(PermissionGate(db).get(approval_id))
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")


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
    return ApproveResponse(approval=_to_out(approval), result=result)


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
