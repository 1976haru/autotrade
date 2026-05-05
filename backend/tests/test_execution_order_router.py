import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderType
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
