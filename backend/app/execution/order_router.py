from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.rate_limit import check_global_rate_limit, check_rate_limit
from app.risk.auto_stop import maybe_trigger_auto_stop
from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.executor import OrderExecutor
from app.execution.order_executor import derive_order_source
from app.permission.gate import PermissionGate
from app.risk.daily_pnl import (
    compute_today_realized_pnl,
    compute_weekly_realized_pnl_kst,
    count_consecutive_losing_trades,
    count_orders_today_kst,
)
from app.risk.order_guard import (
    GuardDecision,
    OrderGuard,
    OrderGuardConfig,
)
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

    # #38: OrderGuard pre-trade 검사 — duplicate fingerprint / cooldown /
    # pending. 모든 cooldown / window가 0(default)이면 본 가드는 사실상 no-op
    # (기존 호환). 하나라도 활성이면 RiskManager 평가 *전*에 차단해 broker
    # 호출 비용을 들이지 않는다.
    guard_cfg = OrderGuardConfig(
        duplicate_window_seconds=risk.policy.order_guard_duplicate_window_seconds,
        symbol_cooldown_seconds=risk.policy.order_guard_symbol_cooldown_seconds,
        strategy_symbol_cooldown_seconds=risk.policy.order_guard_strategy_symbol_cooldown_seconds,
        post_exit_cooldown_seconds=risk.policy.order_guard_post_exit_cooldown_seconds,
        ai_extra_cooldown_seconds=risk.policy.order_guard_ai_extra_cooldown_seconds,
        block_when_pending_same_side=risk.policy.order_guard_block_when_pending_same_side,
        price_bucket_pct=risk.policy.order_guard_price_bucket_pct,
    )
    guard_result = OrderGuard(guard_cfg, db).check(
        order, mode=mode.value, requested_by_ai=requested_by_ai,
    )
    # ALLOW 외에는 audit row를 작성한 뒤 즉시 분기. RETRY_REPLAY는 idempotency
    # 의미 — 호출자가 기존 audit_id를 carry할 수 있도록 result에 명시하지만,
    # 본 PR에서는 client_order_id 기반 검사가 위에서 먼저 raise하므로 사실상
    # 도달하지 않는 분기 (방어용).
    if guard_result.decision != GuardDecision.ALLOW:
        guard_audit = OrderAuditLog(
            mode=mode.value,
            requested_by_ai=requested_by_ai,
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            order_type=order.order_type.value,
            limit_price=order.limit_price,
            latest_price=0,                # broker quote 호출 회피
            decision=RiskDecision.REJECTED.value,
            reasons=list(guard_result.reasons),
            trade_reason=order.trade_reason,
            strategy=order.strategy,
            signal_strength=order.signal_strength,
            signal_confidence=order.signal_confidence,
            client_order_id=order.client_order_id,
            ai_decision_meta=order.ai_decision_meta,
            # #40: 주문 source 분류
            source=derive_order_source(order, requested_by_ai=requested_by_ai).value,
        )
        db.add(guard_audit)
        db.commit()
        return OrderRoutingResult(
            decision=RiskDecision.REJECTED,
            reasons=list(guard_result.reasons),
            audit=guard_audit,
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

    # 177: 시스템 전체 rate limit — 모든 주문 종류 통합. 161과 별개.
    global_rate_violation_count: int | None = None
    if risk.policy.global_rate_limit_max_count > 0:
        within, current_count = check_global_rate_limit(
            db,
            window_seconds=risk.policy.global_rate_limit_window_seconds,
            max_count=risk.policy.global_rate_limit_max_count,
        )
        if not within:
            global_rate_violation_count = current_count

    # 183: 일일(KST date) 최대 주문 횟수. decision 무관, 모든 audit row 카운트.
    daily_order_violation_count: int | None = None
    if risk.policy.max_orders_per_day > 0:
        today_count = count_orders_today_kst(db)
        if today_count >= risk.policy.max_orders_per_day:
            daily_order_violation_count = today_count

    quote     = await broker.get_price(order.symbol)
    balance   = await broker.get_balance()
    positions = await broker.get_positions()

    # 145: max_daily_loss 강제력 회복. 매 주문 평가 직전에 audit log 기반으로
    # 오늘의 realized PnL을 재계산해 RiskManager 카운터를 채운다 — 이 라인이
    # 없으면 daily_realized_pnl이 0에 머물러 max_daily_loss 검사가 무효.
    risk.daily_realized_pnl = compute_today_realized_pnl(db)

    # #36: 주간 PnL + 연속 손실 카운트도 매 평가 직전 재계산. 임계 0인 경우
    # rule이 비활성이라 사실상 no-op이지만, 한도 활성 시 신선한 값이 필요.
    weekly_pnl = (
        compute_weekly_realized_pnl_kst(db)
        if risk.policy.weekly_loss_limit > 0 else 0
    )
    consecutive_loss_count = (
        count_consecutive_losing_trades(db)
        if risk.policy.consecutive_loss_limit > 0 else 0
    )

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
        weekly_realized_pnl=weekly_pnl,
        consecutive_loss_count=consecutive_loss_count,
    )

    # #36: BLOCK_NEW_BUY 조건이 reason으로 누적된 경우 RiskCheckResult를
    # REJECTED로 명시적으로 덮어쓴다 (BUY only). evaluate_order의 마지막 줄이
    # `if result.reasons: result.decision = REJECTED` 이라 이미 처리되지만,
    # SELL 통과 정책을 분명히 하기 위해 본 분기를 유지.
    # (현재 evaluate_order의 fallthrough가 reasons 누적되면 자동 REJECTED로
    # 변환하므로 별도 처리 불필요 — 본 주석은 향후 옵트인 변경 시 hint.)

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

    # 177: 시스템 전체 rate limit 위반.
    if global_rate_violation_count is not None:
        decision.reasons.append(
            f"global rate limit exceeded: {global_rate_violation_count} orders "
            f"in {risk.policy.global_rate_limit_window_seconds}s window "
            f"(max {risk.policy.global_rate_limit_max_count})"
        )
        decision.decision = RiskDecision.REJECTED

    # 183: 일일 주문 횟수 한도 위반.
    if daily_order_violation_count is not None:
        decision.reasons.append(
            f"max_orders_per_day exceeded: {daily_order_violation_count} orders "
            f"today (max {risk.policy.max_orders_per_day})"
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
        # #40: 주문 source 분류 (STRATEGY / AI / MANUAL / OPERATOR_OVERRIDE / UNKNOWN).
        source=derive_order_source(order, requested_by_ai=requested_by_ai).value,
        # 152: AI decision metadata도 같은 row에 carry.
        ai_decision_meta=order.ai_decision_meta,
    )
    db.add(audit)

    if decision.decision == RiskDecision.REJECTED:
        db.commit()
        # 182: 연속 REJECTED 누적 임계 도달 시 자동 emergency_stop. 이미 켜져
        # 있거나 임계 0이면 no-op.
        maybe_trigger_auto_stop(
            db, risk=risk,
            threshold=risk.policy.auto_stop_consecutive_rejections,
        )
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
