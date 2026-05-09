"""#43: LIVE_SHADOW shadow trade integration tests.

Coverage:
- LIVE_SHADOW route_order writes ShadowTrade with actual_broker_order_sent=False
- broker.place_order is never invoked in LIVE_SHADOW (AsyncMock spy)
- would_have_decision differentiates pure-shadow-reject vs other-rule-reject
  (emergency stop case)
- /api/shadow/trades and /api/shadow/summary endpoints
- Non-LIVE_SHADOW modes (SIMULATION) do NOT write ShadowTrade rows

Defense in depth — these tests lock the invariant that LIVE_SHADOW is *signal-only*:
no broker.place_order, no NEEDS_APPROVAL queueing, only audit + shadow_trade rows.
"""

from unittest.mock import AsyncMock

from sqlalchemy import select

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, ShadowTrade


def _set_mode(monkeypatch, mode: OperationMode) -> None:
    monkeypatch.setattr(get_settings(), "default_mode", mode)


# ====================================================================
# 1. ShadowTrade row written + broker.place_order not called
# ====================================================================

def test_live_shadow_writes_shadow_trade_row(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    spy = AsyncMock()
    client.test_broker.place_order = spy

    res = client.post(
        "/api/broker/orders",
        json={"symbol": "005930", "side": "BUY", "quantity": 1},
    )
    # Audit row stays REJECTED — contract preserved.
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["decision"] == "REJECTED"
    assert any("LIVE_SHADOW" in r for r in detail["reasons"])

    # broker.place_order is the *only* path to a real order — must be untouched.
    spy.assert_not_awaited()

    # ShadowTrade row exists with the invariant.
    with client.test_db_factory() as db:
        rows = db.execute(select(ShadowTrade)).scalars().all()
        assert len(rows) == 1
        shadow = rows[0]
        assert shadow.actual_broker_order_sent is False
        assert shadow.mode == "LIVE_SHADOW"
        assert shadow.symbol == "005930"
        assert shadow.side == "BUY"
        assert shadow.quantity == 1
        assert shadow.would_have_decision == "APPROVED"  # only shadow reason → would-have-passed
        assert shadow.would_have_reasons == []
        assert shadow.estimation_method == "latest_price_proxy"
        assert shadow.estimated_slippage_bps == 0.0
        assert shadow.estimated_fill_price == shadow.latest_price
        assert shadow.audit_id is not None
        # Cross-reference: audit row exists with same id and is REJECTED.
        audit = db.execute(
            select(OrderAuditLog).where(OrderAuditLog.id == shadow.audit_id)
        ).scalar_one()
        assert audit.decision == "REJECTED"
        assert audit.mode == "LIVE_SHADOW"


# ====================================================================
# 2. would_have_decision differentiates other-rule-reject
# ====================================================================

def test_live_shadow_records_emergency_stop_as_would_have_rejected(client, monkeypatch):
    """LIVE_SHADOW + emergency stop ON → ShadowTrade.would_have_decision=REJECTED
    with the emergency-stop reason. The shadow reason is excluded from
    would_have_reasons (it's the LIVE_SHADOW gate, not a real rule)."""
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    spy = AsyncMock()
    client.test_broker.place_order = spy

    # Turn on emergency stop — this is a real risk rule that would have
    # rejected the order in any mode.
    client.test_risk_manager.emergency_stop = True

    res = client.post(
        "/api/broker/orders",
        json={"symbol": "005930", "side": "BUY", "quantity": 1},
    )
    assert res.status_code == 400
    spy.assert_not_awaited()

    with client.test_db_factory() as db:
        shadow = db.execute(select(ShadowTrade)).scalar_one()
        assert shadow.would_have_decision == "REJECTED"
        # The shadow reason itself is NOT in would_have_reasons — only real rules.
        assert all("LIVE_SHADOW" not in r for r in shadow.would_have_reasons)
        assert any("emergency" in r.lower() for r in shadow.would_have_reasons)


# ====================================================================
# 3. Non-LIVE_SHADOW modes do NOT write ShadowTrade rows
# ====================================================================

def test_simulation_mode_does_not_write_shadow_trade(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.SIMULATION)
    res = client.post(
        "/api/broker/orders",
        json={"symbol": "005930", "side": "BUY", "quantity": 1},
    )
    assert res.status_code == 200  # SIMULATION + MockBroker → APPROVED + filled

    with client.test_db_factory() as db:
        shadow_count = db.execute(select(ShadowTrade)).scalars().all()
        assert shadow_count == [], (
            "Non-LIVE_SHADOW modes must not write to shadow_trade — "
            "this row would dilute the LIVE_SHADOW analytics surface."
        )


def test_simulation_rejection_does_not_write_shadow_trade(client, monkeypatch):
    """Even a REJECTED order in SIMULATION (e.g. emergency stop) must not
    write a ShadowTrade — the table is exclusively for LIVE_SHADOW mode."""
    _set_mode(monkeypatch, OperationMode.SIMULATION)
    client.test_risk_manager.emergency_stop = True

    res = client.post(
        "/api/broker/orders",
        json={"symbol": "005930", "side": "BUY", "quantity": 1},
    )
    assert res.status_code == 400

    with client.test_db_factory() as db:
        rows = db.execute(select(ShadowTrade)).scalars().all()
        assert rows == []


# ====================================================================
# 4. /api/shadow/trades + /api/shadow/summary endpoints
# ====================================================================

def test_api_shadow_trades_returns_recorded_rows(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    # Two orders in LIVE_SHADOW.
    for sym in ("005930", "000660"):
        client.post(
            "/api/broker/orders",
            json={"symbol": sym, "side": "BUY", "quantity": 1},
        )

    res = client.get("/api/shadow/trades")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2
    # Newest first.
    assert rows[0]["symbol"] == "000660"
    assert rows[1]["symbol"] == "005930"
    for row in rows:
        assert row["actual_broker_order_sent"] is False
        assert row["mode"] == "LIVE_SHADOW"
        assert row["estimation_method"] == "latest_price_proxy"
        assert row["confidence_note"]  # non-empty disclaimer string
        assert "audit_id" in row


def test_api_shadow_trades_filters_by_symbol(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    client.post("/api/broker/orders", json={"symbol": "005930", "side": "BUY", "quantity": 1})
    client.post("/api/broker/orders", json={"symbol": "000660", "side": "BUY", "quantity": 1})

    res = client.get("/api/shadow/trades?symbol=000660")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "000660"


def test_api_shadow_trades_filters_by_would_have_decision(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    # First order → would_have_decision=APPROVED (no other reasons).
    client.post("/api/broker/orders", json={"symbol": "005930", "side": "BUY", "quantity": 1})
    # Toggle emergency stop → next order is would_have_decision=REJECTED.
    client.test_risk_manager.emergency_stop = True
    client.post("/api/broker/orders", json={"symbol": "000660", "side": "BUY", "quantity": 1})

    res = client.get("/api/shadow/trades?would_have_decision=APPROVED")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "005930"

    res = client.get("/api/shadow/trades?would_have_decision=REJECTED")
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "000660"


def test_api_shadow_summary_aggregates_counts(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    # Two would-have-APPROVED, one would-have-REJECTED via emergency stop.
    client.post("/api/broker/orders", json={"symbol": "005930", "side": "BUY", "quantity": 1})
    client.post("/api/broker/orders", json={"symbol": "000660", "side": "BUY", "quantity": 1})
    client.test_risk_manager.emergency_stop = True
    client.post("/api/broker/orders", json={"symbol": "035720", "side": "BUY", "quantity": 1})

    res = client.get("/api/shadow/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert body["would_have_approved_count"] == 2
    assert body["would_have_rejected_count"] == 1
    assert body["actual_broker_orders_sent"] == 0
    assert body["avg_estimated_slippage_bps"] == 0.0
    assert "invariant" in body["invariant_note"].lower() or "실제 주문" in body["invariant_note"]


def test_api_shadow_summary_empty_state(client):
    res = client.get("/api/shadow/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert body["would_have_approved_count"] == 0
    assert body["would_have_rejected_count"] == 0
    assert body["actual_broker_orders_sent"] == 0


# ====================================================================
# 5. Static guard: routes_shadow does not import broker
# ====================================================================

def test_routes_shadow_does_not_import_broker():
    """절대 원칙 5/7 — Shadow read-only surface는 broker 인스턴스에 의존하지 않는다.
    DB SELECT만으로 동작해야 한다."""
    import app.api.routes_shadow as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "broker.place_order(",
        "BrokerAdapter.place_order(",
        "import app.brokers",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"routes_shadow.py must not contain '{snippet}' — "
            "shadow surface is DB-only (CLAUDE.md absolute principle 5/7)."
        )
