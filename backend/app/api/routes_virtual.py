"""193: VirtualOrder ledger read-only surface.

148-150에서 영구화한 VirtualOrder 라이프사이클 / fill / position 데이터를
운영자가 frontend에서 조회할 수 있도록 read-only endpoint 노출.

CLAUDE.md 절대 원칙 준수:
- 새 broker 호출 0건.
- 새 AI 실행 경로 0건.
- 새 RiskManager / PermissionGate 분기 0건.
- 본 모듈은 DB SELECT만 수행한다.
"""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import VirtualOrder
from app.db.session import get_db
from app.virtual.order_ledger import (
    STATUS_ACCEPTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_FILLED,
    STATUS_NEW, STATUS_PARTIALLY_FILLED, STATUS_REJECTED,
    TERMINAL_STATES,
)
from app.virtual.position_engine import compute_open_positions

router = APIRouter(prefix="/virtual", tags=["virtual"])


_VALID_STATUSES = {
    STATUS_NEW, STATUS_ACCEPTED, STATUS_PARTIALLY_FILLED,
    STATUS_FILLED, STATUS_CANCELLED, STATUS_REJECTED, STATUS_EXPIRED,
}


class VirtualOrderOut(BaseModel):
    id:               int
    created_at:       datetime
    updated_at:       datetime
    audit_id:         int | None = None
    symbol:           str
    side:             str
    quantity:         int
    order_type:       str
    limit_price:      int | None  = None
    requested_price:  int | None  = None
    status:           str
    structured_reason: str | None = None
    strategy:         str | None  = None
    mode:             str
    filled_quantity:  int
    avg_fill_price:   int | None  = None
    filled_at:        datetime | None = None
    note:             str | None  = None


class VirtualOrderSummary(BaseModel):
    """Counts by status for a quick operator at-a-glance view.

    pending_count = NEW + ACCEPTED + PARTIALLY_FILLED — 같은 의미: "아직 끝나지
    않은 주문 수". terminal_count = FILLED + CANCELLED + REJECTED + EXPIRED.
    """
    total:           int
    pending_count:   int
    terminal_count:  int
    by_status:       dict[str, int]


def _to_out(row: VirtualOrder) -> VirtualOrderOut:
    return VirtualOrderOut(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        audit_id=row.audit_id,
        symbol=row.symbol,
        side=row.side,
        quantity=row.quantity,
        order_type=row.order_type,
        limit_price=row.limit_price,
        requested_price=row.requested_price,
        status=row.status,
        structured_reason=row.structured_reason,
        strategy=row.strategy,
        mode=row.mode,
        filled_quantity=row.filled_quantity,
        avg_fill_price=row.avg_fill_price,
        filled_at=row.filled_at,
        note=row.note,
    )


@router.get("/orders", response_model=list[VirtualOrderOut])
def list_virtual_orders(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description=f"필터: one of {sorted(_VALID_STATUSES)}"),
    symbol: str | None = Query(None, max_length=16),
    db:     Session = Depends(get_db),
) -> list[VirtualOrderOut]:
    """가상 주문 목록 (created_at desc).

    status가 _VALID_STATUSES에 없으면 무시 (소문자 / 오타에 관용적). 빈 목록은 [].
    """
    stmt = select(VirtualOrder).order_by(VirtualOrder.id.desc())
    if status is not None and status in _VALID_STATUSES:
        stmt = stmt.where(VirtualOrder.status == status)
    if symbol:
        stmt = stmt.where(VirtualOrder.symbol == symbol)
    stmt = stmt.offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [_to_out(r) for r in rows]


class VirtualPositionOut(BaseModel):
    symbol:         str
    strategy:       str | None
    quantity:       int
    avg_price:      int
    last_price:     int
    unrealized_pnl: int
    unrealized_pct: float
    hold_seconds:   float
    realized_pnl:   int


@router.get("/positions", response_model=list[VirtualPositionOut])
def list_virtual_positions(
    last_prices: str | None = Query(
        None,
        description="콤마 구분 'symbol:price' 목록. 미지정 시 unrealized=0으로 산출.",
    ),
    db: Session = Depends(get_db),
) -> list[VirtualPositionOut]:
    """가상 포지션 요약 (FIFO 페어매칭, 148-150).

    last_prices 미지정 시 unrealized_pnl=0이 되며 realized + qty + avg는 정확.
    parser는 관용적 — `005930:75000,000660:200000` 형태. 잘못된 토큰은 skip.
    """
    prices: dict[str, int] = {}
    if last_prices:
        for tok in last_prices.split(","):
            tok = tok.strip()
            if not tok or ":" not in tok:
                continue
            sym, raw = tok.split(":", 1)
            sym = sym.strip()
            try:
                prices[sym] = int(raw.strip())
            except ValueError:
                continue
    summaries = compute_open_positions(db, last_prices=prices)
    return [
        VirtualPositionOut(
            symbol=s.symbol,
            strategy=s.strategy,
            quantity=s.quantity,
            avg_price=s.avg_price,
            last_price=s.last_price,
            unrealized_pnl=s.unrealized_pnl,
            unrealized_pct=s.unrealized_pct,
            hold_seconds=s.hold_seconds,
            realized_pnl=s.realized_pnl,
        ) for s in summaries
    ]


@router.get("/orders/summary", response_model=VirtualOrderSummary)
def virtual_orders_summary(db: Session = Depends(get_db)) -> VirtualOrderSummary:
    """status별 행 수. zero-row인 status도 0으로 채워 frontend chip 안정화."""
    rows = db.execute(
        select(VirtualOrder.status, func.count(VirtualOrder.id))
        .group_by(VirtualOrder.status)
    ).all()
    counts = Counter()
    for status, n in rows:
        counts[status] += int(n or 0)
    by_status: dict[str, int] = {s: counts.get(s, 0) for s in _VALID_STATUSES}
    pending = sum(by_status.get(s, 0) for s in (
        STATUS_NEW, STATUS_ACCEPTED, STATUS_PARTIALLY_FILLED,
    ))
    terminal = sum(by_status.get(s, 0) for s in TERMINAL_STATES)
    return VirtualOrderSummary(
        total=pending + terminal,
        pending_count=pending,
        terminal_count=terminal,
        by_status=by_status,
    )
