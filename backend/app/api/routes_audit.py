from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AiAnalysisLog, AuditEvent, BacktestRun, OrderAuditLog
from app.db.session import get_db


router = APIRouter(prefix="/audit", tags=["audit"])


class OrderAuditOut(BaseModel):
    id:              int
    created_at:      datetime
    mode:            str
    requested_by_ai: bool
    symbol:          str
    side:            str
    quantity:        int
    order_type:      str
    limit_price:     int | None = None
    latest_price:    int
    decision:        str
    reasons:         list
    # 134: 자유 문자열 사유. 0005 이전 row + 호출자 미명시 입력은 NULL.
    trade_reason:    str | None = None
    # 138: 주문을 만든 전략 이름. 0006 이전 + 수동 주문은 NULL.
    strategy:        str | None = None
    # 139: 신호 quality 0-100. 산출 안 된 경로 + 0007 이전 row는 NULL.
    signal_strength:   int | None = None
    signal_confidence: int | None = None
    # 140: idempotency 키 (호출자 발급). 미명시 + 0008 이전 row는 NULL.
    client_order_id:   str | None = None
    executed:        bool
    broker_order_id: str | None = None
    broker_status:   str | None = None
    filled_quantity: int
    avg_fill_price:  int | None = None
    message:         str
    # 189: AI 결정 메타 (152의 0010 마이그레이션 컬럼).
    # 운영자가 거부 사유 / confidence / reasoning을 audit에서 직접 볼 수 있어야 한다.
    # 0010 이전 row + AI 미경유 주문은 NULL.
    ai_decision_meta: dict | None = None
    # 198: 168 archival flag — frontend AuditLog에서 archived sub-tab을 만들어
    # cold rows를 분리해서 볼 수 있도록 surface한다.
    archived:         bool = False
    # #40: 주문 source 분류 — STRATEGY / AI / MANUAL / OPERATOR_OVERRIDE / UNKNOWN.
    # 0018 이전 row + 호출자 미명시는 NULL → frontend에서 'UNKNOWN' 표시 권장.
    source:           str | None = None


class AiAuditOut(BaseModel):
    id:            int
    created_at:    datetime
    ticker:        str
    extra:         str
    active_strats: list
    risk_params:   dict
    # 123: 호출 시점 운용모드. 0004 마이그레이션 이전 row는 NULL (FE의 ModeBadge가
    # null이면 미렌더해 자연스럽게 hidden).
    mode:          str | None = None
    text:          str | None = None
    model:         str | None = None
    input_tokens:  int
    output_tokens: int
    score:         dict | None = None
    error:         str | None = None


class BacktestSummaryOut(BaseModel):
    id:             int
    created_at:     datetime
    strategy:       str
    params:         dict
    initial_cash:   int
    quantity:       int
    bars_processed: int
    final_cash:     int
    total_pnl:      int
    win_count:      int
    loss_count:     int
    max_drawdown:   int
    data_source:    str
    data_symbol:    str | None = None


def _ensure_utc(ts: datetime | None) -> datetime | None:
    if ts is None or ts.tzinfo is not None:
        return ts
    return ts.replace(tzinfo=timezone.utc)


def _to_order_out(row: OrderAuditLog) -> OrderAuditOut:
    return OrderAuditOut(
        id=row.id,
        created_at=_ensure_utc(row.created_at),
        mode=row.mode,
        requested_by_ai=row.requested_by_ai,
        symbol=row.symbol,
        side=row.side,
        quantity=row.quantity,
        order_type=row.order_type,
        limit_price=row.limit_price,
        latest_price=row.latest_price,
        decision=row.decision,
        reasons=list(row.reasons or []),
        trade_reason=row.trade_reason,
        strategy=row.strategy,
        signal_strength=row.signal_strength,
        signal_confidence=row.signal_confidence,
        client_order_id=row.client_order_id,
        executed=row.executed,
        broker_order_id=row.broker_order_id,
        broker_status=row.broker_status,
        filled_quantity=row.filled_quantity,
        avg_fill_price=row.avg_fill_price,
        message=row.message,
        ai_decision_meta=row.ai_decision_meta,
        archived=bool(row.archived),
        source=getattr(row, "source", None),  # #40
    )


