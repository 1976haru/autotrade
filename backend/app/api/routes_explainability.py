"""Signal Explainability read-only API (#33).

`GET /api/signals/{audit_id}/explain` — OrderAuditLog row의 결정 사슬을
구조화된 SignalExplanation으로 반환한다. 본 endpoint는 *read-only* — DB
write도, broker / RiskManager / PermissionGate 호출도 하지 않는다.

기존 OrderAuditLog 스키마는 변경되지 않는다 — 본 endpoint는 row를 select해서
`extract_reasons_from_audit_row`로 합성만 한다. 운영자/Agent/Frontend 패널이
"왜 이 신호가 승인/거절/대기됐는지"를 한눈에 파악할 수 있게 한다.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.explainability import (
    extract_reasons_from_audit_row,
)


router = APIRouter(prefix="/signals", tags=["signals"])


class SignalReasonOut(BaseModel):
    category: str
    status:   str
    severity: str
    source:   str | None = None
    code:     str | None = None
    message:  str
    details:  dict | None = None


class SignalExplainOut(BaseModel):
    audit_trace_id: int | None = None
    symbol:         str | None = None
    strategy:       str | None = None
    action:         str | None = None
    final_status:   str
    summary:        str
    reasons:        list[SignalReasonOut]
    indicators:     dict | None = None
    risk_notes:     list[str] = []
    operator_note:  str | None = None
    # UI 패널이 PASS/WARN/FAIL/BLOCKED/INFO 별로 카드를 나누기 위해 grouped도 함께.
    grouped:        dict[str, list[SignalReasonOut]] = {}


@router.get("/{audit_id}/explain", response_model=SignalExplainOut)
def explain_signal(audit_id: int, db: Session = Depends(get_db)) -> SignalExplainOut:
    """OrderAuditLog row → 구조화된 SignalExplanation.

    audit_id가 없으면 404. row의 reasons / decision / ai_decision_meta /
    message를 분석해 카테고리별 SignalReason으로 정규화. 본 endpoint는
    read-only — broker/risk/permission/execution 호출 없음.
    """
    row = db.get(OrderAuditLog, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit_id={audit_id} not found")

    explanation = extract_reasons_from_audit_row(row)
    grouped = explanation.grouped_by_status()
    grouped_out = {
        k: [SignalReasonOut(**r.to_dict()) for r in v] for k, v in grouped.items()
    }
    return SignalExplainOut(
        audit_trace_id=explanation.audit_trace_id,
        symbol=explanation.symbol,
        strategy=explanation.strategy,
        action=explanation.action,
        final_status=explanation.final_status.value,
        summary=explanation.summary,
        reasons=[SignalReasonOut(**r.to_dict()) for r in explanation.reasons],
        indicators=explanation.indicators,
        risk_notes=explanation.risk_notes,
        operator_note=explanation.operator_note,
        grouped=grouped_out,
    )
