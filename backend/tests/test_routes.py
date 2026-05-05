import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_mock_broker, get_risk_manager
from app.brokers.mock_broker import MockBrokerAdapter
from app.main import app
from app.risk.risk_manager import RiskManager, RiskPolicy


@pytest.fixture
def client():
    broker = MockBrokerAdapter()
    risk = RiskManager(RiskPolicy())
    app.dependency_overrides[get_mock_broker] = lambda: broker
    app.dependency_overrides[get_risk_manager] = lambda: risk
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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


def test_mock_broker_price_and_balance(client):
    price = client.get("/api/broker/mock/price/005930").json()
    assert price["symbol"] == "005930"
    assert price["price"] == 75_000
    balance = client.get("/api/broker/mock/balance").json()
    assert balance["cash"] == 10_000_000
    positions = client.get("/api/broker/mock/positions").json()
    assert positions == []


def test_mock_broker_order_happy_path(client):
    order = {"symbol": "005930", "side": "BUY", "quantity": 1}
    res = client.post("/api/broker/mock/orders", json=order)
    assert res.status_code == 200
    assert res.json()["status"] == "FILLED"
    positions = client.get("/api/broker/mock/positions").json()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "005930"


def test_mock_broker_order_rejected_by_risk(client):
    order = {"symbol": "005930", "side": "BUY", "quantity": 50}
    res = client.post("/api/broker/mock/orders", json=order)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["decision"] == "REJECTED"
    assert any("max_order_notional" in r for r in detail["reasons"])


def test_ai_analyze_is_placeholder_without_execute_permission(client):
    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 200
    body = res.json()
    assert body["can_execute_order"] is False
    assert "005930" in body["text"]
