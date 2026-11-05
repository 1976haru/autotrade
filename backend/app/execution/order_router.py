from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.rate_limit import check_global_rate_limit, check_rate_limit
from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult, OrderSide
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval, ShadowTrade
from app.execution.executor import OrderExecutor
from app.execution.order_executor import derive_order_source
from app.permission.gate import PermissionGate
from app.risk.auto_stop import maybe_trigger_auto_stop
from app.risk.daily_pnl import (
    compute_today_realized_pnl,
    compute_weekly_realized_pnl_kst,
    count_orders_today_kst,
    get_consecutive_loss_state,
)
from app.risk.order_guard import (
    GuardDecision,
    OrderGuard,
    OrderGuardConfig,
)
from app.risk.risk_manager import (
    SHADOW_GATE_REASONS,
    RiskDecision,
    RiskManager,
)

_KST = timezone(timedelta(hours=9))


def _paper_daily_buy_limit_reason(
    db: Session, *, account_scope: str, order: OrderRequest,
    current_price: int, daily_limit_krw: int, now: datetime | None = None,
    cost_reserve_bps: int = 100,
) -> str | None:
    """Final account-scoped daily BUY notional gate for every KIS PAPER caller.

    Accepted/pending orders reserve their full notional; filled orders count
    actual fills; canceled orders retain only their filled amount. Unknown
    prices/status fail closed so a missing audit value cannot free budget.
    """
    if order.side != OrderSide.BUY:
        return None
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    today = moment.astimezone(_KST).date()
    rows = db.execute(
        select(OrderAuditLog).where(
            OrderAuditLog.account_id == account_scope,
            OrderAuditLog.mode == OperationMode.PAPER.value,
            OrderAuditLog.side == OrderSide.BUY.value,
        )
    ).scalars().all()
    committed = 0
    for row in rows:
        created = row.created_at
        if created is None:
            return "PAPER_DAILY_BUY_STATE_UNKNOWN: order audit timestamp missing"
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created.astimezone(_KST).date() != today:
            continue
        status = str(row.broker_status or "").upper()
        accepted = (
            bool(row.executed) or bool(row.broker_order_id)
            or row.decision in {RiskDecision.APPROVED.value, RiskDecision.NEEDS_APPROVAL.value}
        )
        if not accepted or row.decision == RiskDecision.REJECTED.value:
            continue
        if status in {"CANCELLED", "CANCELED", "REJECTED", "REJECT"}:
            quantity = max(0, int(row.filled_quantity or 0))
        else:
            quantity = max(0, int(row.quantity or 0))
        if quantity == 0:
            continue
        prices = [int(value) for value in (row.limit_price, row.latest_price, row.avg_fill_price)
                  if value is not None and int(value) > 0]
        if not prices:
            return "PAPER_DAILY_BUY_STATE_UNKNOWN: accepted order price missing"
        committed += quantity * max(prices) * (10_000 + cost_reserve_bps) // 10_000

    order_price = int(order.limit_price or current_price or 0)
    if order_price <= 0:
        return "PAPER_DAILY_BUY_STATE_UNKNOWN: current order price missing"
    requested = int(order.quantity) * order_price * (10_000 + cost_reserve_bps) // 10_000
    if committed + requested > int(daily_limit_krw):
        return (
            f"PAPER_DAILY_BUY_LIMIT_EXCEEDED: committed {committed} + requested "
            f"{requested} > daily limit {int(daily_limit_krw)} KRW"
        )
    return None


