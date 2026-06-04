"""STEP 3 (2026-06-03): 거래 일생 테이블 빌더 + API 테스트.

read-only — 새 주문 0건, DB write 0건 (빌더는 SELECT 만). 합성 audit row 사용.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.main import app
from app.reporting.trade_lifecycle import (
    CSV_COLUMNS,
    aggregate_by_strategy,
    build_trade_lifecycle,
    to_csv,
)


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _order(db, *, symbol, side, qty, t, avg_fill=None, latest=10_000,
           strategy="VWAP", reason="VWAP 상회", executed=True, filled=0):
    db.add(OrderAuditLog(
        created_at=t, mode="PAPER", requested_by_ai=False, symbol=symbol, side=side,
        quantity=qty, order_type="MARKET", latest_price=latest, decision="APPROVED",
        reasons=[reason], executed=executed, filled_quantity=filled,
        avg_fill_price=avg_fill, message="", strategy=strategy, trade_reason=reason,
        broker_order_id="X",
    ))
    db.commit()


_T0 = datetime(2026, 6, 2, 0, 10)   # UTC
_FROM = datetime(2026, 6, 1)
_TO = datetime(2026, 6, 3)


def test_buy_then_sell_pairs_into_closed_trade_with_pnl():
    S = _session_factory()
    with S() as db:
        _order(db, symbol="005930", side="BUY", qty=2, t=_T0,
               avg_fill=10_000, filled=2, strategy="VWAP")
        _order(db, symbol="005930", side="SELL", qty=2, t=_T0 + timedelta(minutes=30),
               avg_fill=10_500, filled=2, strategy="VWAP")
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    assert len(rows) == 1
    r = rows[0]
    assert r.status == "CLOSED"
    assert r.buy_price == 10_000 and r.sell_price == 10_500
    assert r.quantity == 2
    assert r.gross_pnl_krw == (10_500 - 10_000) * 2   # 1000
    assert r.gross_pnl_pct == 5.0
    assert r.holding_minutes == 30.0
    assert r.price_basis == "FILL"
    assert r.strategy == "VWAP"
    assert r.is_order_signal is False


def test_timestamps_are_utc_marked_for_correct_kst_display():
    """buy_time/sell_time must carry a UTC tz marker (+00:00) so the frontend
    new Date() converts to KST instead of mis-reading naive UTC as local time."""
    S = _session_factory()
    with S() as db:
        _order(db, symbol="005930", side="BUY", qty=2, t=_T0, avg_fill=10_000, filled=2)
        _order(db, symbol="005930", side="SELL", qty=2, t=_T0 + timedelta(minutes=30),
               avg_fill=10_500, filled=2)
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    r = rows[0]
    assert r.buy_time.endswith("+00:00"), r.buy_time
    assert r.sell_time.endswith("+00:00"), r.sell_time
    # holding time still correct with tz-aware timestamps
    assert r.holding_minutes == 30.0


def test_open_position_when_no_sell():
    S = _session_factory()
    with S() as db:
        _order(db, symbol="000660", side="BUY", qty=1, t=_T0, avg_fill=50_000, filled=1)
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    assert len(rows) == 1
    assert rows[0].status == "OPEN"
    assert rows[0].sell_time is None
    assert rows[0].gross_pnl_krw is None


def test_estimate_basis_when_no_fill_price():
    """체결가 없으면 latest_price proxy + price_basis=ESTIMATE."""
    S = _session_factory()
    with S() as db:
        _order(db, symbol="035420", side="BUY", qty=1, t=_T0, avg_fill=None, latest=200_000)
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    assert rows[0].price_basis == "ESTIMATE"
    assert rows[0].buy_price == 200_000


def test_fifo_partial_pairing():
    S = _session_factory()
    with S() as db:
        _order(db, symbol="005930", side="BUY", qty=2, t=_T0, avg_fill=10_000, filled=2)
        _order(db, symbol="005930", side="BUY", qty=3, t=_T0 + timedelta(minutes=5),
               avg_fill=10_200, filled=3)
        _order(db, symbol="005930", side="SELL", qty=4, t=_T0 + timedelta(minutes=20),
               avg_fill=10_500, filled=4)
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    closed = [r for r in rows if r.status == "CLOSED"]
    opens = [r for r in rows if r.status == "OPEN"]
    # 4 sold: 2 from lot1 (full) + 2 from lot2 (partial); 1 remains open.
    assert sum(r.quantity for r in closed) == 4
    assert sum(r.quantity for r in opens) == 1


def test_sell_without_holding_creates_no_trade():
    S = _session_factory()
    with S() as db:
        _order(db, symbol="068270", side="SELL", qty=1, t=_T0, avg_fill=100_000, filled=1)
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    assert rows == []


def test_aggregate_by_strategy():
    S = _session_factory()
    with S() as db:
        # VWAP: 1 win
        _order(db, symbol="A", side="BUY", qty=1, t=_T0, avg_fill=100, filled=1, strategy="VWAP")
        _order(db, symbol="A", side="SELL", qty=1, t=_T0 + timedelta(minutes=10), avg_fill=110, filled=1, strategy="VWAP")
        # ORB: 1 loss
        _order(db, symbol="B", side="BUY", qty=1, t=_T0, avg_fill=100, filled=1, strategy="ORB")
        _order(db, symbol="B", side="SELL", qty=1, t=_T0 + timedelta(minutes=10), avg_fill=90, filled=1, strategy="ORB")
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    agg = aggregate_by_strategy(rows)
    assert agg["VWAP"]["wins"] == 1 and agg["VWAP"]["win_rate"] == 1.0
    assert agg["ORB"]["wins"] == 0 and agg["ORB"]["win_rate"] == 0.0
    assert agg["ORB"]["total_pnl_krw"] == -10


def test_csv_has_no_sensitive_columns():
    S = _session_factory()
    with S() as db:
        _order(db, symbol="005930", side="BUY", qty=1, t=_T0, avg_fill=10_000, filled=1)
        rows = build_trade_lifecycle(db, date_from=_FROM, date_to=_TO)
    csv_text = to_csv(rows)
    header = csv_text.splitlines()[0].lower()
    for banned in ("account", "secret", "app_key", "token", "계좌"):
        assert banned not in header
    assert "symbol" in header and "gross_pnl_krw" in header
    assert set(CSV_COLUMNS).issuperset({"symbol", "buy_price", "sell_price"})


# ---------- API ----------

def _client():
    S = _session_factory()
    with S() as db:
        _order(db, symbol="005930", side="BUY", qty=2, t=_T0, avg_fill=10_000, filled=2)
        _order(db, symbol="005930", side="SELL", qty=2, t=_T0 + timedelta(minutes=15),
               avg_fill=10_300, filled=2)

    def _override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def test_api_json_returns_trades_and_aggregate():
    client = _client()
    try:
        res = client.get("/api/reporting/trade-lifecycle?date=2026-06-02&mode=PAPER")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]["closed_trades"] == 1
        assert body["is_order_signal"] is False
        assert body["contains_secret"] is False
        assert "VWAP" in body["by_strategy"]
    finally:
        app.dependency_overrides.clear()


def test_api_csv_export():
    client = _client()
    try:
        res = client.get("/api/reporting/trade-lifecycle?date=2026-06-02&format=csv")
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        assert "attachment" in res.headers.get("content-disposition", "")
        assert "symbol" in res.text.splitlines()[0]
    finally:
        app.dependency_overrides.clear()
