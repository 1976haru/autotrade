"""Agent Memory API — read-mostly 학습 저장소 endpoint.

검색 / 단건 조회 / 운영자 메모 추가 / archive toggle / 자동 ingest helper.

**절대 원칙 준수**:
- 본 모듈은 broker / OrderExecutor / route_order를 *직접* 호출하지 않는다.
- 입력 텍스트는 모두 `app.agents.agent_memory.sanitize_text`를 통과 — 민감
  정보(API key / Secret / 계좌번호 / 개인정보)는 raise하여 저장 차단.
- 검색 결과는 *주문 신호가 아니다* — UI에서 BUY/SELL 버튼 생성 금지.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.agent_memory import (
    MemoryRecord,
    MemorySearchFilter,
    MemorySeverity,
    MemoryType,
    MemoryWriteRequest,
    SecretLeakError,
    SourceKind,
    archive_memory,
    get_memory,
    memory_from_daily_report_markdown,
    memory_from_risk_audit_report,
    memory_from_strategy_research_report,
    save_memory,
    search_memory,
)
from app.db.session import get_db


router = APIRouter(prefix="/agents/memory", tags=["agents"])


# ====================================================================
# Pydantic schemas
# ====================================================================


class MemoryRecordOut(BaseModel):
    id:           int
    created_at:   str | None
    updated_at:   str | None
    memory_type:  str
    source_kind:  str | None
    source_id:    int | None
    strategy:     str | None
    symbol:       str | None
    mode:         str | None
    severity:     str
    title:        str
    summary:      str
    lessons:      str | None
    next_action:  str | None
    tags:         list[str]
    meta:         dict[str, Any]
    author:       str | None
    archived:     bool
    is_order_signal: bool


def _to_out(rec: MemoryRecord) -> MemoryRecordOut:
    return MemoryRecordOut(**rec.to_dict())


class MemorySearchOut(BaseModel):
    items: list[MemoryRecordOut]
    notice: str = (
        "본 결과는 주문 신호가 아닙니다. 과거 학습 / 운영 자료입니다 — "
        "RiskManager / PermissionGate / OrderExecutor 우회에 사용 금지."
    )


class MemoryWriteIn(BaseModel):
    memory_type:  str = "operator_note"
    source_kind:  str | None = "operator"
    source_id:    int | None = None
    strategy:     str | None = None
    symbol:       str | None = None
    mode:         str | None = None
    severity:     str = "INFO"
    title:        str = Field(min_length=1, max_length=200)
    summary:      str = Field(min_length=1)
    lessons:      str | None = None
    next_action:  str | None = None
    tags:         list[str] = Field(default_factory=list)
    meta:         dict[str, Any] = Field(default_factory=dict)
    author:       str | None = None


class MemoryArchiveIn(BaseModel):
    archived: bool = True


class IngestDailyReportIn(BaseModel):
    report_date:    str
    markdown:       str
    findings_count: int = 0
    warnings_count: int = 0
    operator_note:  str | None = None


class IngestStrategyResearchIn(BaseModel):
    strategy:          str
    run_id:            int
    audit_level:       str
    summary:           str
    findings_count:    int = 0
    suggestions_count: int = 0
    operator_note:     str | None = None


class IngestRiskAuditIn(BaseModel):
    audit_level:       str
    risk_score:        int
    summary:           str
    pause_recommended: bool = False
    stop_recommended:  bool = False
    events_count:      int = 0
    operator_note:     str | None = None


# ====================================================================
# Endpoints
# ====================================================================


def _parse_enum(cls, value: str | None):
    if value is None:
        return None
    try:
        return cls(value)
    except ValueError:
        valid = [m.value for m in cls]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {cls.__name__}: {value} (valid: {valid})",
        )


@router.get("/search", response_model=MemorySearchOut)
def memory_search(
    memory_type:      str | None = Query(None),
    source_kind:      str | None = Query(None),
    strategy:         str | None = Query(None),
    symbol:           str | None = Query(None),
    mode:             str | None = Query(None),
    severity:         str | None = Query(None),
    tag:              str | None = Query(None),
    keyword:          str | None = Query(None),
    include_archived: bool       = Query(False),
    limit:            int        = Query(50, ge=1, le=500),
    offset:           int        = Query(0, ge=0),
    db:               Session    = Depends(get_db),
) -> MemorySearchOut:
    """Agent Memory 검색. 모든 필터는 AND. 결과는 주문 신호가 *아님*."""
    flt = MemorySearchFilter(
        memory_type=_parse_enum(MemoryType, memory_type),
        source_kind=_parse_enum(SourceKind, source_kind),
        strategy=strategy, symbol=symbol, mode=mode,
        severity=_parse_enum(MemorySeverity, severity),
        tag=tag, keyword=keyword,
        include_archived=include_archived,
        limit=limit, offset=offset,
    )
    rows = search_memory(db, flt)
    return MemorySearchOut(items=[_to_out(r) for r in rows])


@router.get("/{memory_id}", response_model=MemoryRecordOut)
def memory_get(memory_id: int, db: Session = Depends(get_db)) -> MemoryRecordOut:
    rec = get_memory(db, memory_id)
    if rec is None:
        raise HTTPException(status_code=404,
                            detail=f"AgentMemory {memory_id} not found")
    return _to_out(rec)


@router.post("", response_model=MemoryRecordOut)
def memory_create(body: MemoryWriteIn,
                  db: Session = Depends(get_db)) -> MemoryRecordOut:
    """수동 운영 메모 추가. 입력은 sanitize 통과해야 함 — 민감정보 발견 시 400."""
    try:
        req = MemoryWriteRequest(
            memory_type=_parse_enum(MemoryType, body.memory_type) or MemoryType.OPERATOR_NOTE,
            source_kind=_parse_enum(SourceKind, body.source_kind),
            source_id=body.source_id,
            strategy=body.strategy, symbol=body.symbol, mode=body.mode,
            severity=_parse_enum(MemorySeverity, body.severity) or MemorySeverity.INFO,
            title=body.title, summary=body.summary,
            lessons=body.lessons, next_action=body.next_action,
            tags=tuple(body.tags), meta=body.meta or {},
            author=body.author,
        )
        rec = save_memory(db, req)
    except SecretLeakError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error":   "secret_leak_blocked",
                "message": str(e),
                "policy":  "Agent Memory에는 API key / Secret / 계좌번호 / "
                           "개인정보를 저장할 수 없습니다.",
            },
        )
    return _to_out(rec)


@router.post("/{memory_id}/archive", response_model=MemoryRecordOut)
def memory_archive_route(
    memory_id: int,
    body: MemoryArchiveIn,
    db: Session = Depends(get_db),
) -> MemoryRecordOut:
    rec = archive_memory(db, memory_id, archived=body.archived)
    if rec is None:
        raise HTTPException(status_code=404,
                            detail=f"AgentMemory {memory_id} not found")
    return _to_out(rec)


@router.post("/from-daily-report", response_model=MemoryRecordOut)
def memory_from_daily_report_route(
    body: IngestDailyReportIn,
    db: Session = Depends(get_db),
) -> MemoryRecordOut:
    """#57 Daily Report → AgentMemory ingest. sanitize 후 저장."""
    try:
        req = memory_from_daily_report_markdown(
            report_date=body.report_date,
            markdown=body.markdown,
            findings_count=body.findings_count,
            warnings_count=body.warnings_count,
            operator_note=body.operator_note,
        )
        rec = save_memory(db, req)
    except SecretLeakError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "secret_leak_blocked", "message": str(e)},
        )
    return _to_out(rec)


