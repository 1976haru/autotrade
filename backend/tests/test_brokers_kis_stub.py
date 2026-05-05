import asyncio

import pytest

from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide
from app.brokers.kis import KisBrokerAdapter


def run(coro):
    return asyncio.run(coro)


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


def test_get_price_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="stub"):
        run(KisBrokerAdapter().get_price("005930"))


def test_get_balance_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="stub"):
        run(KisBrokerAdapter().get_balance())


def test_get_positions_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="stub"):
        run(KisBrokerAdapter().get_positions())


def test_place_order_raises_with_explicit_safety_message():
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)
    with pytest.raises(NotImplementedError, match="intentionally not implemented"):
        run(KisBrokerAdapter().place_order(order))


def test_cancel_order_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="stub"):
        run(KisBrokerAdapter().cancel_order("any-id"))


def test_get_order_status_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="stub"):
        run(KisBrokerAdapter().get_order_status("any-id"))