def _to_ai_out(row: AiAnalysisLog) -> AiAuditOut:
    return AiAuditOut(
        id=row.id,
        created_at=_ensure_utc(row.created_at),
        ticker=row.ticker,
        extra=row.extra,
        active_strats=list(row.active_strats or []),
        risk_params=dict(row.risk_params or {}),
        mode=row.mode,
        text=row.text,
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        score=row.score,
        error=row.error,
    )


def _to_backtest_out(row: BacktestRun) -> BacktestSummaryOut:
    return BacktestSummaryOut(
        id=row.id,
        created_at=_ensure_utc(row.created_at),
        strategy=row.strategy,
        params=dict(row.params or {}),
        initial_cash=row.initial_cash,
        quantity=row.quantity,
        bars_processed=row.bars_processed,
        final_cash=row.final_cash,
        total_pnl=row.total_pnl,
        win_count=row.win_count,
        loss_count=row.loss_count,
        max_drawdown=row.max_drawdown,
        data_source=row.data_source,
        data_symbol=row.data_symbol,
    )


@router.get("/orders", response_model=list[OrderAuditOut])
def list_order_audits(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_archived: bool = Query(False, description="168: cold rows 포함 여부"),
    db:     Session = Depends(get_db),
) -> list[OrderAuditOut]:
    """기본 hot rows만 반환 — 168 archival flag로 분리. 운영자가 cold도 보고
    싶으면 ?include_archived=true."""
    stmt = select(OrderAuditLog).order_by(OrderAuditLog.id.desc())
    if not include_archived:
        stmt = stmt.where(OrderAuditLog.archived.is_(False))
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [_to_order_out(r) for r in rows]


@router.get("/ai", response_model=list[AiAuditOut])
def list_ai_audits(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db:     Session = Depends(get_db),
) -> list[AiAuditOut]:
    rows = db.execute(
        select(AiAnalysisLog)
        .order_by(AiAnalysisLog.id.desc())
        .limit(limit).offset(offset)
    ).scalars().all()
    return [_to_ai_out(r) for r in rows]


@router.get("/backtests", response_model=list[BacktestSummaryOut])
def list_backtest_runs(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db:     Session = Depends(get_db),
) -> list[BacktestSummaryOut]:
    rows = db.execute(
        select(BacktestRun)
        .order_by(BacktestRun.id.desc())
        .limit(limit).offset(offset)
    ).scalars().all()
    return [_to_backtest_out(r) for r in rows]


# ====================================================================
# #68: 통합 audit_event timeline — read-only + archive only.
# DELETE 엔드포인트는 *의도적으로 추가하지 않는다*. archive PATCH만.
# ====================================================================


from fastapi import HTTPException, Body  # noqa: E402 — 라우트 블록 안 import
from pydantic import Field               # noqa: E402

from app.audit.events import (           # noqa: E402
    AuditEventNotFoundError,
    SecretLeakError,
    archive_event,
    log_audit_event,
)


class AuditEventOut(BaseModel):
    id:           int
    created_at:   datetime
    event_type:   str
    severity:     str
    source:       str
    actor:        str | None = None
    symbol:       str | None = None
    strategy:     str | None = None
    mode:         str | None = None
    target_kind:  str | None = None
    target_id:    int | None = None
    summary:      str
    reason:       str | None = None
    details:      dict | None = None
    archived:     bool       = False
    archived_at:  datetime | None = None
    archived_by:  str | None = None
    archive_note: str | None = None


def _to_event_out(row: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=row.id,
        created_at=_ensure_utc(row.created_at),
        event_type=row.event_type,
        severity=row.severity,
        source=row.source,
        actor=row.actor,
        symbol=row.symbol,
        strategy=row.strategy,
        mode=row.mode,
        target_kind=row.target_kind,
        target_id=row.target_id,
        summary=row.summary,
        reason=row.reason,
        details=row.details,
        archived=bool(row.archived),
        archived_at=_ensure_utc(row.archived_at) if row.archived_at else None,
        archived_by=row.archived_by,
        archive_note=row.archive_note,
    )


