from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_market_data
from app.backtest.engine import BacktestEngine
from app.strategies.concrete import build_strategy
from app.backtest.types import Bar, BacktestConfig, BacktestResult, Trade
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
    # 23: 비용 모델 — config 미제공 시 모두 0/None.
    entry_signal_price: int | None = None
    exit_signal_price:  int | None = None
    fees:               int = 0
    taxes:              int = 0
    slippage_cost:      int = 0


class BacktestConfigPayload(BaseModel):
    """클라이언트가 명시적으로 보내는 config (#23). 미제공 시 legacy 동작."""
    execution_model:           str  = "next_open"
    execution_delay_bars:      int  = 1
    allow_same_bar_execution:  bool = False
    slippage_bps:              int  = 0
    commission_bps:            int  = 0
    tax_bps:                   int  = 0
    exit_on_last_bar:          bool = True


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
    # 23: 체결 모델 + 비용 모델. 미제공 시 legacy(same_close, 비용 0).
    config:       BacktestConfigPayload | None = None


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
    # 23: 비용 모델 — config 미제공 시 0.
    gross_pnl:      int = 0
    net_pnl:        int = 0
    total_fees:     int = 0
    total_taxes:    int = 0
    total_slippage: int = 0
    config:         BacktestConfigPayload | None = None
    # 24: 신규 지표 — 거래 0건/계산 불가 시 안전 default.
    expectancy:             float = 0.0
    flat_count:             int   = 0
    max_consecutive_wins:   int   = 0
    max_consecutive_losses: int   = 0
    hourly_pnl:             dict[int, int] = Field(default_factory=dict)


def _trade_to_dict(t) -> dict:
    return {
        "symbol":             t.symbol,
        "entry_ts":           t.entry_ts.isoformat(),
        "entry_price":        t.entry_price,
        "exit_ts":            t.exit_ts.isoformat(),
        "exit_price":         t.exit_price,
        "quantity":           t.quantity,
        "pnl":                t.pnl,
        "entry_signal_price": t.entry_signal_price,
        "exit_signal_price":  t.exit_signal_price,
        "fees":               t.fees,
        "taxes":              t.taxes,
        "slippage_cost":      t.slippage_cost,
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
        symbol             = t["symbol"],
        entry_ts           = datetime.fromisoformat(t["entry_ts"]),
        entry_price        = t["entry_price"],
        exit_ts            = datetime.fromisoformat(t["exit_ts"]),
        exit_price         = t["exit_price"],
        quantity           = t["quantity"],
        pnl                = t["pnl"],
        entry_signal_price = t.get("entry_signal_price"),
        exit_signal_price  = t.get("exit_signal_price"),
        fees               = int(t.get("fees", 0) or 0),
        taxes              = int(t.get("taxes", 0) or 0),
        slippage_cost      = int(t.get("slippage_cost", 0) or 0),
    ) for t in run.trades_json]
    return BacktestResult(
        trades         = trades,
        initial_cash   = run.initial_cash,
        final_cash     = run.final_cash,
        bars_processed = run.bars_processed,
    )


