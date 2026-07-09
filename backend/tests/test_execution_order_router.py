import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderType, Position
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.order_router import route_order
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _order(qty: int = 1) -> OrderRequest:
    return OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=qty, order_type=OrderType.MARKET,
    )


def _sell(qty: int = 1) -> OrderRequest:
    return OrderRequest(
        symbol="005930", side=OrderSide.SELL, quantity=qty, order_type=OrderType.MARKET,
    )


def _seed_closed_loss(db, *, symbol: str = "005930", buy: int = 100_000, sell: int = 90_000) -> None:
    db.add(OrderAuditLog(
        mode="SIMULATION", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=buy,
        decision="APPROVED", reasons=[], executed=True,
        avg_fill_price=buy, filled_quantity=1, broker_status="FILLED",
    ))
    db.flush()
    db.add(OrderAuditLog(
        mode="SIMULATION", symbol=symbol, side="SELL", quantity=1,
        order_type="MARKET", latest_price=sell,
        decision="APPROVED", reasons=[], executed=True,
        avg_fill_price=sell, filled_quantity=1, broker_status="FILLED",
    ))


def run(coro):
    return asyncio.run(coro)


def test_simulation_small_order_is_approved_and_executed():
    Session = _session_factory()
    with Session() as db:
        broker = MockBrokerAdapter()
        result = run(route_order(
            order=_order(1), requested_by_ai=False,
            mode=OperationMode.SIMULATION,
            broker=broker, risk=RiskManager(RiskPolicy()), db=db,
        ))
        assert result.decision == RiskDecision.APPROVED
        assert result.result is not None
        assert result.result.status.value == "FILLED"
        assert result.audit.executed is True
        assert result.audit.broker_status == "FILLED"


def test_daily_order_cap_applies_to_buy_not_sell():
    # A(2026-06-05): 일일 주문 횟수 한도는 신규 진입(BUY)에만 적용.
    #   청산(SELL)은 면제 — cap 사유가 SELL 의 reasons 에 누적되면 안 된다.
    Session = _session_factory()
    policy = RiskPolicy(max_orders_per_day=1)
    with Session() as db:
        broker = MockBrokerAdapter()
        r1 = run(route_order(order=_order(1), requested_by_ai=False,
                 mode=OperationMode.SIMULATION, broker=broker,
                 risk=RiskManager(policy), db=db))
        assert r1.decision == RiskDecision.APPROVED      # 1st BUY → today count = 1.

        r2 = run(route_order(order=_order(1), requested_by_ai=False,
                 mode=OperationMode.SIMULATION, broker=broker,
                 risk=RiskManager(policy), db=db))
        assert r2.decision == RiskDecision.REJECTED      # 2nd BUY → 한도 차단.
        assert any("max_orders_per_day" in x for x in r2.reasons)

        sell = OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=1,
                            order_type=OrderType.MARKET)
        r3 = run(route_order(order=sell, requested_by_ai=False,
                 mode=OperationMode.SIMULATION, broker=broker,
                 risk=RiskManager(policy), db=db))
        assert not any("max_orders_per_day" in x for x in r3.reasons)  # SELL 면제.


def test_consecutive_loss_cooldown_blocks_five_buy_candidates_then_resumes():
    Session = _session_factory()
    policy = RiskPolicy(
        max_order_notional=10_000_000,
        consecutive_loss_limit=5,
        consecutive_loss_cooldown_buys=5,
        consecutive_loss_enabled_modes=frozenset({"SIMULATION"}),
    )
    with Session() as db:
        for _ in range(5):
            _seed_closed_loss(db)
        db.commit()

        broker = MockBrokerAdapter(initial_cash=10_000_000)
        risk = RiskManager(policy)
        blocked = [
            run(route_order(
                order=_order(1), requested_by_ai=False,
                mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
            ))
            for _ in range(5)
        ]
        assert all(r.decision == RiskDecision.REJECTED for r in blocked)
        assert all(any("ConsecutiveLossRule" in x for x in r.reasons) for r in blocked)
        assert broker.orders == {}

        resumed = run(route_order(
            order=_order(1), requested_by_ai=False,
            mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
        ))
        assert resumed.decision == RiskDecision.APPROVED
        assert resumed.result is not None
        assert not any("ConsecutiveLossRule" in x for x in resumed.reasons)


def test_consecutive_loss_cooldown_does_not_block_sell_exit():
    Session = _session_factory()
    policy = RiskPolicy(
        max_order_notional=10_000_000,
        consecutive_loss_limit=5,
        consecutive_loss_cooldown_buys=5,
        consecutive_loss_enabled_modes=frozenset({"SIMULATION"}),
    )
    with Session() as db:
        for _ in range(5):
            _seed_closed_loss(db)
        db.commit()

        broker = MockBrokerAdapter(initial_cash=10_000_000)
        broker.positions["005930"] = Position(
            symbol="005930", quantity=1, avg_price=75_000, market_price=75_000,
        )
        risk = RiskManager(policy)
        result = run(route_order(
            order=_sell(1), requested_by_ai=False,
            mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
        ))
        assert result.decision == RiskDecision.APPROVED
        assert result.result is not None
        assert result.result.side == OrderSide.SELL


