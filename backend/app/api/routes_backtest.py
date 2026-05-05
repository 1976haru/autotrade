from datetime import datetime, timezone
from typing import Literal

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


# ---------- compare (parameter sweep) ----------

# Hyperparameter sweeps are O(N) over the param sets but each run is bounded
# by bars_processed. Cap N so a careless caller can't pin the worker.
MAX_COMPARE_PARAM_SETS = 50


# Whitelisted sort metrics — all "higher is better", sorted descending.
# max_drawdown is intentionally omitted because the convention there is
# "lower is better"; clients that want to rank by drawdown can sort the
# response client-side without ambiguity.
CompareSortBy = Literal["total_pnl", "sharpe_ratio", "profit_factor", "win_rate"]


class BacktestCompareRequest(BaseModel):
    """Run the same data through multiple parameter sets and rank the results.

    Bars are resolved once and reused across every param set, so a 50-set
    sweep over a market range only fetches the range once. Each run is
    persisted as a normal BacktestRun so the sweep entries show up in
    /api/audit/backtests like any other run.
    """
    strategy:     str
    param_sets:   list[dict] = Field(min_length=1, max_length=MAX_COMPARE_PARAM_SETS)
    sort_by:      CompareSortBy = "total_pnl"
    initial_cash: int = 10_000_000
    quantity:     int = 1
    bars:         list[BarPayload] | None = None
    symbol:       str | None = None
    start:        datetime | None = None
    end:          datetime | None = None
    interval:     Interval = Interval.DAY_1


class BacktestCompareResponse(BaseModel):
    sort_by:    str
    bars_processed: int
    runs:       list[BacktestResponse]


def _metric_value(resp: BacktestResponse, metric: str) -> float | None:
    return getattr(resp, metric)


def _sort_key(resp: BacktestResponse, metric: str) -> tuple[int, float]:
    """Sort key that places None values last regardless of direction.

    Returns (none_first, neg_value): tuples sort lexicographically, so any
    response with metric=None gets none_first=1 and ends up after the
    valued rows; among valued rows, larger metric values come first because
    we negate before sorting ascending.
    """
    v = _metric_value(resp, metric)
    if v is None:
        return (1, 0.0)
    return (0, -float(v))


@router.post("/compare", response_model=BacktestCompareResponse)
async def compare_backtests(
    req: BacktestCompareRequest,
    db: Session = Depends(get_db),
    upstream: MarketDataAdapter = Depends(get_market_data),
) -> BacktestCompareResponse:
    if req.initial_cash <= 0 or req.quantity <= 0:
        raise HTTPException(status_code=400, detail="initial_cash and quantity must be positive")

    # Validate every strategy + params combination up front so a bad row
    # doesn't appear several runs into a sweep with persisted partial state.
    strategies = []
    for i, params in enumerate(req.param_sets):
        try:
            strategies.append(build_strategy(req.strategy, params))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"param_sets[{i}]: {e}")

    # Bars resolved once and reused. Reuse the existing single-run resolver
    # by adapting the request shape — keeps the data-source enforcement and
    # error mapping in one place.
    proxy = BacktestRequest(
        strategy=req.strategy,
        params={},
        initial_cash=req.initial_cash,
        quantity=req.quantity,
        bars=req.bars,
        symbol=req.symbol,
        start=req.start,
        end=req.end,
        interval=req.interval,
    )
    bars, data_source, data_symbol, data_start, data_end, data_interval = await _resolve_bars(
        proxy, db, upstream,
    )

    runs: list[BacktestResponse] = []
    for params, strategy in zip(req.param_sets, strategies):
        engine = BacktestEngine(initial_cash=req.initial_cash, quantity=req.quantity)
        result = engine.run(bars, strategy)

        run = BacktestRun(
            strategy=req.strategy,
            params=dict(params),
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
        runs.append(_build_response(run, result))

    runs.sort(key=lambda r: _sort_key(r, req.sort_by))
    return BacktestCompareResponse(
        sort_by=req.sort_by,
        bars_processed=len(bars),
        runs=runs,
    )
