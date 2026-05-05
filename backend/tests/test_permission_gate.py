import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import OrderAuditLog, PendingApproval
from app.permission.gate import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    PermissionGate,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(db, symbol="005930"):
    a = OrderAuditLog(
        mode="LIVE_MANUAL_APPROVAL", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=75_000,
        decision="NEEDS_APPROVAL", reasons=["manual approval required"],
    )
    db.add(a)
    db.flush()
    return a


def _order(symbol="005930", qty=1):
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty,
                        order_type=OrderType.MARKET)


def test_submit_creates_pending_row_linked_to_audit():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        approval = PermissionGate(db).submit(
            audit=audit, order=_order(), mode=OperationMode.LIVE_MANUAL_APPROVAL,
        )
        assert approval.status == "PENDING"
        assert approval.audit_id == audit.id
        assert approval.symbol == "005930"
        assert approval.mode == "LIVE_MANUAL_APPROVAL"
        assert approval.decided_at is None


def test_list_pending_excludes_decided():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db, "005930"), order=_order("005930"),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a2 = gate.submit(audit=_audit(db, "000660"), order=_order("000660"),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id, note="not now")
        pending = gate.list_pending()
        assert [p.id for p in pending] == [a2.id]


def test_get_unknown_id_raises():
    Session = _session()
    with Session() as db:
        with pytest.raises(ApprovalNotFoundError):
            PermissionGate(db).get(9999)


def test_approve_executes_order_and_updates_audit():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=2),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        broker = MockBrokerAdapter()
        approved, result = asyncio.run(gate.approve(
            approval.id, broker, decided_by="user", note="ok",
        ))
        assert approved.status == "APPROVED"
        assert approved.decided_by == "user"
        assert approved.decided_at is not None
        assert result.status.value == "FILLED"
        assert result.filled_quantity == 2

        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is True
        assert refreshed_audit.broker_status == "FILLED"
        assert refreshed_audit.filled_quantity == 2
        assert refreshed_audit.avg_fill_price == 75_000


def test_reject_does_not_execute_or_touch_audit_executed_flag():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        rejected = gate.reject(approval.id, decided_by="user", note="risky")
        assert rejected.status == "REJECTED"
        assert rejected.note == "risky"

        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is False
        assert refreshed_audit.broker_order_id is None


def test_cannot_approve_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter()))


def test_cannot_reject_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        asyncio.run(gate.approve(approval.id, MockBrokerAdapter()))
        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.reject(approval.id)


def test_submit_persists_via_session():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        approval = PermissionGate(db).submit(
            audit=audit, order=_order(), mode=OperationMode.LIVE_MANUAL_APPROVAL,
        )
        approval_id = approval.id
    with Session() as db2:
        loaded = db2.execute(
            select(PendingApproval).where(PendingApproval.id == approval_id)
        ).scalar_one()
        assert loaded.status == "PENDING"
