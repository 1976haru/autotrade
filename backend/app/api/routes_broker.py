from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import Balance, BrokerAdapter, OrderRequest, Position, Quote
from app.core.config import get_settings
from app.db.session import get_db
from app.execution.order_router import route_order
from app.risk.risk_manager import RiskDecision, RiskManager

router = APIRouter(prefix="/broker/mock", tags=["mock-broker"])


@router.get("/price/{symbol}")
async def get_price(symbol: str, broker: BrokerAdapter = Depends(get_broker)) -> Quote:
    return await broker.get_price(symbol)


@router.get("/balance")
async def get_balance(broker: BrokerAdapter = Depends(get_broker)) -> Balance:
    return await broker.get_balance()


@router.get("/positions")
async def get_positions(broker: BrokerAdapter = Depends(get_broker)) -> list[Position]:
    return await broker.get_positions()


@router.post("/orders")
async def place_order(
    order: OrderRequest,
    broker: BrokerAdapter = Depends(get_broker),
    risk: RiskManager = Depends(get_risk_manager),
    db: Session = Depends(get_db),
):
    routing = await route_order(
        order=order,
        requested_by_ai=False,
        mode=get_settings().default_mode,
        broker=broker,
        risk=risk,
        db=db,
    )

    if routing.decision == RiskDecision.REJECTED:
        raise HTTPException(
            status_code=400,
            detail={"decision": routing.decision, "reasons": routing.reasons},
        )

    if routing.decision == RiskDecision.NEEDS_APPROVAL:
        return JSONResponse(
            status_code=202,
            content={
                "status":      "PENDING_APPROVAL",
                "approval_id": routing.approval.id,
                "reasons":     routing.reasons,
            },
        )

    return routing.result
