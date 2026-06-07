import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import Balance, BrokerAdapter, OrderRequest, Position, Quote
from app.core.config import get_settings
from app.db.session import get_db
from app.execution.order_router import DuplicateOrderError, route_order
from app.risk.risk_manager import RiskDecision, RiskManager

router = APIRouter(prefix="/broker", tags=["broker"])
_log = logging.getLogger("autotrade.broker")

# T2: 마지막 정상 잔고 — 레이트리밋/일시 실패 시 stale 로 폴백(가짜 0 금지, 기준시각 명시).
_last_good_balance: dict | None = None


def _balance_dict(bal) -> dict:
    if hasattr(bal, "model_dump"):
        return dict(bal.model_dump())
    if hasattr(bal, "__dict__"):
        return {k: v for k, v in vars(bal).items() if not k.startswith("_")}
    return dict(bal)


def _now_hm_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M")


@router.get("/price/{symbol}")
async def get_price(symbol: str, broker: BrokerAdapter = Depends(get_broker)) -> Quote:
    return await broker.get_price(symbol)


@router.get("/balance")
async def get_balance(broker: BrokerAdapter = Depends(get_broker)):
    """잔고 — 성공 시 broker_healthy=true. 레이트리밋/일시 실패 시 마지막 정상값을
    stale=true + as_of_kst 로 반환(없으면 503 broker_healthy=false)."""
    global _last_good_balance
    try:
        bal = await broker.get_balance()
        out = _balance_dict(bal)
        out.update({"stale": False, "as_of_kst": _now_hm_kst(), "broker_healthy": True})
        _last_good_balance = dict(out)
        return out
    except Exception as exc:  # noqa: BLE001 — KIS 일시 실패는 stale 폴백(앱 전면 실패 금지).
        _log.warning("[broker] balance 조회 실패: %s", exc)
        if _last_good_balance is not None:
            stale = dict(_last_good_balance)
            stale["stale"] = True
            stale["broker_healthy"] = False
            return stale  # 옛 정상값(as_of_kst 그대로) — 가짜 0 아님
        return JSONResponse(
            status_code=503,
            content={"detail": "증권사(KIS) 응답이 없어요", "broker_healthy": False, "kis_error": True},
        )


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
    try:
        routing = await route_order(
            order=order,
            requested_by_ai=False,
            mode=get_settings().default_mode,
            broker=broker,
            risk=risk,
            db=db,
        )
    except DuplicateOrderError as e:
        # 140: 같은 client_order_id로 이미 처리된 주문 — 409 Conflict.
        raise HTTPException(status_code=409, detail=str(e))

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