@router.get("/events", response_model=list[AuditEventOut])
def list_audit_events(
    limit:       int  = Query(50, ge=1, le=200),
    offset:      int  = Query(0, ge=0),
    event_type:  str | None = Query(None, description="event_type filter"),
    severity:    str | None = Query(None, description="severity filter"),
    source:      str | None = Query(None, description="source filter"),
    symbol:      str | None = Query(None, description="symbol filter"),
    strategy:    str | None = Query(None, description="strategy filter"),
    actor:       str | None = Query(None, description="actor filter"),
    include_archived: bool = Query(False, description="archived row 포함"),
    db: Session = Depends(get_db),
) -> list[AuditEventOut]:
    """#68: 통합 감사 이벤트 timeline (read-only).

    기존 OrderAuditLog / PendingApproval / AgentDecisionLog / EmergencyStopEvent
    / VirtualOrder / FuturesOrderAuditLog는 그대로 보존되며, 본 timeline은 그
    위의 cross-cutting view. 새 hook이 INSERT한 audit_event만 본 endpoint에서
    조회된다.

    **DELETE 엔드포인트는 의도적으로 없다** — audit row 삭제 = 감사 추적 손실.
    """
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc())
    if not include_archived:
        stmt = stmt.where(AuditEvent.archived.is_(False))
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if severity:
        stmt = stmt.where(AuditEvent.severity == severity)
    if source:
        stmt = stmt.where(AuditEvent.source == source)
    if symbol:
        stmt = stmt.where(AuditEvent.symbol == symbol)
    if strategy:
        stmt = stmt.where(AuditEvent.strategy == strategy)
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [_to_event_out(r) for r in rows]


@router.get("/events/{event_id}", response_model=AuditEventOut)
def get_audit_event(event_id: int, db: Session = Depends(get_db)) -> AuditEventOut:
    """단건 상세."""
    row = db.execute(
        select(AuditEvent).where(AuditEvent.id == event_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="audit_event not found")
    return _to_event_out(row)


class OperatorNoteIn(BaseModel):
    """수동 OPERATOR_NOTE 이벤트 — 운영자가 운영 메모 / incident 기록 등 추가.

    실 broker 주문 / risk 결정 / AI 제안은 본 endpoint로 만들지 *않는다* —
    그것들은 각 라우트의 hook으로만 추가됨.
    """
    summary:     str  = Field(..., min_length=1, max_length=255)
    reason:      str | None = Field(default=None, max_length=255)
    symbol:      str | None = Field(default=None, max_length=16)
    strategy:    str | None = Field(default=None, max_length=64)
    actor:       str | None = Field(default=None, max_length=64)
    details:     dict | None = None


@router.post("/events", response_model=AuditEventOut, status_code=201)
def post_operator_note(
    body: OperatorNoteIn,
    db:   Session = Depends(get_db),
) -> AuditEventOut:
    """OPERATOR_NOTE 한 건 추가. event_type / severity / source는 *고정* —
    OPERATOR_NOTE / INFO / OPERATOR.

    Secret 패턴이 summary / reason / details 어디에 있으면 SecretLeakError
    → 400. *redaction 아닌 거부* 정책.
    """
    try:
        row = log_audit_event(
            db,
            event_type="OPERATOR_NOTE",
            severity="INFO",
            source="OPERATOR",
            actor=body.actor,
            symbol=body.symbol,
            strategy=body.strategy,
            summary=body.summary,
            reason=body.reason,
            details=body.details,
        )
    except SecretLeakError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error":   "secret_leak_blocked",
                "message": str(exc),
            },
        ) from exc
    return _to_event_out(row)


class ArchiveIn(BaseModel):
    archived_by: str | None = Field(default=None, max_length=64)
    note:        str | None = Field(default=None, max_length=255)


@router.patch("/events/{event_id}/archive", response_model=AuditEventOut)
def patch_archive_event(
    event_id: int,
    body: ArchiveIn | None = Body(default=None),
    db: Session = Depends(get_db),
) -> AuditEventOut:
    """archive flag set — delete *대체*. row는 그대로 보존.

    **본 endpoint는 row를 삭제하지 않는다.** 운영자가 노이즈를 분리하거나 cold
    storage로 옮기고 싶을 때 archived=True로 표시만 한다. 멱등 — 이미 archived
    인 row에 다시 호출해도 archived_by/note는 *덮어쓰지 않음*.
    """
    payload = body or ArchiveIn()
    try:
        row = archive_event(
            db, event_id,
            archived_by=payload.archived_by, note=payload.note,
        )
    except AuditEventNotFoundError:
        raise HTTPException(status_code=404, detail="audit_event not found") from None
    return _to_event_out(row)
