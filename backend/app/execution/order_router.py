from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.rate_limit import check_rate_limit
from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.executor import OrderExecutor
from app.permission.gate import PermissionGate
from app.risk.daily_pnl import compute_today_realized_pnl
from app.risk.risk_manager import RiskDecision, RiskManager


class DuplicateOrderError(Exception):
    """140: 같은 client_order_id로 이미 audit row가 있는데 다시 들어온 주문.
    onClick double-fire 같은 사고에서 두 번 체결되는 사고를 차단한다. caller는
    이 예외를 catch해 409 Conflict로 surface하거나 idempotently 무시한다."""


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
    0. (140) idempotency: 호출자가 client_order_id를 보냈고 같은 id의 audit
       row가 이미 있으면 DuplicateOrderError raise — broker 호출 없이 즉시
       거부. NULL id 주문은 검사 X.
    1. Read live broker state (price, balance, positions).
    2. RiskManager.evaluate_order — produces APPROVED / NEEDS_APPROVAL / REJECTED.
    3. Always write OrderAuditLog (the record exists even when rejected).
    4. REJECTED        → commit audit, return.
       NEEDS_APPROVAL  → enqueue via PermissionGate, return (no broker call).
       APPROVED        → OrderExecutor sends to broker and updates audit, commit.

    Caller decides the surface (HTTP status, log entry, etc.).
    """
    # 140: idempotency 첫 검사 — broker/risk 호출 비용을 들이기 전에 차단.
    if order.client_order_id:
        existing = db.execute(
            select(OrderAuditLog.id).where(
                OrderAuditLog.client_order_id == order.client_order_id
            )
        ).first()
        if existing is not None:
            raise DuplicateOrderError(
                f"client_order_id={order.client_order_id} already processed"
            )

    # 161: AI rate limit — flooding 방어. broker/risk 호출 비용 들이기 전에 차단.
    # max_count <= 0이면 검사 비활성. AI 외 경로 주문은 검사 우회.
    ai_rate_violation_count: int | None = None
    if requested_by_ai and risk.policy.ai_rate_limit_max_count > 0:
        within, current_count = check_rate_limit(
            db,
            strategy=order.strategy,
            symbol=order.symbol,
            window_seconds=risk.policy.ai_rate_limit_window_seconds,
            max_count=risk.policy.ai_rate_limit_max_count,
        )
        if not within:
            ai_rate_violation_count = current_count

    quote     = await broker.get_price(order.symbol)
    balance   = await broker.get_balance()
    positions = await broker.get_positions()

    # 145: max_daily_loss 강제력 회복. 매 주문 평가 직전에 audit log 기반으로
    # 오늘의 realized PnL을 재계산해 RiskManager 카운터를 채운다 — 이 라인이
    # 없으면 daily_realized_pnl이 0에 머물러 max_daily_loss 검사가 무효.
    risk.daily_realized_pnl = compute_today_realized_pnl(db)

    # 143: Quote.timestamp는 ISO 문자열. RiskManager가 stale 검사를 수행하려면
    # datetime이 필요하므로 여기서 파싱한다. 파싱 실패는 broker 계약 위반이지만
    # 안전 측 — None으로 두면 RiskManager가 검사를 건너뛴다 (기존 동작 유지).
    quote_ts: datetime | None = None
    try:
        quote_ts = datetime.fromisoformat(quote.timestamp)
    except (TypeError, ValueError):
        quote_ts = None

    decision = risk.evaluate_order(
        order=order,
        mode=mode,
        balance=balance,
        positions=positions,
        latest_price=quote.price,
        requested_by_ai=requested_by_ai,
        latest_price_timestamp=quote_ts,
    )

    # 161: rate limit 위반은 RiskManager 결과를 REJECTED로 덮어쓴다 — 다른 가드
    # 통과 여부와 무관하게 차단. reason은 누적 (운영자가 다른 위반도 같이 본다).
    if ai_rate_violation_count is not None:
        decision.reasons.append(
            f"AI rate limit exceeded: {ai_rate_violation_count} proposals "
            f"in {risk.policy.ai_rate_limit_window_seconds}s window "
            f"(max {risk.policy.ai_rate_limit_max_count}) for "
            f"({order.strategy}, {order.symbol})"
        )
        decision.decision = RiskDecision.REJECTED

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
        # 138: 주문을 만든 전략. LiveEngine이 자동 채우고, 수동 주문은 NULL —
        # 두 경우 모두 audit에서 사후 식별 가능.
        strategy=order.strategy,
        # 139: 신호 quality (136) 영구화. 산출되지 않은 경로는 NULL.
        signal_strength=order.signal_strength,
        signal_confidence=order.signal_confidence,
        # 140: idempotency 키. 호출자가 보낸 그대로 audit row에 영구화.
        client_order_id=order.client_order_id,
        # 152: AI decision metadata도 같은 row에 carry.
        ai_decision_meta=order.ai_decision_meta,
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
