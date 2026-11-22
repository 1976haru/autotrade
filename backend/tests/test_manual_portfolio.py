"""수동 포트폴리오 — compute_manual_holdings / compute_period_realized_pnl / API."""
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, OrderAuditLog
from app.portfolio.manual_portfolio import (
    compute_manual_holdings,
    compute_period_realized_pnl,
    resolve_period_dates,
)


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    yield s
    s.close()
    engine.dispose()


def _order(db, symbol: str, reason: str, qty: int, price: int, at: datetime | None = None):
    o = OrderAuditLog(
        symbol=symbol,
        side=("BUY" if reason == "manual_buy" else "SELL"),
        quantity=qty,
        order_type="MARKET",
        latest_price=price,
        decision="APPROVED",
        message="",
        mode="PAPER",
        trade_reason=reason,
        executed=True,
        filled_quantity=qty,
        avg_fill_price=price,
        created_at=at or datetime.now(timezone.utc),
    )
    db.add(o)
    db.commit()
    return o


class _Pos:
    def __init__(self, symbol: str, market_price: int):
        self.symbol = symbol
        self.market_price = market_price


# ── compute_manual_holdings ───────────────────────────────────────────────────

def test_empty(db):
    assert compute_manual_holdings(db, []) == []


def test_single_buy(db):
    _order(db, "005930", "manual_buy", 10, 70_000)
    hs = compute_manual_holdings(db, [_Pos("005930", 75_000)])
    assert len(hs) == 1
    h = hs[0]
    assert h.symbol == "005930"
    assert h.quantity == 10
    assert h.avg_price == 70_000
    assert h.current_price == 75_000
    assert h.unrealized_pnl == 50_000         # (75-70)*10k
    assert h.unrealized_pnl_pct == pytest.approx(7.14, abs=0.1)
    assert h.name == "삼성전자"                # TOP402 이름 조회 확인


def test_partial_sell_fifo(db):
    _order(db, "005930", "manual_buy",  10, 70_000)
    _order(db, "005930", "manual_sell",  3, 80_000)
    hs = compute_manual_holdings(db, [_Pos("005930", 75_000)])
    assert len(hs) == 1
    assert hs[0].quantity == 7               # 10 - 3


def test_full_sell_clears(db):
    _order(db, "005930", "manual_buy",  10, 70_000)
    _order(db, "005930", "manual_sell", 10, 80_000)
    assert compute_manual_holdings(db, []) == []


def test_multiple_symbols(db):
    _order(db, "005930", "manual_buy", 5, 70_000)
    _order(db, "033780", "manual_buy", 3, 80_000)
    hs = compute_manual_holdings(db, [_Pos("005930", 70_000), _Pos("033780", 80_000)])
    assert len(hs) == 2
    assert {h.symbol for h in hs} == {"005930", "033780"}


def test_no_broker_price_zero(db):
    _order(db, "005930", "manual_buy", 5, 70_000)
    hs = compute_manual_holdings(db, [])
    assert hs[0].current_price == 0
    assert hs[0].unrealized_pnl == -350_000  # 0 - 70k*5


def test_weight_pct_sums_100(db):
    _order(db, "005930", "manual_buy", 5, 70_000)
    _order(db, "033780", "manual_buy", 3, 80_000)
    hs = compute_manual_holdings(db, [_Pos("005930", 70_000), _Pos("033780", 80_000)])
    total_w = sum(h.weight_pct for h in hs)
    assert total_w == pytest.approx(100.0, abs=0.5)


def test_to_dict_has_all_keys(db):
    _order(db, "005930", "manual_buy", 1, 70_000)
    h = compute_manual_holdings(db, [])[0]
    d = h.to_dict()
    for k in ("symbol", "name", "quantity", "avg_price", "current_price",
              "market_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct",
              "first_bought_at", "holding_days", "weight_pct"):
        assert k in d, f"missing key: {k}"


def test_multiple_lots_vwap(db):
    _order(db, "005930", "manual_buy", 10, 70_000)
    _order(db, "005930", "manual_buy",  5, 80_000)   # VWAP = (700k+400k)/15 = 73333
    hs = compute_manual_holdings(db, [_Pos("005930", 75_000)])
    assert hs[0].quantity == 15
    assert hs[0].avg_price == pytest.approx(73_333, abs=1)


