"""Virtual Order Ledger tests (148, MUST)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.virtual.order_ledger import (
    STATUS_ACCEPTED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_FILLED,
    STATUS_NEW,
    STATUS_PARTIALLY_FILLED,
    STATUS_REJECTED,
    TERMINAL_STATES,
    VirtualOrderError,
    create_order,
    is_terminal,
    transition,
)


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _new(db, **overrides):
    defaults = dict(
        symbol="005930", side="BUY", quantity=10,
        mode="SIMULATION",
    )
    defaults.update(overrides)
    return create_order(db, **defaults)


# ---------- create_order ----------

def test_create_order_starts_in_new_status():
    Session = _session()
    with Session() as db:
        order = _new(db)
        db.commit()
        assert order.status == STATUS_NEW
        assert order.filled_quantity == 0
        assert order.id is not None


def test_create_order_rejects_invalid_quantity():
    Session = _session()
    with Session() as db:
        with pytest.raises(ValueError):
            _new(db, quantity=0)


def test_create_order_rejects_unknown_side():
    Session = _session()
    with Session() as db:
        with pytest.raises(ValueError):
            _new(db, side="LONG")


def test_create_order_persists_optional_audit_link():
    """OrderAuditLog cross-reference — audit_id가 채워진 주문은 양 테이블 join이
    가능해야 한다."""
    Session = _session()
    with Session() as db:
        from app.db.models import OrderAuditLog
        audit = OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=100,
            decision="APPROVED", reasons=[],
        )
        db.add(audit)
        db.flush()
        order = _new(db, audit_id=audit.id)
        db.commit()
        assert order.audit_id == audit.id


# ---------- transition: legal paths ----------

def test_new_to_accepted_transition():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_ACCEPTED, reason="risk_passed")
        assert order.status == STATUS_ACCEPTED
        assert order.structured_reason == "risk_passed"


def test_new_to_rejected_transition():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_REJECTED, reason="notional_exceeds_limit")
        assert order.status == STATUS_REJECTED
        assert is_terminal(order)


def test_accepted_to_filled_via_partial():
    Session = _session()
    with Session() as db:
        order = _new(db, quantity=10)
        transition(db, order, to_status=STATUS_ACCEPTED)
        transition(db, order, to_status=STATUS_PARTIALLY_FILLED,
                   filled_delta=4, avg_fill_price=100)
        assert order.filled_quantity == 4
        assert order.avg_fill_price == 100
        transition(db, order, to_status=STATUS_FILLED,
                   filled_delta=6, avg_fill_price=105)
        assert order.filled_quantity == 10
        assert order.avg_fill_price == 105
        assert is_terminal(order)
        assert order.filled_at is not None


def test_partial_can_be_cancelled():
    Session = _session()
    with Session() as db:
        order = _new(db, quantity=10)
        transition(db, order, to_status=STATUS_ACCEPTED)
        transition(db, order, to_status=STATUS_PARTIALLY_FILLED,
                   filled_delta=3, avg_fill_price=100)
        transition(db, order, to_status=STATUS_CANCELLED, reason="operator_cancel")
        assert order.status == STATUS_CANCELLED
        # 부분 체결 수량은 보존됨 — cancel은 잔량만 취소.
        assert order.filled_quantity == 3


def test_accepted_can_expire():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_ACCEPTED)
        transition(db, order, to_status=STATUS_EXPIRED, reason="day_order_timeout")
        assert order.status == STATUS_EXPIRED
        assert is_terminal(order)


# ---------- transition: illegal paths blocked ----------

def test_terminal_state_cannot_transition():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_REJECTED)
        with pytest.raises(VirtualOrderError):
            transition(db, order, to_status=STATUS_ACCEPTED)


def test_new_cannot_skip_to_filled():
    """NEW에서 직접 FILLED로 갈 수 없다 — RiskManager 평가 우회 차단."""
    Session = _session()
    with Session() as db:
        order = _new(db)
        with pytest.raises(VirtualOrderError):
            transition(db, order, to_status=STATUS_FILLED)


def test_filled_cannot_be_uncancelled():
    """FILLED 종료 후 CANCELLED 시도 — 거부 (이중 결정 차단)."""
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_ACCEPTED)
        transition(db, order, to_status=STATUS_FILLED,
                   filled_delta=10, avg_fill_price=100)
        with pytest.raises(VirtualOrderError):
            transition(db, order, to_status=STATUS_CANCELLED)


def test_unknown_target_status_raises():
    Session = _session()
    with Session() as db:
        order = _new(db)
        with pytest.raises(VirtualOrderError):
            transition(db, order, to_status="WAT")


# ---------- filled_delta validation ----------

def test_filled_delta_overflow_rejected():
    """filled_quantity가 quantity를 초과하지 않는다 — 단순 invariant."""
    Session = _session()
    with Session() as db:
        order = _new(db, quantity=5)
        transition(db, order, to_status=STATUS_ACCEPTED)
        with pytest.raises(VirtualOrderError):
            transition(db, order, to_status=STATUS_FILLED,
                       filled_delta=10, avg_fill_price=100)


def test_negative_filled_delta_rejected():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_ACCEPTED)
        with pytest.raises(ValueError):
            transition(db, order, to_status=STATUS_PARTIALLY_FILLED,
                       filled_delta=-1, avg_fill_price=100)


def test_terminal_states_set_filled_at_timestamp():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_REJECTED)
        assert order.filled_at is not None


def test_non_terminal_does_not_set_filled_at():
    Session = _session()
    with Session() as db:
        order = _new(db)
        transition(db, order, to_status=STATUS_ACCEPTED)
        assert order.filled_at is None


def test_terminal_states_constant_includes_all_four():
    assert TERMINAL_STATES == {
        STATUS_FILLED, STATUS_CANCELLED, STATUS_REJECTED, STATUS_EXPIRED,
    }
