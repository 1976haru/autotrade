"""194: tests for /api/futures/orders + /api/futures/orders/summary."""

from datetime import datetime, timedelta, timezone

from app.db.models import FuturesOrderAuditLog


def _seed(client, *, n=1, decision="APPROVED", contract="KOSPI200_F",
          forced=False, executed=True, margin_delta=0, leverage=5.0):
    base = datetime.now(timezone.utc) - timedelta(seconds=n)
    with client.test_db_factory() as db:
        for i in range(n):
            db.add(FuturesOrderAuditLog(
                mode="SIMULATION", contract=contract, side="BUY",
                quantity=1, order_type="MARKET", leverage=leverage,
                decision=decision, reasons=[],
                executed=executed, filled_quantity=1 if executed else 0,
                broker_status="FILLED" if executed else None,
                margin_delta=margin_delta,
                forced_liquidation=forced, message="",
                created_at=base + timedelta(seconds=i),
            ))
        db.commit()


# ---------- /api/futures/orders ----------

def test_list_futures_orders_empty(client):
    res = client.get("/api/futures/orders")
    assert res.status_code == 200
    assert res.json() == []


def test_list_futures_orders_returns_recent_first(client):
    _seed(client, n=3)
    rows = client.get("/api/futures/orders").json()
    assert [r["id"] for r in rows] == sorted([r["id"] for r in rows], reverse=True)


def test_list_filters_by_contract(client):
    _seed(client, n=2, contract="KOSPI200_F")
    _seed(client, n=1, contract="MINI_F")
    rows = client.get("/api/futures/orders?contract=MINI_F").json()
    assert len(rows) == 1
    assert rows[0]["contract"] == "MINI_F"


def test_list_filters_by_decision(client):
    _seed(client, n=2, decision="APPROVED")
    _seed(client, n=3, decision="REJECTED")
    rows = client.get("/api/futures/orders?decision=REJECTED").json()
    assert len(rows) == 3
    assert all(r["decision"] == "REJECTED" for r in rows)


def test_list_filters_by_forced_liquidation(client):
    _seed(client, n=2, forced=False)
    _seed(client, n=1, forced=True)
    rows = client.get("/api/futures/orders?forced=true").json()
    assert len(rows) == 1
    assert rows[0]["forced_liquidation"] is True

    rows = client.get("/api/futures/orders?forced=false").json()
    assert len(rows) == 2
    assert all(r["forced_liquidation"] is False for r in rows)


def test_list_validates_limit(client):
    assert client.get("/api/futures/orders?limit=0").status_code   == 422
    assert client.get("/api/futures/orders?limit=999").status_code == 422


# ---------- /api/futures/orders/summary ----------

def test_summary_empty(client):
    body = client.get("/api/futures/orders/summary").json()
    assert body["total"]                    == 0
    assert body["by_decision"]              == {}
    assert body["forced_liquidation_count"] == 0
    assert body["executed_count"]           == 0
    assert body["cumulative_margin_delta"]  == 0


def test_summary_aggregates_all_fields(client):
    _seed(client, n=3, decision="APPROVED", executed=True,  margin_delta=1000)
    _seed(client, n=2, decision="REJECTED", executed=False, margin_delta=0)
    _seed(client, n=1, decision="APPROVED", executed=True,
           margin_delta=-500, forced=True)
    body = client.get("/api/futures/orders/summary").json()
    assert body["total"]                    == 6
    assert body["by_decision"]["APPROVED"]  == 4
    assert body["by_decision"]["REJECTED"]  == 2
    assert body["forced_liquidation_count"] == 1
    assert body["executed_count"]           == 4
    assert body["cumulative_margin_delta"]  == 3 * 1000 + 0 + (-500)
