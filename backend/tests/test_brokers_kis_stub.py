import asyncio

import httpx
import pytest

from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide
from app.brokers.kis import KisBrokerAdapter
from app.brokers.kis_client import KisClient


def run(coro):
    return asyncio.run(coro)


def _stub_kis_client(
    price: str = "75000",
    balance_response: dict | None = None,
) -> KisClient:
    """Build a KisClient backed by httpx MockTransport returning fixed responses."""
    default_balance = {
        "output1": [
            {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "75100.0",  "prpr": "75500"},
            {"pdno": "000660", "hldg_qty":  "5", "pchs_avg_pric": "182000.0", "prpr": "180500"},
        ],
        "output2": [{"dnca_tot_amt": "5234800", "tot_evlu_amt": "10000000"}],
    }
    response = balance_response if balance_response is not None else default_balance

    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/quotations/inquire-price"):
            return httpx.Response(200, json={"output": {"stck_prpr": price}})
        if request.url.path.endswith("/inquire-balance"):
            return httpx.Response(200, json=response)
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


def test_get_balance_returns_cash_and_equity():
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801",
                         client=_stub_kis_client())
    bal = run(a.get_balance())
    assert bal.cash == 5_234_800
    assert bal.equity == 10_000_000
    assert bal.buying_power == 5_234_800
    assert bal.currency == "KRW"


def test_get_balance_raises_when_account_no_too_short():
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="123",
                         client=_stub_kis_client())
    with pytest.raises(RuntimeError, match="at least 10 chars"):
        run(a.get_balance())


def test_get_positions_maps_kis_fields_and_filters_zero_qty():
    response = {
        "output1": [
            {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "75100.0", "prpr": "75500"},
            {"pdno": "000660", "hldg_qty":  "0", "pchs_avg_pric":     "0.0", "prpr":     "0"},
            {"pdno": "035420", "hldg_qty":  "3", "pchs_avg_pric": "194000.5", "prpr": "197000"},
        ],
        "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "5000000"}],
    }
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801",
                         client=_stub_kis_client(balance_response=response))
    positions = run(a.get_positions())
    assert len(positions) == 2
    assert positions[0].symbol == "005930"
    assert positions[0].quantity == 10
    assert positions[0].avg_price == 75_100
    assert positions[0].market_price == 75_500
    assert positions[1].symbol == "035420"
    assert positions[1].avg_price == 194_000  # truncated from 194000.5
    assert positions[1].quantity == 3


def test_get_positions_empty_when_no_holdings():
    response = {"output1": [], "output2": [{"dnca_tot_amt": "0", "tot_evlu_amt": "0"}]}
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801",
                         client=_stub_kis_client(balance_response=response))
    assert run(a.get_positions()) == []


def test_account_no_split_uses_last_two_chars_as_product_code():
    a = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567899",
                         client=_stub_kis_client())
    assert a._split_account() == ("12345678", "99")


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