# #43: LIVE_SHADOW shadow trade 추정 체결가 산출 메서드 식별자. 본 PR은
# latest_price를 그대로 추정 체결가로 쓰는 가장 단순한 proxy. 향후 orderbook
# depth / 슬리피지 모델 추가 시 method 문자열을 새로 부여하고 분기.
_SHADOW_ESTIMATION_METHOD = "latest_price_proxy"
_SHADOW_CONFIDENCE_NOTE = (
    "추정치 — 실제 체결과 다를 수 있습니다 "
    "(orderbook depth / 부분체결 / 호가 공백 / 슬리피지 미반영)."
)
def _consecutive_loss_mode_enabled(risk: RiskManager, mode: OperationMode) -> bool:
    enabled_modes = risk.policy.consecutive_loss_enabled_modes
    return not enabled_modes or mode.value in enabled_modes


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
    now:             datetime | None = None,
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
    from app.kis_paper.account_scope import account_scope_for_broker
    query_scope = account_scope_for_broker(broker)
    if order.client_order_id:
        query = select(OrderAuditLog.id).where(OrderAuditLog.client_order_id == order.client_order_id)
        if query_scope is not None:
            query = query.where(OrderAuditLog.account_id == query_scope)
        existing = db.execute(query).first()
        if existing is not None:
            raise DuplicateOrderError(
                f"client_order_id={order.client_order_id} already processed"
            )

    from app.brokers.kis import KisBrokerAdapter
    from app.kis_paper.account_scope import ACCOUNT_SCOPE_UNKNOWN, account_scope_for_broker
    account_scope = account_scope_for_broker(broker)
    if isinstance(broker, KisBrokerAdapter) and account_scope is None:
        audit = OrderAuditLog(
            mode=mode.value, requested_by_ai=requested_by_ai, symbol=order.symbol,
            side=order.side.value, quantity=order.quantity, order_type=order.order_type.value,
            limit_price=order.limit_price, latest_price=0,
            decision=RiskDecision.REJECTED.value,
            reasons=[f"{ACCOUNT_SCOPE_UNKNOWN}: KIS account scope unavailable"],
            trade_reason=order.trade_reason, strategy=order.strategy,
            client_order_id=order.client_order_id,
            source=derive_order_source(order, requested_by_ai=requested_by_ai).value,
            account_id=account_scope,
        )
        db.add(audit)
        db.commit()
        return OrderRoutingResult(decision=RiskDecision.REJECTED,
                                  reasons=list(audit.reasons), audit=audit)

    # #38: OrderGuard pre-trade 검사 — duplicate fingerprint / cooldown /
    # pending. 모든 cooldown / window가 0(default)이면 본 가드는 사실상 no-op
    # (기존 호환). 하나라도 활성이면 RiskManager 평가 *전*에 차단해 broker
    # 호출 비용을 들이지 않는다.
    # INTRADAY-FLAT-BY-CLOSE-V1: 프로그램(kis_paper_auto) 신규 BUY 서버측 재검증.
    #   14:30 컷오프 / 휴장일 / 달력 미등록 / 직전일 EOD 미완료 → broker 호출 없이
    #   REJECTED audit. 스캔·run-once·재시작·지연 호출 *모든* 경로가 여기서 막힌다.
    #   V2: 수동매매 패널 BUY(manual_buy)도 KIS 브로커면 시간/달력 차단. SELL 은 대상 아님.
    if order.side == OrderSide.BUY:
        from app.brokers.kis import KisBrokerAdapter
        from app.kis_paper.intraday_buy_gate import (
            is_manual_buy,
            is_program_order,
            manual_buy_block_reason,
            program_buy_block_reason,
        )
        block_code: str | None = None
        block_msg = ""
        if is_program_order(order.trade_reason):
            # now: 호출자(driver/executor)의 서버 시계 — 미지정이면 wall clock.
            block_code, block_msg = program_buy_block_reason(
                db, now=now, symbol=order.symbol, quantity=order.quantity)
        elif is_manual_buy(order.trade_reason) and isinstance(broker, KisBrokerAdapter):
            # V2: 수동매매 패널 BUY 도 14:30 이후/휴장일 차단 (KIS 세션 대상).
            block_code, block_msg = manual_buy_block_reason(now=now)
        if block_code is not None:
            cutoff_reasons = [f"{block_code}: {block_msg}"]
            cutoff_audit = OrderAuditLog(
                mode=mode.value,
                requested_by_ai=requested_by_ai,
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                latest_price=0,                # broker quote 호출 회피
                decision=RiskDecision.REJECTED.value,
                reasons=cutoff_reasons,
                trade_reason=order.trade_reason,
                strategy=order.strategy,
                signal_strength=order.signal_strength,
                signal_confidence=order.signal_confidence,
                client_order_id=order.client_order_id,
                ai_decision_meta=order.ai_decision_meta,
                source=derive_order_source(order, requested_by_ai=requested_by_ai).value,
                account_id=account_scope,
            )
            db.add(cutoff_audit)
            db.commit()
            return OrderRoutingResult(
                decision=RiskDecision.REJECTED,
                reasons=cutoff_reasons,
                audit=cutoff_audit,
            )

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
            account_id=account_scope,
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

    # 183: 일일(KST date) 최대 주문 횟수. *신규 진입(BUY)에만* 적용 — 청산(SELL:
    #  손절/익절/청산)은 한도 소진으로 막히면 안 되므로 면제(auto_permission 게이트와
    #  동일 정책, 위험 감소 방향). decision 무관, 모든 audit row 카운트.
    daily_order_violation_count: int | None = None
    if risk.policy.max_orders_per_day > 0 and order.side == OrderSide.BUY:
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
    consecutive_loss_count = 0
    if (
        risk.policy.consecutive_loss_limit > 0
        and _consecutive_loss_mode_enabled(risk, mode)
    ):
        (
            consecutive_loss_count,
            _latest_losing_sell_audit_id,
            latest_losing_sell_at,
        ) = get_consecutive_loss_state(db)
        if (
            order.side == OrderSide.BUY
            and risk.policy.consecutive_loss_cooldown_minutes > 0
            and consecutive_loss_count >= risk.policy.consecutive_loss_limit
            and latest_losing_sell_at is not None
        ):
            elapsed = datetime.now(timezone.utc) - latest_losing_sell_at
            if elapsed >= timedelta(minutes=risk.policy.consecutive_loss_cooldown_minutes):
                consecutive_loss_count = 0

    # 143: Quote.timestamp는 ISO 문자열. RiskManager가 stale 검사를 수행하려면
    # datetime이 필요하므로 여기서 파싱한다. 파싱 실패는 broker 계약 위반이지만
    # 안전 측 — None으로 두면 RiskManager가 검사를 건너뛴다 (기존 동작 유지).
    quote_ts: datetime | None = None
    try:
        quote_ts = datetime.fromisoformat(quote.timestamp)
    except (TypeError, ValueError):
        quote_ts = None

    paper_position_limits = None
    paper_daily_buy_limit_violation: str | None = None
    if mode == OperationMode.PAPER:
        from app.brokers.kis import KisBrokerAdapter

        if isinstance(broker, KisBrokerAdapter) and bool(getattr(broker, "is_paper", False)):
            from app.core.config import get_settings
            from app.kis_paper.order_limits import resolve_kis_paper_order_limits
            from app.risk.position_limits import policy_from_risk_policy

            limits = resolve_kis_paper_order_limits(get_settings())
            paper_position_limits = replace(
                policy_from_risk_policy(risk.policy),
                max_order_notional=limits.max_order_krw,
                max_symbol_exposure=limits.per_symbol_krw,
                max_positions=limits.max_positions,
                max_total_exposure=limits.total_budget_krw,
            )
            # The driver-level daily budget is advisory for scan flow. Enforce
            # the same account-scoped cap here so manual/direct BUY callers
            # cannot bypass it at the final common order route.
            paper_daily_buy_limit_violation = _paper_daily_buy_limit_reason(
                db,
                account_scope=account_scope,
                order=order,
                current_price=int(quote.price or 0),
                daily_limit_krw=limits.daily_buy_krw,
                now=now,
                cost_reserve_bps=limits.cost_reserve_bps,
            )

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
        paper_position_limits=paper_position_limits,
    )
    if paper_daily_buy_limit_violation is not None:
        decision.reasons.append(paper_daily_buy_limit_violation)
        decision.decision = RiskDecision.REJECTED
    # P0-SAFETY-FIX-V1: LEVEL_1 SELL 프로그램 소유권 (브로커 보유 확인은 RiskManager 가 수행).
    _own_block = emergency_sell_ownership_block(
        db, risk, side=order.side, symbol=order.symbol, quantity=int(order.quantity),
        trade_reason=order.trade_reason)
    if _own_block is not None and decision.decision != RiskDecision.REJECTED:
        from app.risk.kill_switch_policy import STRICT_REASON
        decision.decision = RiskDecision.REJECTED
        decision.reasons = [STRICT_REASON, _own_block] + [
            r for r in decision.reasons if r != STRICT_REASON]

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
        account_id=account_scope,
    )
    db.add(audit)
    # Force account-scoped unique-key checks before any external order call.
    db.flush()

    if decision.decision == RiskDecision.REJECTED:
        # #43: LIVE_SHADOW 추정 기록 — RiskManager가 LIVE_SHADOW 주문을 항상
        # REJECTED로 변환하므로 OrderAuditLog만 보면 거부 이력만 남는다. 그 위에
        # would-have 정보(다른 가드 통과 여부 + 추정 체결가)를 영구화해 운영자가
        # "실 시세에서 다른 가드까지 다 통과한 후보 비율"을 분석할 수 있게 한다.
        # actual_broker_order_sent는 invariant False — broker.place_order는 LIVE_SHADOW
        # 에서 절대 호출되지 않으며, OrderExecutor의 _EXECUTABLE_DECISIONS 가드가
        # 마지막 backstop이다 (REJECTED는 broker로 도달 X).
        if mode == OperationMode.LIVE_SHADOW:
            db.flush()  # audit.id 확보 — FK 무결성을 위해 commit 전에 ID 부여.
            # SHADOW_GATE_REASONS(LIVE_SHADOW 정책 게이트 + ENABLE_LIVE_TRADING
            # flag)는 *실제 risk rule이 아닌* 운영 게이트라 would-have 분석에서
            # 제외 — 운영자가 다음 단계로 승격하면 자연스럽게 사라지는 reason들.
            non_shadow_reasons = [
                r for r in decision.reasons if r not in SHADOW_GATE_REASONS
            ]
            shadow_would_have = (
                RiskDecision.APPROVED.value
                if not non_shadow_reasons
                else RiskDecision.REJECTED.value
            )
            shadow = ShadowTrade(
                audit_id=audit.id,
                mode=mode.value,
                requested_by_ai=requested_by_ai,
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                latest_price=quote.price,
                would_have_decision=shadow_would_have,
                would_have_reasons=list(non_shadow_reasons),
                actual_broker_order_sent=False,
                estimated_fill_price=quote.price,
                estimated_slippage_bps=0.0,
                estimation_method=_SHADOW_ESTIMATION_METHOD,
                confidence_note=_SHADOW_CONFIDENCE_NOTE,
                strategy=order.strategy,
                trade_reason=order.trade_reason,
                source=derive_order_source(order, requested_by_ai=requested_by_ai).value,
                client_order_id=order.client_order_id,
                ai_decision_meta=order.ai_decision_meta,
            )
            db.add(shadow)
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