def test_consecutive_loss_and_daily_loss_reasons_coexist_without_overwrite():
    Session = _session_factory()
    policy = RiskPolicy(
        max_order_notional=10_000_000,
        max_daily_loss=200_000,
        consecutive_loss_limit=5,
        consecutive_loss_cooldown_buys=5,
        consecutive_loss_enabled_modes=frozenset({"SIMULATION"}),
    )
    with Session() as db:
        for _ in range(5):
            _seed_closed_loss(db, buy=100_000, sell=50_000)
        db.commit()

        result = run(route_order(
            order=_order(1), requested_by_ai=False,
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(initial_cash=10_000_000),
            risk=RiskManager(policy),
            db=db,
        ))
        assert result.decision == RiskDecision.REJECTED
        assert "daily loss limit reached" in result.reasons
        assert any("ConsecutiveLossRule" in x for x in result.reasons)


def test_oversized_order_is_rejected_with_audit_only():
    Session = _session_factory()
    with Session() as db:
        result = run(route_order(
            order=_order(50), requested_by_ai=False,  # 50 * 75_000 = 3.75M > 1M cap
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()), db=db,
        ))
        assert result.decision == RiskDecision.REJECTED
        assert result.audit.executed is False
        assert result.result is None
        assert any("notional" in r for r in result.reasons)
    # audit should be persisted even on rejection
    with Session() as db2:
        rows = db2.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1
        assert rows[0].decision == "REJECTED"


def test_manual_approval_mode_enqueues_without_executing():
    Session = _session_factory()
    with Session() as db:
        # 061 queue gate: needs enable_live_trading=True for the order to
        # actually queue rather than getting REJECTED at the flag.
        result = run(route_order(
            order=_order(1), requested_by_ai=False,
            mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy(enable_live_trading=True)),
            db=db,
        ))
        assert result.decision == RiskDecision.NEEDS_APPROVAL
        assert result.approval is not None
        assert result.approval.status == "PENDING"
        assert result.result is None
        assert result.audit.executed is False
    with Session() as db2:
        approvals = db2.execute(select(PendingApproval)).scalars().all()
        assert len(approvals) == 1


def test_shadow_mode_rejects_without_calling_broker():
    Session = _session_factory()
    with Session() as db:
        # If route reached the broker, mock would fill — assert it didn't.
        broker = MockBrokerAdapter(initial_cash=10_000_000)
        starting_cash = broker.cash
        result = run(route_order(
            order=_order(1), requested_by_ai=False,
            mode=OperationMode.LIVE_SHADOW,
            broker=broker, risk=RiskManager(RiskPolicy()), db=db,
        ))
        assert result.decision == RiskDecision.REJECTED
        assert any("LIVE_SHADOW" in r for r in result.reasons)
        assert broker.cash == starting_cash  # broker untouched


def test_ai_execution_blocked_when_flag_off():
    Session = _session_factory()
    with Session() as db:
        result = run(route_order(
            order=_order(1), requested_by_ai=True,
            mode=OperationMode.LIVE_AI_EXECUTION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy(enable_live_trading=True, enable_ai_execution=False)),
            db=db,
        ))
        assert result.decision == RiskDecision.REJECTED
        assert any("AI execution" in r for r in result.reasons)
        assert result.result is None


def test_ai_assist_mode_enqueues_without_executing():
    """Mirror of test_manual_approval_mode_enqueues_without_executing for
    LIVE_AI_ASSIST. Both modes share the RiskManager early-return path and
    must remain symmetric — adding LIVE wire-up should not accidentally
    diverge them.
    """
    Session = _session_factory()
    with Session() as db:
        result = run(route_order(
            order=_order(1), requested_by_ai=False,
            mode=OperationMode.LIVE_AI_ASSIST,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy(enable_live_trading=True)),
            db=db,
        ))
        assert result.decision == RiskDecision.NEEDS_APPROVAL
        assert result.approval is not None
        assert result.approval.status == "PENDING"
        assert result.result is None
        assert result.audit.executed is False


def test_audit_records_requested_by_ai_flag():
    Session = _session_factory()
    with Session() as db:
        run(route_order(
            order=_order(1), requested_by_ai=True,
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()), db=db,
        ))
    with Session() as db2:
        audit = db2.execute(select(OrderAuditLog)).scalar_one()
        assert audit.requested_by_ai is True
