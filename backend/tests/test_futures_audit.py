"""Futures order audit log tests (169, MUST)."""

import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import FuturesOrderAuditLog
from app.futures.mock import MockFuturesBroker
from app.futures.types import (
    FuturesOrderRequest,
    FuturesOrderType,
    FuturesSide,
)


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _order(side="BUY", qty=1, contract="KOSPI200_2503", order_type="MARKET",
           limit_price=None):
    return FuturesOrderRequest(
        contract=contract, side=FuturesSide(side), quantity=qty,
        order_type=FuturesOrderType(order_type), limit_price=limit_price,
    )


# ---------- backwards compat: db=None ----------

def test_db_none_no_audit_writes():
    """db 미주입 시 in-memory만 — audit 테이블 변동 없음 (기존 테스트와 동일)."""
    Session = _session()
    broker = MockFuturesBroker(initial_cash=20_000_000)
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(_order("BUY", 1)))
    # broker는 db 모르므로 어떤 DB에도 row 안 남음.
    with Session() as db:
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert rows == []


# ---------- audit writes when db is injected ----------

def test_open_long_records_audit():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("BUY", 1)))
        db.commit()
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert r.contract == "KOSPI200_2503"
    assert r.side     == "BUY"
    assert r.executed is True
    assert r.broker_status == "FILLED"
    assert r.leverage == 5.0
    assert r.liquidation_price is not None
    assert r.forced_liquidation is False


def test_open_short_records_audit():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("SELL", 1)))
        db.commit()
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert len(rows) == 1
    assert rows[0].side == "SELL"
    assert rows[0].executed is True


def test_close_position_records_audit():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("BUY", 1)))
        broker.set_mark_price("KOSPI200_2503", 1100)
        asyncio.run(broker.place_order(_order("SELL", 1)))
        db.commit()
        rows = db.execute(
            select(FuturesOrderAuditLog).order_by(FuturesOrderAuditLog.id)
        ).scalars().all()
    assert len(rows) == 2
    # 첫 행 = open, 둘째 = close.
    assert rows[0].message == "virtual_open"
    assert rows[1].message == "virtual_close"
    assert rows[1].forced_liquidation is False


def test_force_liquidate_records_forced_audit():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("BUY", 1)))
        pos = broker.positions["KOSPI200_2503"]
        broker.set_mark_price("KOSPI200_2503", pos.liquidation_price)
        result = broker.force_liquidate_if_needed("KOSPI200_2503")
        assert result is not None
        db.commit()
        rows = db.execute(
            select(FuturesOrderAuditLog).order_by(FuturesOrderAuditLog.id)
        ).scalars().all()
    assert len(rows) == 2
    forced_row = rows[1]
    assert forced_row.forced_liquidation is True
    assert "forced_liquidation" in forced_row.reasons
    assert forced_row.message == "virtual_force_liquidate"


def test_insufficient_cash_recorded_as_rejected():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=100, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        asyncio.run(broker.place_order(_order("BUY", 1)))
        db.commit()
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert len(rows) == 1
    assert rows[0].decision        == "REJECTED"
    assert rows[0].executed        is False
    assert rows[0].broker_status   == "REJECTED"
    assert "insufficient_cash" in rows[0].reasons


def test_limit_not_crossed_recorded_as_rejected():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        # LIMIT BUY at 800 — mark 1000이라 not crossed.
        order = _order("BUY", 1, order_type="LIMIT", limit_price=800)
        asyncio.run(broker.place_order(order))
        db.commit()
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert len(rows) == 1
    assert rows[0].executed is False
    assert "limit_not_crossed" in rows[0].reasons


def test_partial_close_records_audit():
    """5주 진입 후 2주만 청산 — 둘 다 audit row."""
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("BUY", 5)))
        asyncio.run(broker.place_order(_order("SELL", 2)))
        db.commit()
        rows = db.execute(
            select(FuturesOrderAuditLog).order_by(FuturesOrderAuditLog.id)
        ).scalars().all()
    assert len(rows) == 2
    assert rows[0].quantity == 5
    assert rows[1].quantity == 2


def test_audit_mode_default_virtual_futures():
    """기본 audit_mode='VIRTUAL_FUTURES' — 운영자가 명시적으로 다른 모드
    (LIVE_FUTURES_SHADOW 등) 주입 가능."""
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(initial_cash=20_000_000, db=db)
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("BUY", 1)))
        db.commit()
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert rows[0].mode == "VIRTUAL_FUTURES"


def test_custom_audit_mode_propagates():
    Session = _session()
    with Session() as db:
        broker = MockFuturesBroker(
            initial_cash=20_000_000, db=db, audit_mode="LIVE_FUTURES_SHADOW",
        )
        broker.set_mark_price("KOSPI200_2503", 1000)
        broker.set_leverage(5.0)
        asyncio.run(broker.place_order(_order("BUY", 1)))
        db.commit()
        rows = db.execute(select(FuturesOrderAuditLog)).scalars().all()
    assert rows[0].mode == "LIVE_FUTURES_SHADOW"