CANCEL_TRADE_REASON = "kis_paper_cancel"
PROGRAM_TRADE_REASON = "kis_paper_auto"


def emergency_sell_ownership_block(db: Session, risk: RiskManager, *, side: OrderSide,
                                   symbol: str, quantity: int,
                                   trade_reason: str | None) -> str | None:
    """P0-SAFETY-FIX-V1: 새 정책 LEVEL_1 의 SELL 은 *프로그램 소유 수량* 이내의 프로그램
    청산(NORMAL_EXIT / EOD_FORCED_EXIT)만. RiskManager 는 브로커 보유만 확인하므로(DB 없음)
    소유권은 여기서 원장(trade_reason=kis_paper_auto 체결 순수량)으로 검사한다 —
    수동·타인 보유 매도 0. LEVEL_1(새 정책)이 아니면 None (기존 경로 무변경)."""
    from app.risk.kill_switch_policy import STRICT_REASON, emergency_order_policy
    if getattr(side, "value", side) != OrderSide.SELL.value:
        return None
    pol = emergency_order_policy(risk)
    tag = f"{STRICT_REASON} (LEVEL_1)"
    is_program_sell = (trade_reason or "") == PROGRAM_TRADE_REASON
    if not is_program_sell:
        if pol.active and pol.allows_reduce_only_sell:
            return f"{tag}: only program exits may sell; manual/unowned sell blocked"
        return None
    try:
        from app.core.config import get_settings
        from app.kis_paper.account_scope import (
            account_scope_block_reason,
            current_account_scope,
            program_holdings,
        )
        settings = get_settings()
        if account_scope_block_reason(settings) is not None:
            return "ACCOUNT_SCOPE_UNKNOWN: program SELL blocked"
        owned = int(program_holdings(db, current_account_scope(settings)).get(symbol, 0) or 0)
    except Exception as exc:  # noqa: BLE001
        return f"ACCOUNT_LEDGER_UNAVAILABLE: program SELL blocked ({type(exc).__name__})"
    if quantity <= 0 or quantity > owned:
        return f"{tag}: SELL exceeds program-owned quantity (owned={owned}, requested={quantity})"
    return None


