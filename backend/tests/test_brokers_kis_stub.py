import asyncio

import httpx
import pytest

from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide
from app.brokers.kis import KisBrokerAdapter
from app.brokers.kis_client import KisClient


def run(coro):
    return asyncio.run(coro)


def _stub_kis_client(price: str = "75000") -> KisClient:
    """Build a KisClient backed by an httpx MockTransport returning a fixed quote."""
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/quotations/inquire-price"):
            return httpx.Response(200, json={"output": {"stck_prpr": price}})
        return httpx.Response(404)
    return KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler))


def test_implements_broker_adapter_protocol():
    assert issubclass(KisBrokerAdapter, BrokerAdapter)


def test_constructor_reads_settings_credentials_when_unset():
    a = KisBrokerAdapter()
    # In test env, settings defaults are empty strings + is_paper=True
    assert a.app_key == ""
    assert a.app_secret == ""
    assert a.account_no == ""
    assert a.is_paper is True
    assert a.has_credentials() is False


def test_constructor_explicit_overrides():
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="acc", is_paper=False)
    assert a.app_key == "k"
    assert a.app_secret == "s"
    assert a.account_no == "acc"
    assert a.is_paper is False
    assert a.has_credentials() is True


def test_partial_credentials_does_not_count_as_complete():
    a = KisBrokerAdapter(app_key="k", app_secret="", account_no="acc")
    assert a.has_credentials() is False


def test_get_price_returns_quote_from_kis_response():
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="acc",
                         client=_stub_kis_client(price="75000"))
    quote = run(a.get_price("005930"))
    assert quote.symbol == "005930"
    assert quote.price  == 75_000
    assert quote.source == "kis"


def test_get_price_raises_when_no_credentials():
    a = KisBrokerAdapter()
    with pytest.raises(RuntimeError, match="not configured"):
        run(a.get_price("005930"))


def test_get_balance_still_stub():
    with pytest.raises(NotImplementedError, match="follow-up"):
        run(KisBrokerAdapter().get_balance())


def test_get_positions_still_stub():
    with pytest.raises(NotImplementedError, match="follow-up"):
        run(KisBrokerAdapter().get_positions())


def test_place_order_explicitly_disabled_in_shadow():
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)
    with pytest.raises(NotImplementedError, match="intentionally disabled"):
        run(KisBrokerAdapter().place_order(order))


def test_cancel_order_still_stub():
    with pytest.raises(NotImplementedError, match="follow-up"):
        run(KisBrokerAdapter().cancel_order("any-id"))


def test_get_order_status_still_stub():
    with pytest.raises(NotImplementedError, match="follow-up"):
        run(KisBrokerAdapter().get_order_status("any-id"))
