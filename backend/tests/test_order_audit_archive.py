"""OrderAudit archival tests (168, MUST)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit.archive import mark_orders_older_than_archived
from app.db.base import Base
from app.db.models import OrderAuditLog


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(db, *, created_at=None):
    row = OrderAuditLog(
        mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
        order_type="MARKET", latest_price=100,
        decision="APPROVED", reasons=[],
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


# ---------- mark_orders_older_than_archived ----------

def test_zero_days_is_noop():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _audit(db, created_at=now - timedelta(days=999))
        db.commit()
        affected = mark_orders_older_than_archived(db, days=0, now=now)
        assert affected == 0
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert all(not r.archived for r in rows)


def test_marks_only_old_rows():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        old    = _audit(db, created_at=now - timedelta(days=200))
        recent = _audit(db, created_at=now - timedelta(days=30))
        db.commit()
        affected = mark_orders_older_than_archived(db, days=180, now=now)
    assert affected == 1
    with Session() as db2:
        old_r    = db2.get(OrderAuditLog, old.id)
        recent_r = db2.get(OrderAuditLog, recent.id)
        assert old_r.archived    is True
        assert recent_r.archived is False


def test_dry_run_returns_count_without_mutation():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(3):
            _audit(db, created_at=now - timedelta(days=200))
        db.commit()
        count = mark_orders_older_than_archived(db, days=180, now=now, dry_run=True)
    assert count == 3
    with Session() as db2:
        rows = db2.execute(select(OrderAuditLog)).scalars().all()
        # 모두 archived=False 그대로.
        assert all(not r.archived for r in rows)


def test_already_archived_rows_not_double_processed():
    """이미 archived=True인 row는 다시 카운트되지 않음 — idempotent."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        old = _audit(db, created_at=now - timedelta(days=200))
        old.archived = True
        db.commit()
        affected = mark_orders_older_than_archived(db, days=180, now=now)
    assert affected == 0


def test_default_now_uses_current_utc():
    """now 미지정 시 datetime.now(timezone.utc) 사용 — 실제 운영 호출."""
    Session = _session()
    with Session() as db:
        # 365일 전 row.
        _audit(db, created_at=datetime.now(timezone.utc) - timedelta(days=365))
        db.commit()
        affected = mark_orders_older_than_archived(db, days=180)
    assert affected == 1


def test_negative_days_treated_as_zero():
    Session = _session()
    with Session() as db:
        _audit(db, created_at=datetime.now(timezone.utc) - timedelta(days=999))
        db.commit()
        affected = mark_orders_older_than_archived(db, days=-1)
    assert affected == 0


# ---------- /api/audit/orders integration ----------

def test_list_orders_excludes_archived_by_default(client):
    """기본 hot 응답에 archived row는 빠진다."""
    with client.test_db_factory() as db:
        # 2 hot + 2 cold.
        for _ in range(2):
            db.add(OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="APPROVED", reasons=[], archived=False,
            ))
        for _ in range(2):
            db.add(OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="APPROVED", reasons=[], archived=True,
            ))
        db.commit()

    rows = client.get("/api/audit/orders").json()
    assert len(rows) == 2  # hot only


def test_list_orders_includes_archived_when_explicit(client):
    """?include_archived=true → 모두 반환."""
    with client.test_db_factory() as db:
        for archived in (False, True, False, True):
            db.add(OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="APPROVED", reasons=[], archived=archived,
            ))
        db.commit()

    rows = client.get("/api/audit/orders?include_archived=true").json()
    assert len(rows) == 4


def test_archive_then_query_hot_excludes(client):
    """archive 함수 호출 후 hot query는 archived row를 더 이상 안 본다."""
    with client.test_db_factory() as db:
        old = OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=100,
            decision="APPROVED", reasons=[],
            created_at=datetime.now(timezone.utc) - timedelta(days=200),
        )
        recent = OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=100,
            decision="APPROVED", reasons=[],
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        db.add_all([old, recent])
        db.commit()

        # archive 호출 — old만 marked.
        affected = mark_orders_older_than_archived(db, days=180)
    assert affected == 1
    # hot query — old 빠짐.
    rows = client.get("/api/audit/orders").json()
    assert len(rows) == 1
