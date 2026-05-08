from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import BrokerAdapter, OrderResult
from app.core.config import get_settings
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
    # #41: TTL surface — settings.approval_ttl_seconds > 0이면 채워짐.
    # ttl=0이면 expires_at=None, is_expired=False (만료 비활성).
    expires_at:            datetime | None = None
    seconds_until_expiry:  int | None      = None
    is_expired:            bool            = False
    # #41: 승인 전 재검증 정보 — attempts 배열 요약.
    attempt_count:         int             = 0
    last_attempt_at:       datetime | None = None
    last_attempt_reasons:  list[str]       = []
    # #41: 신호 출처 분류 — AI 제안 / 전략 신호 / 수동 주문 / 청산 후보 / 알 수 없음.
    request_source:        str             = "UNKNOWN"
    request_source_label:  str             = "알 수 없음"


class ApprovalDecision(BaseModel):
    decided_by: str | None = None
    note:       str | None = None


class ApproveResponse(BaseModel):
    approval: ApprovalOut
    result:   OrderResult


# #41: 신호 출처 분류 — audit_meta + approval row 기반.
_REQUEST_SOURCE_LABELS = {
    "AI":              "AI 제안",
    "STRATEGY":        "전략 신호",
    "MANUAL":          "수동 주문",
    "LIQUIDATION":     "청산 후보",
    "RISK_OVERRIDE":   "리스크 예외 요청",
    "UNKNOWN":         "알 수 없음",
}


def _derive_request_source(audit_meta: dict, approval) -> str:
    """audit_meta + approval row → request_source 분류.

    우선순위:
    1. audit row의 source 컬럼(#40)이 있으면 그대로 (AI/STRATEGY/MANUAL/
       OPERATOR_OVERRIDE/UNKNOWN). LIQUIDATION 분류는 별도.
    2. requested_by_ai=True → AI.
    3. strategy 존재 → STRATEGY.
    4. trade_reason='liquidation' 또는 audit_meta가 청산성을 명시 → LIQUIDATION.
    5. 그 외 → MANUAL.
    """
    source = audit_meta.get("source")
    if source in ("AI", "STRATEGY", "MANUAL", "OPERATOR_OVERRIDE", "UNKNOWN"):
        # audit row source가 OPERATOR_OVERRIDE면 RISK_OVERRIDE로 (UI 명시).
        if source == "OPERATOR_OVERRIDE":
            return "RISK_OVERRIDE"
        return source
    if audit_meta.get("requested_by_ai"):
        return "AI"
    if audit_meta.get("strategy"):
        return "STRATEGY"
    trade_reason = (audit_meta.get("trade_reason") or "").lower()
    if "liquidation" in trade_reason or "stop" in trade_reason:
        return "LIQUIDATION"
    return "MANUAL"


def _ttl_fields(
    approval, *, ttl_seconds: int, now: datetime | None = None,
) -> tuple[datetime | None, int | None, bool]:
    """approval row + ttl_seconds → (expires_at, seconds_until_expiry, is_expired).

    ttl_seconds=0 또는 created_at 없음 → 모두 None / False.
    """
    if ttl_seconds <= 0 or approval.created_at is None:
        return None, None, False
    created = approval.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    expires = created + timedelta(seconds=ttl_seconds)
    cur = now or datetime.now(timezone.utc)
    delta = (expires - cur).total_seconds()
    is_expired = delta <= 0 or approval.status == STATUS_EXPIRED
    seconds_until = int(delta) if delta > 0 else 0
    return expires, seconds_until, is_expired


def _attempts_summary(approval) -> tuple[int, datetime | None, list[str]]:
    """attempts 배열 요약 → (count, last_at, last_reasons)."""
    attempts = list(approval.attempts or [])
    if not attempts:
        return 0, None, []
    last = attempts[-1]
    last_at_raw = last.get("at")
    last_at: datetime | None = None
    if isinstance(last_at_raw, str):
        try:
            last_at = datetime.fromisoformat(last_at_raw)
        except ValueError:
            last_at = None
    last_reasons = list(last.get("reasons") or [])
    return len(attempts), last_at, last_reasons


def _to_out(
    approval,
    audit_meta: dict | None = None,
    *,
    ttl_seconds: int = 0,
    now: datetime | None = None,
) -> ApprovalOut:
    audit_meta = audit_meta or {}
    expires_at, secs_until, is_expired = _ttl_fields(
        approval, ttl_seconds=ttl_seconds, now=now,
    )
    attempt_count, last_at, last_reasons = _attempts_summary(approval)
    source = _derive_request_source(audit_meta, approval)
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
        # #41 신규 필드
        expires_at=expires_at,
        seconds_until_expiry=secs_until,
        is_expired=is_expired,
        attempt_count=attempt_count,
        last_attempt_at=last_at,
        last_attempt_reasons=last_reasons,
        request_source=source,
        request_source_label=_REQUEST_SOURCE_LABELS.get(source, source),
    )


def _load_audit_meta(db: Session, approvals: list) -> dict[int, dict]:
    """audit_id → {reasons, requested_by_ai, strategy, signal_*, ai_decision_meta,
    trade_reason, source}.

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
            OrderAuditLog.trade_reason,
            OrderAuditLog.source,
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
            "trade_reason":      row[7],
            "source":            row[8],
        }
        for row in rows
    }


# Backwards-compat alias for tests / imports that may have referenced the
# old narrower helper. Returns reasons-only dict for callers that need it.
def _load_reasons(db: Session, approvals: list) -> dict[int, list]:
    return {k: v["reasons"] for k, v in _load_audit_meta(db, approvals).items()}


@router.get("", response_model=list[ApprovalOut])
def list_pending(db: Session = Depends(get_db)) -> list[ApprovalOut]:
    """현재 PENDING approvals. #41: settings.approval_ttl_seconds > 0이면
    호출 시점에 lazy expire (PermissionGate.list_pending이 처리)."""
    ttl = get_settings().approval_ttl_seconds
    approvals = PermissionGate(db).list_pending(ttl_seconds=ttl)
    meta_by_audit = _load_audit_meta(db, approvals)
    return [
        _to_out(a, meta_by_audit.get(a.audit_id), ttl_seconds=ttl)
        for a in approvals
    ]


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
    ttl = get_settings().approval_ttl_seconds
    rows = PermissionGate(db).list_decided(limit=limit, offset=offset, status=status)
    meta_by_audit = _load_audit_meta(db, rows)
    return [
        _to_out(a, meta_by_audit.get(a.audit_id), ttl_seconds=ttl)
        for a in rows
    ]


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: int, db: Session = Depends(get_db)) -> ApprovalOut:
    try:
        approval = PermissionGate(db).get(approval_id)
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    ttl = get_settings().approval_ttl_seconds
    meta = _load_audit_meta(db, [approval]).get(approval.audit_id)
    return _to_out(approval, meta, ttl_seconds=ttl)


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
    ttl = get_settings().approval_ttl_seconds
    meta = _load_audit_meta(db, [approval]).get(approval.audit_id)
    return ApproveResponse(approval=_to_out(approval, meta, ttl_seconds=ttl), result=result)


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
