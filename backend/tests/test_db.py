from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import OrderAuditLog


def _make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def test_create_and_query_audit_row():
    Session = _make_session()
    with Session() as db:
        row = OrderAuditLog(
            mode="SIMULATION",
            symbol="005930",
            side="BUY",
            quantity=1,
            order_type="MARKET",
            latest_price=75_000,
            decision="APPROVED",
            reasons=[],
        )
        db.add(row)
        db.commit()

    with Session() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1
        loaded = rows[0]
        assert loaded.mode == "SIMULATION"
        assert loaded.symbol == "005930"
        assert loaded.decision == "APPROVED"
        assert loaded.executed is False
        assert loaded.created_at is not None


def test_reasons_round_trips_as_json_list():
    Session = _make_session()
    reasons = ["order notional exceeds max_order_notional", "live trading disabled"]
    with Session() as db:
        db.add(OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=10,
            order_type="MARKET", latest_price=200_000,
            decision="REJECTED", reasons=reasons,
        ))
        db.commit()

    with Session() as db:
        loaded = db.execute(select(OrderAuditLog)).scalar_one()
        assert loaded.reasons == reasons


def test_executed_fields_can_be_updated():
    Session = _make_session()
    with Session() as db:
        row = OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=75_000,
            decision="APPROVED", reasons=[],
        )
        db.add(row)
        db.commit()
        row.executed = True
        row.broker_order_id = "abc-123"
        row.broker_status = "FILLED"
        row.filled_quantity = 1
        row.avg_fill_price = 75_000
        row.message = "mock filled"
        db.commit()

    with Session() as db:
        loaded = db.execute(select(OrderAuditLog)).scalar_one()
        assert loaded.executed is True
        assert loaded.broker_order_id == "abc-123"
        assert loaded.broker_status == "FILLED"
        assert loaded.filled_quantity == 1
        assert loaded.avg_fill_price == 75_000
