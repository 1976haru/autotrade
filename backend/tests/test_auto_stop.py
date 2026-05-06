"""Auto emergency_stop on consecutive rejections tests (182, MUST)."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import EmergencyStopEvent, OrderAuditLog
from app.risk.auto_stop import (
    consecutive_rejection_count,
    maybe_trigger_auto_stop,
)
from app.risk.risk_manager import RiskManager, RiskPolicy


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit_row(decision="REJECTED"):
    return OrderAuditLog(
        mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
        order_type="MARKET", latest_price=100,
        decision=decision, reasons=[],
    )


# ---------- consecutive_rejection_count ----------

def test_count_zero_when_db_empty():
    Session = _session()
    with Session() as db:
        assert consecutive_rejection_count(db, limit=5) == 0


def test_count_zero_when_limit_zero():
    Session = _session()
    with Session() as db:
        for _ in range(10):
            db.add(_audit_row("REJECTED"))
        db.commit()
        assert consecutive_rejection_count(db, limit=0) == 0


def test_count_returns_consecutive_prefix():
    """[REJECTED, REJECTED, REJECTED] (최신순) → 3."""
    Session = _session()
    with Session() as db:
        for _ in range(3):
            db.add(_audit_row("REJECTED"))
        db.commit()
        assert consecutive_rejection_count(db, limit=5) == 3


def test_count_breaks_at_first_non_rejected():
    """[REJECTED, REJECTED, APPROVED, REJECTED] (id 오름차순으로 시드)
    → 최신순 desc로 [REJECTED, APPROVED, REJECTED, REJECTED] → 카운트=1."""
    Session = _session()
    with Session() as db:
        # id 오름차순(시간 흐름)으로: APPROVED → REJECTED → REJECTED → APPROVED → REJECTED
        # 최신은 마지막 REJECTED.
        # 최신순 desc: REJECTED, APPROVED, REJECTED, REJECTED, APPROVED
        # 첫 1개만 REJECTED → 카운트 1.
        db.add(_audit_row("APPROVED"))
        db.add(_audit_row("REJECTED"))
        db.add(_audit_row("REJECTED"))
        db.add(_audit_row("APPROVED"))
        db.add(_audit_row("REJECTED"))
        db.commit()
        assert consecutive_rejection_count(db, limit=10) == 1


def test_count_capped_at_limit():
    """20건 모두 REJECTED지만 limit=5면 5만 반환."""
    Session = _session()
    with Session() as db:
        for _ in range(20):
            db.add(_audit_row("REJECTED"))
        db.commit()
        assert consecutive_rejection_count(db, limit=5) == 5


# ---------- maybe_trigger_auto_stop ----------

def test_no_op_when_threshold_zero():
    Session = _session()
    with Session() as db:
        for _ in range(10):
            db.add(_audit_row("REJECTED"))
        db.commit()
        risk = RiskManager(RiskPolicy())
        triggered = maybe_trigger_auto_stop(db, risk=risk, threshold=0)
    assert triggered is False
    assert risk.emergency_stop is False


def test_no_op_when_below_threshold():
    """3건만 REJECTED + threshold=5 → 미트리거."""
    Session = _session()
    with Session() as db:
        for _ in range(3):
            db.add(_audit_row("REJECTED"))
        db.commit()
        risk = RiskManager(RiskPolicy())
        triggered = maybe_trigger_auto_stop(db, risk=risk, threshold=5)
    assert triggered is False
    assert risk.emergency_stop is False


def test_triggers_when_at_threshold():
    """5건 연속 REJECTED + threshold=5 → 트리거."""
    Session = _session()
    with Session() as db:
        for _ in range(5):
            db.add(_audit_row("REJECTED"))
        db.commit()
        risk = RiskManager(RiskPolicy())
        triggered = maybe_trigger_auto_stop(db, risk=risk, threshold=5)
    assert triggered is True
    assert risk.emergency_stop is True


def test_creates_emergency_stop_event_with_repeated_failure_reason():
    """153 reason_code='repeated_order_failure' 사용 검증."""
    Session = _session()
    with Session() as db:
        for _ in range(5):
            db.add(_audit_row("REJECTED"))
        db.commit()
        risk = RiskManager(RiskPolicy())
        maybe_trigger_auto_stop(db, risk=risk, threshold=5)

        events = db.execute(select(EmergencyStopEvent)).scalars().all()
    assert len(events) == 1
    assert events[0].enabled is True
    assert events[0].reason_code == "repeated_order_failure"
    assert events[0].decided_by == "system"
    assert "5 consecutive REJECTED" in events[0].note


def test_no_op_when_emergency_already_on():
    """이미 emergency_stop=True면 중복 trigger 방지."""
    Session = _session()
    with Session() as db:
        for _ in range(5):
            db.add(_audit_row("REJECTED"))
        db.commit()
        risk = RiskManager(RiskPolicy())
        risk.set_emergency_stop(True)  # 이미 ON
        triggered = maybe_trigger_auto_stop(db, risk=risk, threshold=5)
    assert triggered is False
    # Event 미작성.
    with Session() as db2:
        events = db2.execute(select(EmergencyStopEvent)).scalars().all()
    assert events == []


def test_route_order_triggers_auto_stop_after_n_rejections(client):
    """182 핵심 통합: route_order가 REJECTED 누적 후 자동 stop."""
    risk = client.test_risk_manager
    risk.policy.auto_stop_consecutive_rejections = 3

    # max_order_notional 작게 — 모든 주문 REJECTED.
    risk.policy.max_order_notional = 100  # 100원 한도, 75K 주문 → 거부

    # 3건 연속 시도.
    for _ in range(3):
        res = client.post("/api/broker/orders", json={
            "symbol": "005930", "side": "BUY", "quantity": 1,
        })
        assert res.status_code == 400  # REJECTED

    # 3번째 직후 자동 emergency_stop 발동.
    assert risk.emergency_stop is True

    # EmergencyStopEvent도 작성됐어야.
    with client.test_db_factory() as db:
        events = db.execute(select(EmergencyStopEvent)).scalars().all()
    assert len(events) == 1
    assert events[0].reason_code == "repeated_order_failure"