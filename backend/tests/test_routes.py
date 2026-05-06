from sqlalchemy import select

from app.db.models import EmergencyStopEvent, OrderAuditLog


def test_root_returns_app_metadata(client):
    res = client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["docs"] == "/docs"


def test_status_exposes_safety_flags(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enable_live_trading"] is False
    assert body["enable_ai_execution"] is False
    assert body["default_mode"] == "SIMULATION"
    assert "mode_capabilities" in body


# 201: full safety_flags matrix — every env flag from CLAUDE.md table.
def test_status_safety_flags_block_present(client):
    body = client.get("/api/status").json()
    flags = body.get("safety_flags")
    assert isinstance(flags, dict)
    for key in (
        "default_mode",
        "enable_live_trading",
        "enable_ai_execution",
        "enable_futures_live_trading",
        "kis_is_paper",
        "market_data_provider",
        "enable_fill_polling",
        "stale_price_max_age_seconds",
    ):
        assert key in flags, f"missing {key}"
    # 기본값 검증 — CLAUDE.md "안전 플래그" 표와 lockstep.
    assert flags["enable_live_trading"]         is False
    assert flags["enable_ai_execution"]         is False
    assert flags["enable_futures_live_trading"] is False
    assert flags["kis_is_paper"]                is True
    assert flags["market_data_provider"]        == "mock"
    assert flags["enable_fill_polling"]         is False
    assert flags["stale_price_max_age_seconds"] == 60


def test_risk_policy_returns_defaults(client):
    res = client.get("/api/risk/policy")
    assert res.status_code == 200
    body = res.json()
    assert body["enable_live_trading"] is False
    assert body["enable_ai_execution"] is False
    assert body["max_order_notional"] == 1_000_000


def test_emergency_stop_toggles_flag(client):
    res = client.post("/api/risk/emergency-stop", json={"enabled": True})
    assert res.status_code == 200
    assert res.json() == {"emergency_stop": True}
    res = client.post("/api/risk/emergency-stop", json={"enabled": False})
    assert res.json() == {"emergency_stop": False}


# ---------- emergency-stop audit trail ----------

def test_emergency_stop_logs_event_with_metadata(client):
    res = client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops1", "note": "vol spike",
    })
    assert res.status_code == 200
    with client.test_db_factory() as db:
        ev = db.execute(select(EmergencyStopEvent)).scalar_one()
        assert ev.enabled    is True
        assert ev.decided_by == "ops1"
        assert ev.note       == "vol spike"


def test_emergency_stop_skips_log_on_no_op_toggle(client):
    """Re-asserting current state (e.g. enabled=False when already off)
    should not pollute the audit trail with duplicates."""
    # Default state is OFF — this re-asserts OFF and should be a no-op.
    client.post("/api/risk/emergency-stop", json={"enabled": False})
    with client.test_db_factory() as db:
        rows = db.execute(select(EmergencyStopEvent)).scalars().all()
        assert rows == []


def test_emergency_stop_logs_only_state_changes(client):
    client.post("/api/risk/emergency-stop", json={"enabled": True})   # change
    client.post("/api/risk/emergency-stop", json={"enabled": True})   # no-op
    client.post("/api/risk/emergency-stop", json={"enabled": False})  # change
    client.post("/api/risk/emergency-stop", json={"enabled": False})  # no-op
    with client.test_db_factory() as db:
        rows = db.execute(select(EmergencyStopEvent).order_by(EmergencyStopEvent.id)).scalars().all()
        assert [r.enabled for r in rows] == [True, False]


def test_emergency_stop_history_returns_most_recent_first(client):
    client.post("/api/risk/emergency-stop", json={"enabled": True,  "note": "first"})
    client.post("/api/risk/emergency-stop", json={"enabled": False, "note": "second"})
    res = client.get("/api/risk/emergency-stop/history")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 2
    # Most recent first by id desc
    assert body[0]["note"]    == "second"
    assert body[0]["enabled"] is False
    assert body[1]["note"]    == "first"
    assert body[1]["enabled"] is True


