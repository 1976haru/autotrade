"""HTTP surface for LiveStrategyEngine.

Exposes a single in-process engine for now: configure once, feed bars one at
a time, query status, reset to start over. tick(submit=True) routes the
intended order through RiskManager + PermissionGate + OrderExecutor; the
engine's logical position state is rolled back when the gate rejects.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.backtest.types import Bar
from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.core.config import get_settings
from app.db.session import get_db
from app.execution.order_router import OrderRoutingResult, route_order
from app.risk.risk_manager import RiskDecision, RiskManager
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


class RoutingOut(BaseModel):
    decision:     str
    reasons:      list[str]
    approval_id:  int | None = None
    order_result: OrderResult | None = None


class TickResponse(BaseModel):
    signal:         str
    intended_order: OrderRequest | None = None
    bars_seen:      int
    holding:        bool
    routing:        RoutingOut | None = None


class _EngineState:
    engine:        LiveStrategyEngine | None = None
    strategy_name: str | None = None


def _reset_state() -> None:
    _EngineState.engine = None
    _EngineState.strategy_name = None


def _to_routing_out(r: OrderRoutingResult) -> RoutingOut:
    return RoutingOut(
        decision=r.decision.value,
        reasons=list(r.reasons),
        approval_id=r.approval.id if r.approval is not None else None,
        order_result=r.result,
    )


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
async def tick_route(
    req:    TickRequest,
    broker: BrokerAdapter = Depends(get_broker),
    risk:   RiskManager   = Depends(get_risk_manager),
    db:     Session       = Depends(get_db),
) -> TickResponse:
    engine = _EngineState.engine
    if engine is None:
        raise HTTPException(
            status_code=400,
            detail="engine not configured; POST /api/strategies/configure first",
        )

    bar = Bar(**req.bar.model_dump())
    result = engine.run_tick(bar)

    routing_out: RoutingOut | None = None
    if req.submit and result.intended_order is not None:
        routing = await route_order(
            order=result.intended_order,
            requested_by_ai=False,
            mode=get_settings().default_mode,
            broker=broker,
            risk=risk,
            db=db,
        )
        # Mirror engine.submit_tick: roll back position state when rejected.
        if routing.decision == RiskDecision.REJECTED:
            engine.rollback_intent(result.intended_order)
        routing_out = _to_routing_out(routing)

    return TickResponse(
        signal=result.signal.value,
        intended_order=result.intended_order,
        bars_seen=engine.bars_seen,
        holding=engine.holding,
        routing=routing_out,
    )
