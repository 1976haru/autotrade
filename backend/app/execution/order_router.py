from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.executor import OrderExecutor
from app.permission.gate import PermissionGate
from app.risk.risk_manager import RiskDecision, RiskManager


@dataclass(frozen=True)
class OrderRoutingResult:
    """Outcome of routing one order through Risk → Permission/Executor.

    Always carries the audit row that was written to the session and committed.
    `approval` is set when the decision was NEEDS_APPROVAL; `result` is set
    when the decision was APPROVED and the order executed.
    """
    decision: RiskDecision
    reasons:  list[str]
    audit:    OrderAuditLog
    approval: PendingApproval | None = None
    result:   OrderResult | None     = None


async def route_order(
    *,
    order:           OrderRequest,
    requested_by_ai: bool,
    mode:            OperationMode,
    broker:          BrokerAdapter,
    risk:            RiskManager,
    db:              Session,
) -> OrderRoutingResult:
    """Run an order through the full guardrail chain.

    Steps (CLAUDE.md absolute principle 2 — every order goes through this):
    1. Read live broker state (price, balance, positions).
    2. RiskManager.evaluate_order — produces APPROVED / NEEDS_APPROVAL / REJECTED.
    3. Always write OrderAuditLog (the record exists even when rejected).
    4. REJECTED        → commit audit, return.
       NEEDS_APPROVAL  → enqueue via PermissionGate, return (no broker call).
       APPROVED        → OrderExecutor sends to broker and updates audit, commit.

    Caller decides the surface (HTTP status, log entry, etc.).
    """
    quote     = await broker.get_price(order.symbol)
    balance   = await broker.get_balance()
    positions = await broker.get_positions()

    decision = risk.evaluate_order(
        order=order,
        mode=mode,
        balance=balance,
        positions=positions,
        latest_price=quote.price,
        requested_by_ai=requested_by_ai,
    )

    audit = OrderAuditLog(
        mode=mode.value,
        requested_by_ai=requested_by_ai,
        symbol=order.symbol,
        side=order.side.value,
        quantity=order.quantity,
        order_type=order.order_type.value,
        limit_price=order.limit_price,
        latest_price=quote.price,
        decision=decision.decision.value,
        reasons=list(decision.reasons),
        # 134: 호출자가 명시한 진입/청산 사유. 미명시(None)는 그대로 NULL — 운영자가
        # '미명시 주문'을 audit에서 식별 가능.
        trade_reason=order.trade_reason,
    )
    db.add(audit)

    if decision.decision == RiskDecision.REJECTED:
        db.commit()
        return OrderRoutingResult(
            decision=decision.decision,
            reasons=list(decision.reasons),
            audit=audit,
        )

    if decision.decision == RiskDecision.NEEDS_APPROVAL:
        approval = PermissionGate(db).submit(audit=audit, order=order, mode=mode)
        return OrderRoutingResult(
            decision=decision.decision,
            reasons=list(decision.reasons),
            audit=audit,
            approval=approval,
        )

    # APPROVED → execute
    result = await OrderExecutor(broker, db).execute(order, audit)
    db.commit()
    return OrderRoutingResult(
        decision=decision.decision,
        reasons=list(decision.reasons),
        audit=audit,
        result=result,
    )