def test_emergency_stop_history_empty_initially(client):
    res = client.get("/api/risk/emergency-stop/history")
    assert res.status_code == 200
    assert res.json() == []


def test_emergency_stop_history_limit_caps_results(client):
    for i in range(5):
        # Force a state change every call to write a row
        client.post("/api/risk/emergency-stop", json={"enabled": i % 2 == 0})
    res = client.get("/api/risk/emergency-stop/history?limit=3")
    assert res.status_code == 200
    assert len(res.json()) == 3


def test_mock_broker_price_and_balance(client):
    price = client.get("/api/broker/price/005930").json()
    assert price["symbol"] == "005930"
    assert price["price"] == 75_000
    balance = client.get("/api/broker/balance").json()
    assert balance["cash"] == 10_000_000
    positions = client.get("/api/broker/positions").json()
    assert positions == []


def test_mock_broker_order_happy_path(client):
    order = {"symbol": "005930", "side": "BUY", "quantity": 1}
    res = client.post("/api/broker/orders", json=order)
    assert res.status_code == 200
    assert res.json()["status"] == "FILLED"
    positions = client.get("/api/broker/positions").json()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "005930"

    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1
        audit = rows[0]
        assert audit.decision == "APPROVED"
        assert audit.executed is True
        assert audit.broker_status == "FILLED"
        assert audit.filled_quantity == 1
        assert audit.avg_fill_price == 75_000


def test_mock_broker_order_rejected_by_risk(client):
    order = {"symbol": "005930", "side": "BUY", "quantity": 50}
    res = client.post("/api/broker/orders", json=order)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["decision"] == "REJECTED"
    assert any("max_order_notional" in r for r in detail["reasons"])

    with client.test_db_factory() as db:
        audit = db.execute(select(OrderAuditLog)).scalar_one()
        assert audit.decision == "REJECTED"
        assert audit.executed is False
        assert audit.broker_order_id is None
        assert any("max_order_notional" in r for r in audit.reasons)


# ---------- 208: emergency-stop summary ----------

def test_emergency_stop_summary_empty(client):
    body = client.get("/api/risk/emergency-stop/summary").json()
    assert body["currently_active"]   is False
    assert body["active_since"]       is None
    assert body["total_toggles"]      == 0
    assert body["total_activations"]  == 0
    assert body["by_reason"]          == {}


def test_emergency_stop_summary_aggregates_by_reason(client):
    """3개 ON (각각 다른 사유) + 2개 OFF → activations=3, by_reason 그룹."""
    # ON with reason
    client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops", "reason_code": "data_stale",
    })
    client.post("/api/risk/emergency-stop", json={"enabled": False, "decided_by": "ops"})
    # ON with same reason
    client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops", "reason_code": "data_stale",
    })
    client.post("/api/risk/emergency-stop", json={"enabled": False, "decided_by": "ops"})
    # ON with different reason
    client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops", "reason_code": "broker_error",
    })

    body = client.get("/api/risk/emergency-stop/summary").json()
    assert body["currently_active"]  is True
    assert body["active_since"]      is not None
    assert body["total_toggles"]     == 5
    assert body["total_activations"] == 3
    assert body["by_reason"]["data_stale"]   == 2
    assert body["by_reason"]["broker_error"] == 1


def test_emergency_stop_summary_handles_missing_reason(client):
    """reason_code 없이 ON → by_reason["(none)"]에 분류."""
    client.post("/api/risk/emergency-stop", json={"enabled": True, "decided_by": "ops"})
    body = client.get("/api/risk/emergency-stop/summary").json()
    assert body["by_reason"].get("(none)") == 1
    assert body["currently_active"] is True


def test_emergency_stop_summary_active_since_clears_when_off(client):
    client.post("/api/risk/emergency-stop", json={"enabled": True, "decided_by": "ops"})
    client.post("/api/risk/emergency-stop", json={"enabled": False, "decided_by": "ops"})
    body = client.get("/api/risk/emergency-stop/summary").json()
    assert body["currently_active"] is False
    assert body["active_since"]     is None
