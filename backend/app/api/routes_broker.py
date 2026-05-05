from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_mock_broker, get_risk_manager
from app.brokers.base import Balance, OrderRequest, Position, Quote
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.config import get_settings
from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.execution.executor import OrderExecutor
from app.permission.gate import PermissionGate
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
    db: Session = Depends(get_db),
):
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
    audit = OrderAuditLog(
        mode=settings.default_mode.value,
        symbol=order.symbol,
        side=order.side.value,
        quantity=order.quantity,
        order_type=order.order_type.value,
        limit_price=order.limit_price,
        latest_price=quote.price,
        decision=decision.decision.value,
        reasons=list(decision.reasons),
    )
    db.add(audit)

    if decision.decision == RiskDecision.REJECTED:
        db.commit()
        raise HTTPException(
            status_code=400,
            detail={"decision": decision.decision, "reasons": decision.reasons},
        )

    if decision.decision == RiskDecision.NEEDS_APPROVAL:
        approval = PermissionGate(db).submit(audit=audit, order=order, mode=settings.default_mode)
        return JSONResponse(
            status_code=202,
            content={
                "status":      "PENDING_APPROVAL",
                "approval_id": approval.id,
                "reasons":     list(decision.reasons),
            },
        )

    result = await OrderExecutor(broker, db).execute(order, audit)
    db.commit()
    return result
