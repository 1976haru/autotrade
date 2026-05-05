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


# ---------- cancel ----------

def test_cancel_marks_approval_cancelled_with_metadata():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        cancelled = gate.cancel(approval.id, decided_by="user", note="signal stale")
        assert cancelled.status == "CANCELLED"
        assert cancelled.decided_by == "user"
        assert cancelled.note == "signal stale"
        assert cancelled.decided_at is not None


def test_cancel_does_not_execute_or_touch_audit_executed_flag():
    """Cancel must not run the order or mutate audit beyond what reject does."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        gate.cancel(approval.id)

        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is False
        assert refreshed_audit.broker_order_id is None


def test_cancelled_approval_excluded_from_list_pending():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.cancel(approval.id)
        assert gate.list_pending() == []


def test_cannot_cancel_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.cancel(approval.id)


def test_cannot_approve_after_cancel():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.cancel(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter()))


def test_cancel_unknown_id_raises_not_found():
    Session = _session()
    with Session() as db:
        with pytest.raises(ApprovalNotFoundError):
            PermissionGate(db).cancel(99999)


# ---------- list_decided ----------

def test_list_decided_excludes_pending():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        # a2 stays PENDING — only a1 should appear in list_decided
        gate.submit(audit=_audit(db), order=_order(),
                    mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id)

        decided = gate.list_decided()
        assert len(decided) == 1
        assert decided[0].id == a1.id
        assert decided[0].status == "REJECTED"


def test_list_decided_status_filter():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a2 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a3 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id)
        gate.cancel(a2.id)
        gate.cancel(a3.id)

        cancelled = gate.list_decided(status="CANCELLED")
        assert {a.id for a in cancelled} == {a2.id, a3.id}
        rejected = gate.list_decided(status="REJECTED")
        assert {a.id for a in rejected} == {a1.id}


def test_list_decided_orders_most_recent_first():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a2 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id)
        gate.cancel(a2.id)  # decided after a1

        decided = gate.list_decided()
        # Most recent decided_at first
        assert decided[0].id == a2.id
        assert decided[1].id == a1.id


def test_list_decided_limit_offset():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        ids = []
        for _ in range(5):
            a = gate.submit(audit=_audit(db), order=_order(),
                            mode=OperationMode.LIVE_MANUAL_APPROVAL)
            gate.cancel(a.id)
            ids.append(a.id)

        first_two  = gate.list_decided(limit=2)
        assert len(first_two) == 2
        next_two = gate.list_decided(limit=2, offset=2)
        assert len(next_two) == 2
        # No overlap between pages
        assert {r.id for r in first_two}.isdisjoint({r.id for r in next_two})
