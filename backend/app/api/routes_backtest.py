from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_market_data
from app.backtest.engine import BacktestEngine
from app.strategies.concrete import build_strategy
from app.backtest.types import Bar, BacktestResult, Trade
from app.db.models import BacktestRun
from app.db.session import get_db
from app.market.base import Interval, MarketDataAdapter
from app.market.cache import BarCache

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
    # 둘 중 정확히 하나만 제공: 사전 준비된 bars 또는 시장 데이터 범위
    bars:         list[BarPayload] | None = None
    symbol:       str | None = None
    start:        datetime | None = None
    end:          datetime | None = None
    interval:     Interval = Interval.DAY_1


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
    # Per-trade metrics (no annualization; bar interval is opaque to the engine).
    # None when not computable (no trades / no losses / single trade / zero stdev).
    avg_win:        float
    avg_loss:       float
    profit_factor:  float | None = None
    sharpe_ratio:   float | None = None
    data_source:    str
    data_symbol:    str | None = None
    data_start:     datetime | None = None
    data_end:       datetime | None = None
    data_interval:  str | None = None
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


def _ensure_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _validate_bars(bars: list[Bar]) -> None:
    """Reject crooked bar lists before they reach the engine.

    Caller-supplied bars only — market-mode bars come from BarCache/Adapter
    and are trusted. Rules: single symbol, strictly ascending timestamps, OHLC
    consistency (high>=low and open/close inside [low, high]), positive prices,
    non-negative volume.
    """
    if not bars:
        raise ValueError("bars must not be empty")

    symbol = bars[0].symbol
    prev_ts: datetime | None = None
    for i, bar in enumerate(bars):
        if bar.symbol != symbol:
            raise ValueError(
                f"bars must all share one symbol; got {bar.symbol!r} at index {i}, "
                f"expected {symbol!r}"
            )
        if prev_ts is not None and bar.timestamp <= prev_ts:
            raise ValueError(
                f"bars must be strictly ascending by timestamp; index {i} "
                f"({bar.timestamp.isoformat()}) <= previous ({prev_ts.isoformat()})"
            )
        if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
            raise ValueError(f"bar {i}: prices must be positive")
        if bar.high < bar.low:
            raise ValueError(f"bar {i}: high ({bar.high}) < low ({bar.low})")
        if not (bar.low <= bar.open <= bar.high):
            raise ValueError(
                f"bar {i}: open ({bar.open}) outside [low={bar.low}, high={bar.high}]"
            )
        if not (bar.low <= bar.close <= bar.high):
            raise ValueError(
                f"bar {i}: close ({bar.close}) outside [low={bar.low}, high={bar.high}]"
            )
        if bar.volume < 0:
            raise ValueError(f"bar {i}: volume must be >= 0; got {bar.volume}")
        prev_ts = bar.timestamp


def _result_from_run(run: BacktestRun) -> BacktestResult:
    """Reconstruct a BacktestResult from persisted state.

    Lets `GET /runs/{id}` recompute all derived metrics (sharpe, profit factor,
    win_rate, etc.) the same way the fresh `POST /run` does — single source of
    truth for the formulas, no metric drift between the two endpoints.
    """
    trades = [Trade(
        symbol      = t["symbol"],
        entry_ts    = datetime.fromisoformat(t["entry_ts"]),
        entry_price = t["entry_price"],
        exit_ts     = datetime.fromisoformat(t["exit_ts"]),
        exit_price  = t["exit_price"],
        quantity    = t["quantity"],
        pnl         = t["pnl"],
    ) for t in run.trades_json]
    return BacktestResult(
        trades         = trades,
        initial_cash   = run.initial_cash,
        final_cash     = run.final_cash,
        bars_processed = run.bars_processed,
    )


def _build_response(run: BacktestRun, result: BacktestResult) -> BacktestResponse:
    return BacktestResponse(
        run_id=run.id,
        strategy=run.strategy,
        params=run.params,
        bars_processed=run.bars_processed,
        initial_cash=run.initial_cash,
        final_cash=run.final_cash,
        total_pnl=result.total_pnl,
        win_count=result.win_count,
        loss_count=result.loss_count,
        win_rate=result.win_rate,
        max_drawdown=result.max_drawdown,
        avg_win=result.avg_win,
        avg_loss=result.avg_loss,
        profit_factor=result.profit_factor,
        sharpe_ratio=result.sharpe_ratio,
        data_source=run.data_source,
        data_symbol=run.data_symbol,
        data_start=_ensure_utc(run.data_start),
        data_end=_ensure_utc(run.data_end),
        data_interval=run.data_interval,
        trades=[TradePayload(**t) for t in run.trades_json],
    )


async def _resolve_bars(
    req: BacktestRequest,
    db: Session,
    upstream: MarketDataAdapter,
) -> tuple[list[Bar], str, str | None, datetime | None, datetime | None, str | None]:
    has_bars = req.bars is not None
    has_market = req.symbol is not None and req.start is not None and req.end is not None

    if has_bars and has_market:
        raise HTTPException(status_code=400, detail="provide either `bars` or `(symbol, start, end)`, not both")
    if not has_bars and not has_market:
        raise HTTPException(status_code=400, detail="must provide either `bars` or `(symbol, start, end)`")

    if has_bars:
        if not req.bars:
            raise HTTPException(status_code=400, detail="bars must not be empty")
        bars = [Bar(**b.model_dump()) for b in req.bars]
        try:
            _validate_bars(bars)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return bars, "bars", None, None, None, None

    if req.start > req.end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    cache = BarCache(db)
    cached = cache.get(req.symbol, req.interval.value, req.start, req.end)
    if cached:
        bars = cached
    else:
        try:
            bars = await upstream.get_bars(req.symbol, req.start, req.end, req.interval)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        cache.save(bars, req.interval.value)

    if not bars:
        raise HTTPException(status_code=400, detail="market range yielded no bars")
    return bars, "market", req.symbol, req.start, req.end, req.interval.value


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(
    req: BacktestRequest,
    db: Session = Depends(get_db),
    upstream: MarketDataAdapter = Depends(get_market_data),
) -> BacktestResponse:
    if req.initial_cash <= 0 or req.quantity <= 0:
        raise HTTPException(status_code=400, detail="initial_cash and quantity must be positive")
    try:
        strategy = build_strategy(req.strategy, req.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    bars, data_source, data_symbol, data_start, data_end, data_interval = await _resolve_bars(
        req, db, upstream,
    )

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
        data_source=data_source,
        data_symbol=data_symbol,
        data_start=data_start,
        data_end=data_end,
        data_interval=data_interval,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    return _build_response(run, result)


@router.get("/runs/{run_id}", response_model=BacktestResponse)
def get_run(run_id: int, db: Session = Depends(get_db)) -> BacktestResponse:
    run = db.execute(select(BacktestRun).where(BacktestRun.id == run_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _build_response(run, _result_from_run(run))
