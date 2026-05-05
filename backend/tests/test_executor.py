import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.execution.executor import OrderExecutor


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(db, decision="APPROVED"):
    a = OrderAuditLog(
        mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
        order_type="MARKET", latest_price=75_000,
        decision=decision, reasons=[],
    )
    db.add(a)
    db.flush()
    return a


def _order(qty=1):
    return OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=qty, order_type=OrderType.MARKET,
    )


def test_execute_calls_broker_and_returns_result():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        result = asyncio.run(OrderExecutor(MockBrokerAdapter(), db).execute(_order(2), audit))
        assert result.status.value == "FILLED"
        assert result.filled_quantity == 2
        assert result.avg_fill_price == 75_000


def test_execute_updates_audit_in_place():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        result = asyncio.run(OrderExecutor(MockBrokerAdapter(), db).execute(_order(), audit))
        assert audit.executed is True
        assert audit.broker_order_id == result.order_id
        assert audit.broker_status == "FILLED"
        assert audit.filled_quantity == 1
        assert audit.avg_fill_price == 75_000
        assert audit.message == "mock filled"


def test_execute_does_not_commit():
    """Executor stages changes; the caller decides when to commit so multi-step
    flows (approval) can group multiple updates into a single transaction."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        asyncio.run(OrderExecutor(MockBrokerAdapter(), db).execute(_order(), audit))
        # Roll back; nothing should have been persisted by execute() itself.
        db.rollback()

    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog)).scalar_one_or_none()
        # The pre-flush audit is also rolled back since flush != commit on most SQLite setups
        # but to be safe we just check executed flag was not persisted.
        if loaded is not None:
            assert loaded.executed is False


def test_execute_propagates_broker_rejected_status():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        # Cash is 10_000_000 by default; ordering 1000 shares at 75_000 = 75M -> rejected
        big_order = OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1000, order_type=OrderType.MARKET,
        )
        result = asyncio.run(OrderExecutor(MockBrokerAdapter(), db).execute(big_order, audit))
        assert result.status.value == "REJECTED"
        assert audit.executed is True  # we attempted; broker rejected
        assert audit.broker_status == "REJECTED"


def test_execute_with_none_audit_raises():
    Session = _session()
    with Session() as db:
        with pytest.raises(ValueError, match="audit row is required"):
            asyncio.run(OrderExecutor(MockBrokerAdapter(), db).execute(_order(), None))