async def route_cancel(
    *,
    original_order_id: str,
    org_no:            str | None,
    symbol:            str,
    side:              OrderSide,
    reason:            str,
    mode:              OperationMode,
    broker:            BrokerAdapter,
    risk:              RiskManager,
    db:                Session,
    client_order_id:   str,
    live_trading_enabled: bool = False,
) -> OrderRoutingResult:
    """미체결 주문 *취소* 의 단일 진입점 (INTRADAY-FLAT-BY-CLOSE V2).

    가드 → audit(항상 기록) → OrderExecutor.cancel (broker.cancel_order 유일 호출부).
    - 긴급정지 ON: 취소도 하지 않는다 (#37 — 킬스위치 중 자동 취소 금지, 운영자 수동).
      단 새 정책 LEVEL_1 은 미체결 BUY 취소 허용 (P0-SAFETY-FIX-V1, kill_switch_policy).
    - PAPER 모드 + paper broker + 실거래 OFF 에서만 (실전 계좌 취소 0건).
    - client_order_id idempotency (DuplicateOrderError) — 재시작/중복 tick 재전송 차단.
    취소 *접수* 는 취소 완료가 아니다 — 호출자가 일별체결 잔량으로 대조한다.
    """
    from app.brokers.kis import KisBrokerAdapter
    from app.execution.paper_trader import is_live_broker
    from app.kis_paper.account_scope import account_scope_for_broker
    cancel_scope = account_scope_for_broker(broker)

    duplicate_query = select(OrderAuditLog.id).where(OrderAuditLog.client_order_id == client_order_id)
    if cancel_scope is not None:
        duplicate_query = duplicate_query.where(OrderAuditLog.account_id == cancel_scope)
    if db.execute(duplicate_query).first() is not None:
        raise DuplicateOrderError(f"client_order_id={client_order_id} already processed")

    reasons = [f"CANCEL original={original_order_id} reason={reason}"]
    blocked: str | None = None
    if isinstance(broker, KisBrokerAdapter) and cancel_scope is None:
        blocked = "ACCOUNT_SCOPE_UNKNOWN: cancel refused"
    elif isinstance(broker, KisBrokerAdapter) and db.execute(
        select(OrderAuditLog.id).where(
            OrderAuditLog.account_id == cancel_scope,
            OrderAuditLog.broker_order_id == original_order_id,
            OrderAuditLog.side == side.value,
        ).limit(1)
    ).first() is None:
        blocked = "ORDER_NOT_OWNED_BY_CURRENT_ACCOUNT: cancel refused"
    from app.risk.kill_switch_policy import emergency_order_policy
    es_policy = emergency_order_policy(risk)
    if es_policy.active and not (es_policy.allows_buy_cancel
                                 and getattr(side, "value", side) == OrderSide.BUY.value):
        # P0-SAFETY-FIX-V1: 새 정책 LEVEL_1 은 미체결 *BUY* 취소만 허용(신규 노출 축소).
        #   LEVEL_2/3·legacy strict·SELL 취소는 기존대로 금지 (#37 — 운영자 수동).
        blocked = "emergency stop is enabled — auto cancel prohibited (#37)"
    elif mode != OperationMode.PAPER:
        blocked = f"cancel allowed only in PAPER mode (mode={mode.value})"
    elif live_trading_enabled:
        blocked = "live trading flag is on — paper cancel path refused"
    elif is_live_broker(broker):
        blocked = "broker is not a paper broker — live cancel prohibited"
    audit = OrderAuditLog(
        mode=mode.value, requested_by_ai=False, symbol=symbol, side=side.value,
        quantity=0, order_type="CANCEL", limit_price=None, latest_price=0,
        decision=(RiskDecision.REJECTED if blocked else RiskDecision.APPROVED).value,
        reasons=reasons + ([blocked] if blocked else []),
        trade_reason=CANCEL_TRADE_REASON, strategy="EOD_CANCEL",
        client_order_id=client_order_id, source="STRATEGY",
        account_id=cancel_scope,
    )
    db.add(audit)
    if blocked:
        db.commit()
        return OrderRoutingResult(decision=RiskDecision.REJECTED,
                                  reasons=list(audit.reasons), audit=audit)
    db.flush()
    try:
        result = await OrderExecutor(broker, db).cancel(
            original_order_id, audit, org_no=org_no, symbol=symbol, side=side)
    except Exception as exc:  # noqa: BLE001 — 실패도 audit 에 남긴다.
        audit.executed = False
        audit.broker_status = "ERROR"
        audit.message = f"{type(exc).__name__}: {exc}"[:255]
        db.commit()
        return OrderRoutingResult(decision=RiskDecision.APPROVED,
                                  reasons=list(audit.reasons), audit=audit)
    db.commit()
    return OrderRoutingResult(decision=RiskDecision.APPROVED,
                              reasons=list(audit.reasons), audit=audit, result=result)
