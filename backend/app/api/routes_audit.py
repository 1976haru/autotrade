from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AiAnalysisLog, BacktestRun, OrderAuditLog
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
    executed:        bool
    broker_order_id: str | None = None
    broker_status:   str | None = None
    filled_quantity: int
    avg_fill_price:  int | None = None
    message:         str


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
        executed=row.executed,
        broker_order_id=row.broker_order_id,
        broker_status=row.broker_status,
        filled_quantity=row.filled_quantity,
        avg_fill_price=row.avg_fill_price,
        message=row.message,
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
    db:     Session = Depends(get_db),
) -> list[OrderAuditOut]:
    rows = db.execute(
        select(OrderAuditLog)
        .order_by(OrderAuditLog.id.desc())
        .limit(limit).offset(offset)
    ).scalars().all()
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