def _build_response(
    run: BacktestRun,
    result: BacktestResult,
    *,
    config: BacktestConfigPayload | None = None,
) -> BacktestResponse:
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
        gross_pnl=result.gross_pnl,
        net_pnl=result.net_pnl,
        total_fees=result.total_fees,
        total_taxes=result.total_taxes,
        total_slippage=result.total_slippage,
        config=config,
        expectancy=result.expectancy,
        flat_count=result.flat_count,
        max_consecutive_wins=result.max_consecutive_wins,
        max_consecutive_losses=result.max_consecutive_losses,
        hourly_pnl=result.hourly_pnl,
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
    cfg: BacktestConfig | None = None
    if req.config is not None:
        try:
            cfg = BacktestConfig(**req.config.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    result = engine.run(bars, strategy, config=cfg)

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

    return _build_response(run, result, config=req.config)


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
    config:       BacktestConfigPayload | None = None


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

    cfg: BacktestConfig | None = None
    if req.config is not None:
        try:
            cfg = BacktestConfig(**req.config.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    runs: list[BacktestResponse] = []
    for params, strategy in zip(req.param_sets, strategies):
        engine = BacktestEngine(initial_cash=req.initial_cash, quantity=req.quantity)
        result = engine.run(bars, strategy, config=cfg)

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
        runs.append(_build_response(run, result, config=req.config))

    runs.sort(key=lambda r: _sort_key(r, req.sort_by))
    return BacktestCompareResponse(
        sort_by=req.sort_by,
        bars_processed=len(bars),
        runs=runs,
    )


# ---------- walk-forward (#25) ----------


class WalkForwardConfigPayload(BaseModel):
    mode:                       str   = "rolling"
    train_days:                 int   = 60
    validation_days:            int   = 20
    step_days:                  int   = 0
    holdout_days:               int   = 30
    min_fold_count:             int   = 3
    min_positive_fold_ratio:    float = 0.6
    max_single_fold_pnl_share:  float = 0.7
    min_holdout_pnl:            int   = 0


class WalkForwardRequest(BaseModel):
    """학습기간/검증기간/holdout으로 나눈 walk-forward 백테스트.

    bars 또는 (symbol, start, end)를 BacktestRequest와 동일하게 받는다 — 한
    번 fetch한 bars를 fold마다 슬라이싱.
    """
    strategy:     str
    params:       dict = Field(default_factory=dict)
    initial_cash: int = 10_000_000
    quantity:     int = 1
    bars:         list[BarPayload] | None = None
    symbol:       str | None = None
    start:        datetime | None = None
    end:          datetime | None = None
    interval:     Interval = Interval.DAY_1
    config:       BacktestConfigPayload | None = None
    walk_forward: WalkForwardConfigPayload = Field(default_factory=WalkForwardConfigPayload)


class WalkForwardResponse(BaseModel):
    config:                  dict
    folds:                   list[dict]
    holdout_metrics:         dict | None = None
    holdout_window:          dict | None = None
    summary:                 dict
    promotion_recommendation: str
    warnings:                list[str]
    overfit_flags:           list[str]
    bars_processed:          int


@router.post("/walk-forward", response_model=WalkForwardResponse)
async def run_walk_forward_endpoint(
    req: WalkForwardRequest,
    db: Session = Depends(get_db),
    upstream: MarketDataAdapter = Depends(get_market_data),
) -> WalkForwardResponse:
    if req.initial_cash <= 0 or req.quantity <= 0:
        raise HTTPException(status_code=400, detail="initial_cash and quantity must be positive")

    try:
        # 검증 — strategy / params 조합이 valid한지 fold 진입 전에 한 번만.
        from app.strategies.concrete import build_strategy
        build_strategy(req.strategy, req.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    proxy = BacktestRequest(
        strategy=req.strategy, params=req.params,
        initial_cash=req.initial_cash, quantity=req.quantity,
        bars=req.bars, symbol=req.symbol,
        start=req.start, end=req.end, interval=req.interval,
    )
    bars, _data_source, _ds, _start, _end, _intv = await _resolve_bars(proxy, db, upstream)

    bt_cfg: BacktestConfig | None = None
    if req.config is not None:
        try:
            bt_cfg = BacktestConfig(**req.config.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    from app.backtest.walk_forward_runner import (
        WalkForwardConfig,
        make_strategy_factory,
        run_walk_forward,
    )
    try:
        wf_cfg = WalkForwardConfig(**req.walk_forward.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    factory = make_strategy_factory(req.strategy, req.params)
    result = run_walk_forward(
        bars=bars,
        strategy_factory=factory,
        walk_forward_config=wf_cfg,
        backtest_config=bt_cfg,
        initial_cash=req.initial_cash,
        quantity=req.quantity,
    )

    payload = result.to_dict()
    payload["bars_processed"] = len(bars)
    return WalkForwardResponse(**payload)
