"""Position close auto-route (172, MUST).

VirtualPositionEngine의 `evaluate_close`가 should_close=True를 반환하면
호출자가 SELL OrderRequest를 만들어 RiskManager / PermissionGate / Audit
체인에 통과시켜야 한다. 본 모듈은 그 변환 + 라우팅을 한 함수에 묶음.

원칙:
- 본 함수가 broker.place_order를 직접 호출하지 않는다 — 반드시 route_order
  경유 (CLAUDE.md 절대 원칙 2).
- close 사유(stop_loss / take_profit / time_exit)는 OrderRequest.trade_reason
  으로 carry — audit row에 영구화.
- 청산은 정의상 운영자 위임 결정 — requested_by_ai=False (158/159 AI 가드
  우회 위험 없음).

호출 패턴:
    open_positions = compute_open_positions(db, last_prices=...)
    for pos in open_positions:
        ev = evaluate_close(pos, stop_loss_pct=..., take_profit_pct=...)
        if ev.should_close:
            await auto_close_position(
                pos, ev, mode=mode, broker=broker, risk=risk, db=db,
            )
"""

from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide, OrderType
from app.core.modes import OperationMode
from app.execution.order_router import OrderRoutingResult, route_order
from app.risk.risk_manager import RiskManager
from app.virtual.position_engine import CloseEvaluation, PositionSummary
from sqlalchemy.orm import Session


# evaluate_close가 반환하는 reason 코드 — OrderRequest.trade_reason으로 carry.
_REASON_TO_TRADE_REASON: dict[str, str] = {
    "stop_loss":   "stop_loss",
    "take_profit": "take_profit",
    "time_exit":   "time_exit",
    "unknown":     "auto_close",
}


async def auto_close_position(
    pos:         PositionSummary,
    evaluation:  CloseEvaluation,
    *,
    mode:        OperationMode,
    broker:      BrokerAdapter,
    risk:        RiskManager,
    db:          Session,
    client_order_id: str | None = None,
) -> OrderRoutingResult:
    """should_close=True인 PositionSummary를 SELL 주문으로 변환 후 route_order.

    should_close=False면 ValueError — 호출자 실수 방지.
    """
    if not evaluation.should_close:
        raise ValueError("evaluation.should_close must be True to auto-close")

    trade_reason = _REASON_TO_TRADE_REASON.get(evaluation.reason, "auto_close")

    order = OrderRequest(
        symbol=pos.symbol,
        side=OrderSide.SELL,
        quantity=pos.quantity,
        order_type=OrderType.MARKET,
        trade_reason=trade_reason,
        strategy=pos.strategy,
        client_order_id=client_order_id,
    )
    return await route_order(
        order=order,
        requested_by_ai=False,  # 청산은 시스템 결정 — AI 가드 무관.
        mode=mode,
        broker=broker,
        risk=risk,
        db=db,
    )
