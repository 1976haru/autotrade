"""HTTP surface for LiveStrategyEngine.

Exposes a single in-process engine for now: configure once, feed bars one at
a time, query status, reset to start over. Enough to drive the engine from
the frontend or a script without re-implementing the strategy on the client.

submit=True is intentionally not wired in this PR — routing engine ticks
through RiskManager + PermissionGate + OrderExecutor lands separately so
the wiring decisions can be reviewed on their own.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backtest.types import Bar
from app.brokers.base import OrderRequest
from app.strategies.concrete import build_strategy
from app.strategies.live_engine import LiveStrategyEngine

router = APIRouter(prefix="/strategies", tags=["live-engine"])


class BarIn(BaseModel):
    symbol:    str
    timestamp: datetime
    open:      int
    high:      int
    low:       int
    close:     int
    volume:    int


class ConfigureRequest(BaseModel):
    strategy: str
    params:   dict = Field(default_factory=dict)
    quantity: int = 1


class TickRequest(BaseModel):
    bar:    BarIn
    submit: bool = False


class StatusResponse(BaseModel):
    configured: bool
    strategy:   str | None = None
    quantity:   int | None = None
    bars_seen:  int = 0
    holding:    bool = False


class TickResponse(BaseModel):
    signal:         str
    intended_order: OrderRequest | None = None
    bars_seen:      int
    holding:        bool


class _EngineState:
    engine:        LiveStrategyEngine | None = None
    strategy_name: str | None = None


def _reset_state() -> None:
    _EngineState.engine = None
    _EngineState.strategy_name = None


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    eng = _EngineState.engine
    if eng is None:
        return StatusResponse(configured=False)
    return StatusResponse(
        configured=True,
        strategy=_EngineState.strategy_name,
        quantity=eng.quantity,
        bars_seen=eng.bars_seen,
        holding=eng.holding,
    )


@router.post("/configure", response_model=StatusResponse)
def configure(req: ConfigureRequest) -> StatusResponse:
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")
    try:
        strategy = build_strategy(req.strategy, req.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _EngineState.engine = LiveStrategyEngine(strategy, quantity=req.quantity)
    _EngineState.strategy_name = req.strategy
    return get_status()


@router.post("/reset", response_model=StatusResponse)
def reset_route() -> StatusResponse:
    _reset_state()
    return get_status()


@router.post("/tick", response_model=TickResponse)
async def tick_route(req: TickRequest) -> TickResponse:
    if _EngineState.engine is None:
        raise HTTPException(
            status_code=400,
            detail="engine not configured; POST /api/strategies/configure first",
        )
    if req.submit:
        # The engine has a submit_tick() that routes through Risk/Permission/Executor,
        # but the HTTP wiring (broker/risk/db dependencies) lands in a follow-up PR.
        raise HTTPException(
            status_code=501,
            detail="submit=true requires HTTP wiring through broker / risk / db; "
                   "follow-up PR — until then run_tick only",
        )
    bar = Bar(**req.bar.model_dump())
    result = _EngineState.engine.run_tick(bar)
    return TickResponse(
        signal=result.signal.value,
        intended_order=result.intended_order,
        bars_seen=_EngineState.engine.bars_seen,
        holding=_EngineState.engine.holding,
    )
