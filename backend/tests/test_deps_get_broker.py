"""Tests for the get_broker() factory that switches by OperationMode."""

import httpx
import pytest

from app.api.deps import (
    _get_kis_broker,
    get_broker,
    get_mock_broker,
)
from app.brokers.kis import KisBrokerAdapter
from app.brokers.kis_client import KisClient
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.config import get_settings
from app.core.modes import OperationMode


def _set_mode(monkeypatch, mode: OperationMode) -> None:
    monkeypatch.setattr(get_settings(), "default_mode", mode)


def test_simulation_mode_returns_mock(monkeypatch):
    _set_mode(monkeypatch, OperationMode.SIMULATION)
    assert isinstance(get_broker(), MockBrokerAdapter)


def test_paper_mode_returns_kis_when_kis_is_paper_true(monkeypatch):
    _set_mode(monkeypatch, OperationMode.PAPER)
    monkeypatch.setattr(get_settings(), "kis_is_paper", True)
    broker = get_broker()
    assert isinstance(broker, KisBrokerAdapter)


def test_paper_mode_refuses_to_start_when_kis_is_paper_false(monkeypatch):
    _set_mode(monkeypatch, OperationMode.PAPER)
    monkeypatch.setattr(get_settings(), "kis_is_paper", False)
    with pytest.raises(RuntimeError, match="KIS_IS_PAPER=true"):
        get_broker()


def test_live_manual_approval_returns_mock(monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_MANUAL_APPROVAL)
    assert isinstance(get_broker(), MockBrokerAdapter)


def test_live_ai_execution_returns_mock(monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_EXECUTION)
    assert isinstance(get_broker(), MockBrokerAdapter)


def test_live_ai_assist_returns_mock(monkeypatch):
    """Defense-in-depth: LIVE_AI_ASSIST routes to MockBroker today. The
    LIVE_MANUAL_APPROVAL routing PR will likely flip this to KIS for both
    LIVE_MANUAL_APPROVAL and LIVE_AI_ASSIST; until then this test locks in
    the current safety net."""
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    assert isinstance(get_broker(), MockBrokerAdapter)


def test_live_shadow_returns_kis(monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)
    try:
        broker = get_broker()
        assert isinstance(broker, KisBrokerAdapter)
    finally:
        # The KIS adapter is lru_cached; clear so other tests get a fresh state.
        _get_kis_broker.cache_clear()


def test_mock_broker_singleton(monkeypatch):
    """get_mock_broker is lru_cached; multiple calls return the same instance."""
    _set_mode(monkeypatch, OperationMode.SIMULATION)
    a = get_broker()
    b = get_broker()
    assert a is b
    assert a is get_mock_broker()


def _make_kis_with_stub(price: str = "76500") -> KisBrokerAdapter:
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/quotations/inquire-price"):
            return httpx.Response(200, json={"output": {"stck_prpr": price}})
        if request.url.path.endswith("/inquire-balance"):
            return httpx.Response(200, json={"output1": [], "output2": [{"dnca_tot_amt": "0", "tot_evlu_amt": "0"}]})
        return httpx.Response(404)
    return KisBrokerAdapter(
        app_key="k", app_secret="s", account_no="1234567801",
        client=KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler)),
    )


def test_route_uses_kis_in_shadow_mode(client, monkeypatch):
    """Override get_broker with a stubbed-out KIS adapter and confirm
    routes_broker.get_price routes through it."""
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)

    from app.main import app
    app.dependency_overrides[get_broker] = lambda: _make_kis_with_stub(price="76500")

    res = client.get("/api/broker/price/005930")
    assert res.status_code == 200
    body = res.json()
    assert body["price"] == 76500
    assert body["source"] == "kis"


def test_shadow_mode_order_is_rejected_before_reaching_broker(client, monkeypatch):
    """Defense in depth: even if a KIS broker is wired in shadow mode,
    RiskManager rejects the order before place_order is called."""
    _set_mode(monkeypatch, OperationMode.LIVE_SHADOW)

    from app.main import app
    # Use a real KIS adapter (no stub client) — if RiskManager fails to
    # block, place_order would raise NotImplementedError, not silently pass.
    app.dependency_overrides[get_broker] = lambda: _make_kis_with_stub()

    res = client.post(
        "/api/broker/orders",
        json={"symbol": "005930", "side": "BUY", "quantity": 1},
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["decision"] == "REJECTED"
    assert any("LIVE_SHADOW" in r for r in detail["reasons"])


@pytest.fixture(autouse=True)
def _clear_kis_singleton_after_test():
    yield
    _get_kis_broker.cache_clear()
