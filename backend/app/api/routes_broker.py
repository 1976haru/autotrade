from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_mock_broker, get_risk_manager
from app.brokers.base import Balance, OrderRequest, OrderResult, Position, Quote
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.config import get_settings
from app.risk.risk_manager import RiskDecision, RiskManager

router = APIRouter(prefix="/broker/mock", tags=["mock-broker"])


@router.get("/price/{symbol}")
async def get_price(symbol: str, broker: MockBrokerAdapter = Depends(get_mock_broker)) -> Quote:
    return await broker.get_price(symbol)


@router.get("/balance")
async def get_balance(broker: MockBrokerAdapter = Depends(get_mock_broker)) -> Balance:
    return await broker.get_balance()


@router.get("/positions")
async def get_positions(broker: MockBrokerAdapter = Depends(get_mock_broker)) -> list[Position]:
    return await broker.get_positions()


@router.post("/orders")
async def place_order(
    order: OrderRequest,
    broker: MockBrokerAdapter = Depends(get_mock_broker),
    risk: RiskManager = Depends(get_risk_manager),
) -> OrderResult:
    settings = get_settings()
    quote = await broker.get_price(order.symbol)
    balance = await broker.get_balance()
    positions = await broker.get_positions()
    decision = risk.evaluate_order(
        order=order,
        mode=settings.default_mode,
        balance=balance,
        positions=positions,
        latest_price=quote.price,
    )
    if decision.decision != RiskDecision.APPROVED:
        raise HTTPException(status_code=400, detail={"decision": decision.decision, "reasons": decision.reasons})
    return await broker.place_order(order)
