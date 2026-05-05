from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestEngine
from app.backtest.strategies import build_strategy
from app.backtest.types import Bar
from app.db.models import BacktestRun
from app.db.session import get_db

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BarPayload(BaseModel):
    symbol:    str
    timestamp: datetime
    open:      int
    high:      int
    low:       int
    close:     int
    volume:    int


class TradePayload(BaseModel):
    symbol:      str
    entry_ts:    datetime
    entry_price: int
    exit_ts:     datetime
    exit_price:  int
    quantity:    int
    pnl:         int


class BacktestRequest(BaseModel):
    strategy:     str
    params:       dict = Field(default_factory=dict)
    initial_cash: int = 10_000_000
    quantity:     int = 1
    bars:         list[BarPayload]


class BacktestResponse(BaseModel):
    run_id:         int
    strategy:       str
    params:         dict
    bars_processed: int
    initial_cash:   int
    final_cash:     int
    total_pnl:      int
    win_count:      int
    loss_count:     int
    win_rate:       float
    max_drawdown:   int
    trades:         list[TradePayload]


def _trade_to_dict(t) -> dict:
    return {
        "symbol":      t.symbol,
        "entry_ts":    t.entry_ts.isoformat(),
        "entry_price": t.entry_price,
        "exit_ts":     t.exit_ts.isoformat(),
        "exit_price":  t.exit_price,
        "quantity":    t.quantity,
        "pnl":         t.pnl,
    }


def _build_response(run: BacktestRun, win_rate: float) -> BacktestResponse:
    return BacktestResponse(
        run_id=run.id,
        strategy=run.strategy,
        params=run.params,
        bars_processed=run.bars_processed,
        initial_cash=run.initial_cash,
        final_cash=run.final_cash,
        total_pnl=run.total_pnl,
        win_count=run.win_count,
        loss_count=run.loss_count,
        win_rate=win_rate,
        max_drawdown=run.max_drawdown,
        trades=[TradePayload(**t) for t in run.trades_json],
    )


@router.post("/run", response_model=BacktestResponse)
def run_backtest(req: BacktestRequest, db: Session = Depends(get_db)) -> BacktestResponse:
    if not req.bars:
        raise HTTPException(status_code=400, detail="bars must not be empty")
    if req.initial_cash <= 0 or req.quantity <= 0:
        raise HTTPException(status_code=400, detail="initial_cash and quantity must be positive")
    try:
        strategy = build_strategy(req.strategy, req.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    bars = [Bar(**b.model_dump()) for b in req.bars]
    engine = BacktestEngine(initial_cash=req.initial_cash, quantity=req.quantity)
    result = engine.run(bars, strategy)

    run = BacktestRun(
        strategy=req.strategy,
        params=dict(req.params),
        initial_cash=result.initial_cash,
        quantity=req.quantity,
        bars_processed=result.bars_processed,
        final_cash=result.final_cash,
        total_pnl=result.total_pnl,
        win_count=result.win_count,
        loss_count=result.loss_count,
        max_drawdown=result.max_drawdown,
        trades_json=[_trade_to_dict(t) for t in result.trades],
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    return _build_response(run, result.win_rate)


@router.get("/runs/{run_id}", response_model=BacktestResponse)
def get_run(run_id: int, db: Session = Depends(get_db)) -> BacktestResponse:
    run = db.execute(select(BacktestRun).where(BacktestRun.id == run_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    total = run.win_count + run.loss_count
    win_rate = run.win_count / total if total else 0.0
    return _build_response(run, win_rate)