@router.post("/from-strategy-research", response_model=MemoryRecordOut)
def memory_from_strategy_research_route(
    body: IngestStrategyResearchIn,
    db: Session = Depends(get_db),
) -> MemoryRecordOut:
    """#55 Strategy Researcher → AgentMemory ingest."""
    try:
        req = memory_from_strategy_research_report(
            strategy=body.strategy, run_id=body.run_id,
            audit_level=body.audit_level, summary=body.summary,
            findings_count=body.findings_count,
            suggestions_count=body.suggestions_count,
            operator_note=body.operator_note,
        )
        rec = save_memory(db, req)
    except SecretLeakError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "secret_leak_blocked", "message": str(e)},
        )
    return _to_out(rec)


@router.post("/from-risk-audit", response_model=MemoryRecordOut)
def memory_from_risk_audit_route(
    body: IngestRiskAuditIn,
    db: Session = Depends(get_db),
) -> MemoryRecordOut:
    """#54 Risk Auditor → AgentMemory ingest."""
    try:
        req = memory_from_risk_audit_report(
            audit_level=body.audit_level, risk_score=body.risk_score,
            summary=body.summary,
            pause_recommended=body.pause_recommended,
            stop_recommended=body.stop_recommended,
            events_count=body.events_count,
            operator_note=body.operator_note,
        )
        rec = save_memory(db, req)
    except SecretLeakError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "secret_leak_blocked", "message": str(e)},
        )
    return _to_out(rec)
