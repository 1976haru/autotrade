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
    ApprovalRiskCheckFailedError,
    PermissionGate,
)
from app.risk.risk_manager import RiskManager, RiskPolicy


def _risk_for_live_manual():
    """070: PermissionGate.approve re-evaluates risk against current broker
    state, so the LIVE_MANUAL_APPROVAL queue tests need the global flag on
    (otherwise re-eval would block at the queue gate added in 061)."""
    return RiskManager(RiskPolicy(enable_live_trading=True))


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
            approval.id, broker, _risk_for_live_manual(),
            decided_by="user", note="ok",
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
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), _risk_for_live_manual()))


def test_cannot_reject_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), _risk_for_live_manual()))
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
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), _risk_for_live_manual()))


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


# ---------- 070: re-evaluation at approve time ----------

def test_approve_rejects_when_emergency_stop_toggled_after_submit():
    """Operator pulls emergency_stop between submit and approve. Re-eval must
    block execution and leave the approval as PENDING for retry."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        risk.set_emergency_stop(True)

        with pytest.raises(ApprovalRiskCheckFailedError) as excinfo:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("emergency stop" in r for r in excinfo.value.reasons)

        # Approval still PENDING — operator can retry once the stop clears
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"
        # And the audit row was untouched: no execution attempted
        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is False


def test_approve_rejects_when_notional_now_exceeds_limit():
    """Price moved enough between submit and approve to violate the notional
    cap. Re-eval surfaces the violation and blocks execution."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=2),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # MockBroker default price=75_000 → 2 qty * 75_000 = 150_000 < 1M cap
        # Tighten the policy so re-eval finds the violation.
        risk = RiskManager(RiskPolicy(enable_live_trading=True, max_order_notional=100_000))

        with pytest.raises(ApprovalRiskCheckFailedError) as excinfo:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("max_order_notional" in r for r in excinfo.value.reasons)

        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"


def test_approve_rejects_when_live_trading_flag_toggled_off():
    """The global ENABLE_LIVE_TRADING flag was on at submit (enabling the
    queue) but flipped off before approve. 061's queue gate fires at re-eval."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # Submit happened with flag on (semantically). Now re-eval with flag off.
        risk = RiskManager(RiskPolicy(enable_live_trading=False))

        with pytest.raises(ApprovalRiskCheckFailedError) as excinfo:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("live trading" in r for r in excinfo.value.reasons)

        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"


def test_approve_proceeds_when_re_eval_only_returns_mode_marker():
    """Steady state: no violations, mode-required-approval marker is the only
    reason in the re-eval result. The gate must let execution through."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        approved, result = asyncio.run(gate.approve(
            approval.id, MockBrokerAdapter(), _risk_for_live_manual(),
        ))
        assert approved.status == "APPROVED"
        assert result.status.value == "FILLED"


# ---------- 076: persist re-eval-failed approve attempts ----------

def test_re_eval_failure_appends_an_attempts_entry():
    """The first time approve fails on re-eval, attempts should grow to length
    1 with {at, decided_by, reasons} populated."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        risk.set_emergency_stop(True)

        with pytest.raises(ApprovalRiskCheckFailedError):
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk,
                                     decided_by="ops1"))

        refreshed = db.get(PendingApproval, approval.id)
        assert len(refreshed.attempts) == 1
        entry = refreshed.attempts[0]
        assert entry["decided_by"] == "ops1"
        assert any("emergency stop" in r for r in entry["reasons"])
        assert "at" in entry  # ISO timestamp


def test_re_eval_failures_accumulate_across_repeated_attempts():
    """Repeated failed attempts append; each carries its own {at, decided_by,
    reasons}. Operator handover ("did anyone try this already?") relies on
    the count + most-recent entry."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        risk.set_emergency_stop(True)

        for who in ("ops1", "ops2", "ops3"):
            with pytest.raises(ApprovalRiskCheckFailedError):
                asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk,
                                         decided_by=who))

        refreshed = db.get(PendingApproval, approval.id)
        assert len(refreshed.attempts) == 3
        assert [e["decided_by"] for e in refreshed.attempts] == ["ops1", "ops2", "ops3"]


def test_successful_approve_does_not_append_attempts():
    """Only re-eval-blocked attempts persist; the successful path doesn't
    record on attempts (the audit row already records fulfillment)."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        asyncio.run(gate.approve(approval.id, MockBrokerAdapter(),
                                 _risk_for_live_manual()))
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.attempts == []
