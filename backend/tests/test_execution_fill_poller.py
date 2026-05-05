import asyncio
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import BrokerAdapter, OrderResult, OrderSide, OrderStatus
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.execution.fill_poller import FillPoller, poll_once


def run(coro):
    return asyncio.run(coro)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(
    db,
    *,
    broker_order_id: str | None = "K-001",
    broker_status:   str | None = "RECEIVED",
    filled_quantity: int = 0,
    avg_fill_price:  int | None = None,
    executed:        bool = True,
) -> OrderAuditLog:
    a = OrderAuditLog(
        mode="PAPER", symbol="005930", side="BUY", quantity=10,
        order_type="MARKET", latest_price=75_000,
        decision="APPROVED", reasons=[],
        executed=executed,
        broker_order_id=broker_order_id,
        broker_status=broker_status,
        filled_quantity=filled_quantity,
        avg_fill_price=avg_fill_price,
    )
    db.add(a)
    db.commit()
    return a


class _ScriptedBroker(BrokerAdapter):
    """Minimal broker stub — only get_order_status is interesting for the poller."""

    def __init__(self, status_map: dict[str, OrderResult] | None = None,
                 raise_on: dict[str, Exception] | None = None):
        self.status_map = status_map or {}
        self.raise_on   = raise_on or {}
        self.calls: list[str] = []

    async def get_order_status(self, order_id: str) -> OrderResult:
        self.calls.append(order_id)
        if order_id in self.raise_on:
            raise self.raise_on[order_id]
        return self.status_map.get(order_id, OrderResult(
            order_id=order_id, status=OrderStatus.RECEIVED,
            symbol="005930", side=OrderSide.BUY, quantity=0,
        ))

    # All other abstract methods raise — they are not used by the poller.
    async def get_price(self, symbol: str) -> Any:        raise NotImplementedError
    async def get_balance(self) -> Any:                   raise NotImplementedError
    async def get_positions(self) -> list[Any]:           raise NotImplementedError
    async def place_order(self, order: Any) -> Any:       raise NotImplementedError
    async def cancel_order(self, order_id: str) -> Any:   raise NotImplementedError


# ---------- poll_once ----------

def test_poll_once_no_candidates_returns_zero():
    Session = _session_factory()
    with Session() as db:
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db)) == 0
        assert broker.calls == []


def test_poll_once_skips_unexecuted_audits():
    Session = _session_factory()
    with Session() as db:
        _audit(db, executed=False)
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db)) == 0
        assert broker.calls == []


def test_poll_once_skips_already_filled():
    Session = _session_factory()
    with Session() as db:
        _audit(db, broker_status="FILLED", filled_quantity=10)
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db)) == 0
        assert broker.calls == []


def test_poll_once_updates_received_to_filled():
    Session = _session_factory()
    filled_result = OrderResult(
        order_id="K-001", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_500,
    )
    with Session() as db:
        a = _audit(db)
        broker = _ScriptedBroker(status_map={"K-001": filled_result})
        assert run(poll_once(broker, db)) == 1
        # Reload to confirm commit
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "FILLED"
        assert loaded.filled_quantity == 10
        assert loaded.avg_fill_price == 75_500


def test_poll_once_advances_partially_filled():
    Session = _session_factory()
    partial = OrderResult(
        order_id="K-001", status=OrderStatus.PARTIALLY_FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=4, avg_fill_price=75_300,
    )
    with Session() as db:
        a = _audit(db, broker_status="PARTIALLY_FILLED", filled_quantity=2)
        broker = _ScriptedBroker(status_map={"K-001": partial})
        assert run(poll_once(broker, db)) == 1
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "PARTIALLY_FILLED"
        assert loaded.filled_quantity == 4


def test_poll_once_skips_when_nothing_changed():
    Session = _session_factory()
    same = OrderResult(
        order_id="K-001", status=OrderStatus.RECEIVED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=0,
    )
    with Session() as db:
        _audit(db, broker_status="RECEIVED", filled_quantity=0)
        broker = _ScriptedBroker(status_map={"K-001": same})
        assert run(poll_once(broker, db)) == 0


def test_poll_once_returns_zero_when_broker_does_not_implement():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
        broker = _ScriptedBroker(raise_on={"K-001": NotImplementedError("mock no support")})
        assert run(poll_once(broker, db)) == 0


def test_poll_once_continues_past_a_failing_row():
    Session = _session_factory()
    ok = OrderResult(
        order_id="K-002", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_000,
    )
    with Session() as db:
        _audit(db, broker_order_id="K-001")
        _audit(db, broker_order_id="K-002")
        broker = _ScriptedBroker(
            status_map={"K-002": ok},
            raise_on={"K-001": RuntimeError("upstream flaked")},
        )
        assert run(poll_once(broker, db)) == 1
    with Session() as db2:
        rows = db2.execute(select(OrderAuditLog).order_by(OrderAuditLog.id)).scalars().all()
        assert rows[0].broker_status == "RECEIVED"  # untouched after failure
        assert rows[1].broker_status == "FILLED"


# ---------- FillPoller lifecycle ----------

def test_fill_poller_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        FillPoller(broker_factory=lambda: None, session_factory=lambda: None, interval=0)


def test_fill_poller_start_stop_invokes_poll_at_least_once():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
    filled = OrderResult(
        order_id="K-001", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_000,
    )
    broker = _ScriptedBroker(status_map={"K-001": filled})

    async def driver():
        poller = FillPoller(
            broker_factory=lambda: broker,
            session_factory=Session,
            interval=1,
        )
        poller.start()
        # Yield long enough for the first tick; the loop sleeps after each tick.
        await asyncio.sleep(0.05)
        await poller.stop()

    run(driver())
    assert broker.calls == ["K-001"]
