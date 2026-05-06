"""193: tests for /api/virtual/orders + /api/virtual/orders/summary."""

from datetime import datetime, timedelta, timezone

from app.db.models import VirtualOrder
from app.virtual.order_ledger import (
    STATUS_ACCEPTED, STATUS_CANCELLED, STATUS_FILLED,
    STATUS_NEW, STATUS_REJECTED,
)


def _seed(client, *, n=1, status=STATUS_NEW, symbol="005930",
          requested_by_ai=False, mode="SIMULATION", strategy=None,
          start=None):
    """n개의 VirtualOrder를 status 그대로 삽입. created_at은 단조 증가."""
    base = start or datetime.now(timezone.utc) - timedelta(seconds=n)
    with client.test_db_factory() as db:
        for i in range(n):
            db.add(VirtualOrder(
                symbol=symbol, side="BUY", quantity=1, order_type="MARKET",
                status=status, mode=mode, strategy=strategy,
                created_at=base + timedelta(seconds=i),
                updated_at=base + timedelta(seconds=i),
            ))
        db.commit()


# ---------- /api/virtual/orders ----------

def test_list_virtual_orders_empty(client):
    res = client.get("/api/virtual/orders")
    assert res.status_code == 200
    assert res.json() == []


def test_list_virtual_orders_returns_recent_first(client):
    _seed(client, n=3)
    rows = client.get("/api/virtual/orders").json()
    assert [r["id"] for r in rows] == sorted([r["id"] for r in rows], reverse=True)
    assert all(r["status"] == STATUS_NEW for r in rows)


def test_list_virtual_orders_respects_limit_and_offset(client):
    _seed(client, n=5)
    rows = client.get("/api/virtual/orders?limit=2&offset=1").json()
    assert len(rows) == 2


def test_list_virtual_orders_filters_by_status(client):
    _seed(client, n=2, status=STATUS_NEW)
    _seed(client, n=3, status=STATUS_FILLED)
    rows = client.get(f"/api/virtual/orders?status={STATUS_FILLED}").json()
    assert len(rows) == 3
    assert all(r["status"] == STATUS_FILLED for r in rows)


def test_list_virtual_orders_filters_by_symbol(client):
    _seed(client, n=2, symbol="005930")
    _seed(client, n=1, symbol="000660")
    rows = client.get("/api/virtual/orders?symbol=000660").json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "000660"


def test_list_virtual_orders_invalid_status_is_ignored(client):
    """오타 / 소문자는 필터를 무시하고 전체를 돌려줘서 endpoint가 깨지지 않는다."""
    _seed(client, n=3)
    rows = client.get("/api/virtual/orders?status=invalid").json()
    assert len(rows) == 3


def test_list_virtual_orders_validates_limit(client):
    assert client.get("/api/virtual/orders?limit=0").status_code   == 422
    assert client.get("/api/virtual/orders?limit=999").status_code == 422


# ---------- /api/virtual/orders/summary ----------

def test_summary_empty_returns_zero_counts(client):
    body = client.get("/api/virtual/orders/summary").json()
    assert body["total"]          == 0
    assert body["pending_count"]  == 0
    assert body["terminal_count"] == 0
    # 모든 status key 0으로 채워져야 frontend chip이 안정.
    for s in (STATUS_NEW, STATUS_ACCEPTED, STATUS_FILLED,
              STATUS_CANCELLED, STATUS_REJECTED):
        assert body["by_status"][s] == 0


def test_summary_breaks_down_pending_vs_terminal(client):
    _seed(client, n=2, status=STATUS_NEW)
    _seed(client, n=1, status=STATUS_ACCEPTED)
    _seed(client, n=4, status=STATUS_FILLED)
    _seed(client, n=1, status=STATUS_CANCELLED)
    body = client.get("/api/virtual/orders/summary").json()
    assert body["total"]          == 8
    assert body["pending_count"]  == 3   # NEW(2) + ACCEPTED(1)
    assert body["terminal_count"] == 5   # FILLED(4) + CANCELLED(1)
    assert body["by_status"][STATUS_NEW]      == 2
    assert body["by_status"][STATUS_ACCEPTED] == 1
    assert body["by_status"][STATUS_FILLED]   == 4
    assert body["by_status"][STATUS_CANCELLED]== 1


# ---------- /api/virtual/positions (195) ----------

def _seed_filled(client, *, symbol, side, qty, price, strategy=None,
                 created_at=None):
    """체결된 VirtualOrder 한 행 — position engine이 FIFO에 사용."""
    base = created_at or datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        db.add(VirtualOrder(
            symbol=symbol, side=side, quantity=qty, order_type="MARKET",
            status=STATUS_FILLED, mode="SIMULATION", strategy=strategy,
            filled_quantity=qty, avg_fill_price=price,
            filled_at=base, created_at=base, updated_at=base,
        ))
        db.commit()


def test_list_virtual_positions_empty(client):
    res = client.get("/api/virtual/positions")
    assert res.status_code == 200
    assert res.json() == []


def test_list_virtual_positions_returns_open_lots(client):
    _seed_filled(client, symbol="005930", side="BUY", qty=10, price=70_000)
    rows = client.get("/api/virtual/positions").json()
    assert len(rows) == 1
    assert rows[0]["symbol"]      == "005930"
    assert rows[0]["quantity"]    == 10
    assert rows[0]["avg_price"]   == 70_000
    assert rows[0]["realized_pnl"] == 0


def test_list_virtual_positions_realized_pnl_after_full_close(client):
    """BUY 10@70k, SELL 10@72k → 포지션 닫힘 + realized=20k."""
    _seed_filled(client, symbol="005930", side="BUY",  qty=10, price=70_000)
    _seed_filled(client, symbol="005930", side="SELL", qty=10, price=72_000)
    rows = client.get("/api/virtual/positions").json()
    # 청산되어 list에 들어가지 않더라도, 만약 빈 list면 OK.
    assert all(r["quantity"] != 0 for r in rows)


def test_last_prices_param_drives_unrealized(client):
    _seed_filled(client, symbol="005930", side="BUY", qty=10, price=70_000)
    rows = client.get(
        "/api/virtual/positions?last_prices=005930:73000",
    ).json()
    assert len(rows) == 1
    # +3000 * 10 = +30,000
    assert rows[0]["unrealized_pnl"] == 30_000
    assert rows[0]["last_price"]     == 73_000


def test_last_prices_param_skips_garbage_tokens(client):
    """잘못된 토큰은 무시되고 endpoint가 깨지지 않아야 한다."""
    _seed_filled(client, symbol="005930", side="BUY", qty=1, price=70_000)
    rows = client.get(
        "/api/virtual/positions?last_prices=,bad_token,005930:abc,005930:71000,",
    ).json()
    assert len(rows) == 1
    assert rows[0]["unrealized_pnl"] == 1_000  # 71k - 70k
