"""194: read-only surface for FuturesOrderAuditLog (169).

CLAUDE.md 절대 원칙 준수:
- 새 broker 호출 0건 (`MockFuturesBroker`만 호출하는 다른 모듈이 row를 만든다).
- LIVE 활성화 0건 — 본 모듈은 SELECT만.
- `ENABLE_FUTURES_LIVE_TRADING=false` 환경에서도 UI가 mock 데이터를 보여줄 수 있도록.
"""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FuturesOrderAuditLog
from app.db.session import get_db

router = APIRouter(prefix="/futures", tags=["futures"])


class FuturesOrderOut(BaseModel):
    id:                int
    created_at:        datetime
    mode:              str
    contract:          str
    side:              str
    quantity:          int
    order_type:        str
    limit_price:       int | None  = None
    leverage:          float
    decision:          str
    reasons:           list
    executed:          bool
    broker_status:     str | None  = None
    filled_quantity:   int
    avg_fill_price:    int | None  = None
    margin_delta:      int
    liquidation_price: int | None  = None
    forced_liquidation: bool
    message:           str


class FuturesOrderSummary(BaseModel):
    """선물 주문 카운트 + 강제청산 수 + 누적 margin 변동.

    forced_liquidation_count는 운영자에게 가장 중요한 지표 — 0이 정상.
    """
    total:                    int
    by_decision:              dict[str, int]
    forced_liquidation_count: int
    executed_count:           int
    cumulative_margin_delta:  int


def _to_out(row: FuturesOrderAuditLog) -> FuturesOrderOut:
    return FuturesOrderOut(
        id=row.id,
        created_at=row.created_at,
        mode=row.mode,
        contract=row.contract,
        side=row.side,
        quantity=row.quantity,
        order_type=row.order_type,
        limit_price=row.limit_price,
        leverage=float(row.leverage or 1.0),
        decision=row.decision,
        reasons=list(row.reasons or []),
        executed=bool(row.executed),
        broker_status=row.broker_status,
        filled_quantity=int(row.filled_quantity or 0),
        avg_fill_price=row.avg_fill_price,
        margin_delta=int(row.margin_delta or 0),
        liquidation_price=row.liquidation_price,
        forced_liquidation=bool(row.forced_liquidation),
        message=row.message or "",
    )


@router.get("/orders", response_model=list[FuturesOrderOut])
def list_futures_orders(
    limit:    int = Query(50, ge=1, le=200),
    offset:   int = Query(0, ge=0),
    contract: str | None  = Query(None, max_length=32),
    decision: str | None  = Query(None),
    forced:   bool | None = Query(None, description="True → forced_liquidation만 / False → 일반만"),
    db:       Session = Depends(get_db),
) -> list[FuturesOrderOut]:
    stmt = select(FuturesOrderAuditLog).order_by(FuturesOrderAuditLog.id.desc())
    if contract:
        stmt = stmt.where(FuturesOrderAuditLog.contract == contract)
    if decision:
        stmt = stmt.where(FuturesOrderAuditLog.decision == decision)
    if forced is not None:
        stmt = stmt.where(FuturesOrderAuditLog.forced_liquidation == forced)
    stmt = stmt.offset(offset).limit(limit)
    return [_to_out(r) for r in db.execute(stmt).scalars().all()]


@router.get("/orders/summary", response_model=FuturesOrderSummary)
def futures_orders_summary(db: Session = Depends(get_db)) -> FuturesOrderSummary:
    decisions = db.execute(
        select(FuturesOrderAuditLog.decision, func.count(FuturesOrderAuditLog.id))
        .group_by(FuturesOrderAuditLog.decision)
    ).all()
    by_decision: dict[str, int] = {}
    total = 0
    for decision, n in decisions:
        c = int(n or 0)
        by_decision[decision] = c
        total += c
    forced = db.execute(
        select(func.count(FuturesOrderAuditLog.id))
        .where(FuturesOrderAuditLog.forced_liquidation.is_(True))
    ).scalar_one() or 0
    executed = db.execute(
        select(func.count(FuturesOrderAuditLog.id))
        .where(FuturesOrderAuditLog.executed.is_(True))
    ).scalar_one() or 0
    margin_sum = db.execute(
        select(func.coalesce(func.sum(FuturesOrderAuditLog.margin_delta), 0))
    ).scalar_one() or 0
    return FuturesOrderSummary(
        total=total,
        by_decision=Counter(by_decision),
        forced_liquidation_count=int(forced),
        executed_count=int(executed),
        cumulative_margin_delta=int(margin_sum),
    )
