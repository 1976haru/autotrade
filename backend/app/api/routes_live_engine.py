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

from app.api.deps import get_broker, get_market_data, get_risk_manager
from app.backtest.types import Bar
from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.core.config import get_settings
from app.db.session import get_db
from app.execution.order_router import OrderRoutingResult, route_order
from app.market.base import Interval, MarketDataAdapter
from app.market.cache import BarCache
from app.risk.risk_manager import RiskDecision, RiskManager
from app.strategies.concrete import build_strategy, describe_all_strategies
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


class ReplayRequest(BaseModel):
    symbol:   str
    start:    datetime
    end:      datetime
    interval: Interval = Interval.DAY_1


class ReplayResponse(BaseModel):
    bars_processed:  int
    signals_emitted: dict[str, int]
    last_signal:     str | None  = None
    last_intended:   OrderRequest | None = None
    bars_seen:       int
    holding:         bool
    entry_price:        int | None = None
    last_price:         int | None = None
    unrealized_pnl:     int | None = None
    unrealized_pnl_pct: float | None = None


class StatusResponse(BaseModel):
    configured: bool
    strategy:   str | None = None
    quantity:   int | None = None
    bars_seen:  int = 0
    holding:    bool = False
    # Position tracking — None when flat or no marks yet.
    entry_price:        int | None = None
    last_price:         int | None = None
    unrealized_pnl:     int | None = None
    unrealized_pnl_pct: float | None = None
    # 135: market regime advisory.
    current_regime:           str  = "any"
    regime_matches_strategy:  bool = True


class StrategyParamSchema(BaseModel):
    name:     str
    type:     str
    default:  int | float | str | bool | None = None
    required: bool


class StrategyDescription(BaseModel):
    name:        str
    class_name:  str
    description: str
    params:      list[StrategyParamSchema]
    # 131: contract metadata — 운영자/감사 가독.
    # 미명시 시 base.py default("", "any", {})가 surface된다 → "이 전략은
    # 미완성"으로 운영자가 인지.
    entry:           str  = ""
    exit:            str  = ""
    invalidation:    str  = ""
    required_regime: str  = "any"
    risk_profile:    dict = {}


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
    entry_price:        int | None = None
    last_price:         int | None = None
    unrealized_pnl:     int | None = None
    unrealized_pnl_pct: float | None = None
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


@router.get("/registry", response_model=list[StrategyDescription])
def get_registry() -> list[StrategyDescription]:
    """List all registered strategies + their constructor param schema.

    Frontend uses this to render config forms without hardcoding param names.
    Pure introspection — no engine state, no broker calls.
    """
    return [StrategyDescription(**d) for d in describe_all_strategies()]


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
        entry_price=eng.entry_price,
        last_price=eng.last_price,
        unrealized_pnl=eng.unrealized_pnl,
        unrealized_pnl_pct=eng.unrealized_pnl_pct,
        current_regime=eng.current_regime,
        regime_matches_strategy=eng.regime_matches_strategy,
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
        entry_price=engine.entry_price,
        last_price=engine.last_price,
        unrealized_pnl=engine.unrealized_pnl,
        unrealized_pnl_pct=engine.unrealized_pnl_pct,
        routing=routing_out,
    )


@router.post("/replay", response_model=ReplayResponse)
async def replay_route(
    req:      ReplayRequest,
    upstream: MarketDataAdapter = Depends(get_market_data),
    db:       Session           = Depends(get_db),
) -> ReplayResponse:
    """Feed a range of bars from the market adapter into the configured engine.

    Useful for warming up the engine state from history before manual ticks
    take over. submit is intentionally not exposed — replaying with order
    submission would mass-fire orders and is risky enough to deserve its own
    PR with explicit safeguards.
    """
    engine = _EngineState.engine
    if engine is None:
        raise HTTPException(
            status_code=400,
            detail="engine not configured; POST /api/strategies/configure first",
        )
    if req.start > req.end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    cache = BarCache(db)
    bars = cache.get(req.symbol, req.interval.value, req.start, req.end)
    if not bars:
        try:
            bars = await upstream.get_bars(req.symbol, req.start, req.end, req.interval)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        cache.save(bars, req.interval.value)

    if not bars:
        raise HTTPException(status_code=400, detail="no bars returned for the requested range")

    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    last_result = None
    for bar in bars:
        last_result = engine.run_tick(bar)
        counts[last_result.signal.value] += 1

    return ReplayResponse(
        bars_processed=len(bars),
        signals_emitted=counts,
        last_signal=last_result.signal.value if last_result else None,
        entry_price=engine.entry_price,
        last_price=engine.last_price,
        unrealized_pnl=engine.unrealized_pnl,
        unrealized_pnl_pct=engine.unrealized_pnl_pct,
        last_intended=last_result.intended_order if last_result else None,
        bars_seen=engine.bars_seen,
        holding=engine.holding,
    )