# ── compute_period_realized_pnl ───────────────────────────────────────────────

def test_realized_in_period(db):
    today = date(2026, 7, 3)
    buy_t  = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    sell_t = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    _order(db, "005930", "manual_buy",  10, 70_000, buy_t)
    _order(db, "005930", "manual_sell", 10, 80_000, sell_t)
    pnl = compute_period_realized_pnl(db, since=today, until=today)
    assert pnl == 100_000     # (80k-70k)*10


def test_realized_outside_period_excluded(db):
    yesterday = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    _order(db, "005930", "manual_buy",  10, 70_000, yesterday)
    _order(db, "005930", "manual_sell", 10, 80_000, yesterday)
    pnl = compute_period_realized_pnl(db, since=date(2026, 7, 3), until=date(2026, 7, 3))
    assert pnl == 0


def test_partial_sell_in_period(db):
    today = date(2026, 7, 3)
    buy_t  = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    sell_t = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    _order(db, "005930", "manual_buy",  10, 70_000, buy_t)
    _order(db, "005930", "manual_sell",  3, 80_000, sell_t)
    pnl = compute_period_realized_pnl(db, since=today, until=today)
    assert pnl == 30_000      # (80k-70k)*3


# ── resolve_period_dates ──────────────────────────────────────────────────────

def test_period_today():
    today = date(2026, 7, 3)
    s, e = resolve_period_dates("today", today, None, None)
    assert s == e == today


def test_period_1w():
    today = date(2026, 7, 3)
    s, e = resolve_period_dates("1w", today, None, None)
    assert s == date(2026, 6, 26)
    assert e == today


def test_period_1m():
    today = date(2026, 7, 3)
    s, e = resolve_period_dates("1m", today, None, None)
    assert s == date(2026, 6, 3)
    assert e == today


def test_period_custom():
    today = date(2026, 7, 3)
    s, e = resolve_period_dates("custom", today, "2026-06-01", "2026-06-30")
    assert s == date(2026, 6, 1)
    assert e == date(2026, 6, 30)


def test_period_custom_invalid_falls_back(db):
    today = date(2026, 7, 3)
    s, e = resolve_period_dates("custom", today, "not-a-date", None)
    assert s == e == today


# ── API endpoint ──────────────────────────────────────────────────────────────

def _make_client(db, positions=None):
    """TestClient + DI override helper. 호출 후 반드시 .clear()."""
    from app.main import app
    from app.db.session import get_db
    from app.api.deps import get_broker

    mock_broker = AsyncMock()
    mock_broker.get_positions.return_value = positions or []

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_broker] = lambda: mock_broker
    return app, TestClient(app)


def test_api_summary_empty(db):
    app, c = _make_client(db)
    try:
        r = c.get("/api/manual-portfolio/summary")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    d = r.json()
    assert d["holdings"] == []
    assert d["summary"]["position_count"] == 0
    assert d["period"] == "today"
    assert "data_note" in d


def test_api_summary_with_holding(db):
    _order(db, "005930", "manual_buy", 5, 70_000)
    app, c = _make_client(db, [_Pos("005930", 75_000)])
    try:
        r = c.get("/api/manual-portfolio/summary?period=today")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    d = r.json()
    assert len(d["holdings"]) == 1
    assert d["holdings"][0]["symbol"] == "005930"
    assert d["holdings"][0]["quantity"] == 5
    assert d["holdings"][0]["current_price"] == 75_000
    assert d["summary"]["total_unrealized_pnl"] == 25_000


def test_api_period_1w(db):
    app, c = _make_client(db)
    try:
        r = c.get("/api/manual-portfolio/summary?period=1w")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["period"] == "1w"


def test_api_invalid_period(db):
    app, c = _make_client(db)
    try:
        r = c.get("/api/manual-portfolio/summary?period=bad")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422


def test_api_custom_period(db):
    app, c = _make_client(db)
    try:
        r = c.get("/api/manual-portfolio/summary?period=custom&from=2026-06-01&to=2026-06-30")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    d = r.json()
    assert d["period_from"] == "2026-06-01"
    assert d["period_to"] == "2026-06-30"
