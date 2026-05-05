from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_risk_manager
from app.db.models import EmergencyStopEvent
from app.db.session import get_db
from app.risk.risk_manager import RiskManager, RiskPolicy

router = APIRouter(prefix="/risk", tags=["risk"])


class EmergencyStopRequest(BaseModel):
    enabled:    bool
    decided_by: str | None = None
    note:       str | None = None


class EmergencyStopEventOut(BaseModel):
    id:         int
    created_at: datetime
    enabled:    bool
    decided_by: str | None = None
    note:       str | None = None


@router.get("/policy")
def get_policy(risk: RiskManager = Depends(get_risk_manager)) -> RiskPolicy:
    return risk.policy


@router.post("/emergency-stop")
def set_emergency_stop(
    payload: EmergencyStopRequest,
    risk:    RiskManager = Depends(get_risk_manager),
    db:      Session     = Depends(get_db),
) -> dict:
    """Toggle emergency stop and log a row to the audit trail.

    No-op toggles (re-asserting the current state) skip the audit row to
    avoid noise. The runtime flag still reflects the requested state, so
    callers can use this idempotently without worrying about side effects.
    """
    state_changed = (risk.emergency_stop != payload.enabled)
    risk.set_emergency_stop(payload.enabled)
    if state_changed:
        db.add(EmergencyStopEvent(
            enabled=payload.enabled,
            decided_by=payload.decided_by,
            note=payload.note,
        ))
        db.commit()
    return {"emergency_stop": risk.emergency_stop}


@router.get("/emergency-stop/history", response_model=list[EmergencyStopEventOut])
def emergency_stop_history(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db:     Session = Depends(get_db),
) -> list[EmergencyStopEventOut]:
    """Most recent emergency-stop toggles first.

    The runtime flag is in-memory and resets to OFF on restart by design
    (operators must explicitly re-assert), but the audit trail persists so
    "who toggled when, and why" survives across restarts.
    """
    rows = db.execute(
        select(EmergencyStopEvent)
        .order_by(EmergencyStopEvent.id.desc())
        .limit(limit).offset(offset)
    ).scalars().all()
    return [
        EmergencyStopEventOut(
            id=r.id, created_at=r.created_at, enabled=r.enabled,
            decided_by=r.decided_by, note=r.note,
        )
        for r in rows
    ]
