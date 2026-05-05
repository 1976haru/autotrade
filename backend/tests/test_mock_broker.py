import asyncio

from app.brokers.base import OrderRequest, OrderSide, OrderStatus
from app.brokers.mock_broker import MockBrokerAdapter


def run(coro):
    return asyncio.run(coro)


def test_get_price_returns_known_symbol():
    broker = MockBrokerAdapter()
    quote = run(broker.get_price("005930"))
    assert quote.symbol == "005930"
    assert quote.price == 75_000
    assert quote.source == "mock"


def test_get_price_falls_back_for_unknown_symbol():
    broker = MockBrokerAdapter()
    quote = run(broker.get_price("999999"))
    assert quote.price == 50_000


def test_initial_balance_and_no_positions():
    broker = MockBrokerAdapter(initial_cash=5_000_000)
    balance = run(broker.get_balance())
    positions = run(broker.get_positions())
    assert balance.cash == 5_000_000
    assert balance.equity == 5_000_000
    assert positions == []


def test_buy_order_fills_and_creates_position():
    broker = MockBrokerAdapter(initial_cash=10_000_000)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=10)
    result = run(broker.place_order(order))
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 10
    assert result.avg_fill_price == 75_000
    positions = run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].quantity == 10
    assert positions[0].avg_price == 75_000
    balance = run(broker.get_balance())
    assert balance.cash == 10_000_000 - 75_000 * 10


def test_buy_rejected_when_cash_insufficient():
    broker = MockBrokerAdapter(initial_cash=10_000)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)
    result = run(broker.place_order(order))
    assert result.status == OrderStatus.REJECTED
    assert "insufficient cash" in result.message


def test_sell_rejected_when_no_position():
    broker = MockBrokerAdapter()
    order = OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=1)
    result = run(broker.place_order(order))
    assert result.status == OrderStatus.REJECTED
    assert "insufficient position" in result.message


def test_sell_reduces_position_and_increases_cash():
    broker = MockBrokerAdapter(initial_cash=10_000_000)
    run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=10)))
    cash_after_buy = (run(broker.get_balance())).cash
    sell = run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=4)))
    assert sell.status == OrderStatus.FILLED
    positions = run(broker.get_positions())
    assert len(positions) == 1
    assert positions[0].quantity == 6
    balance = run(broker.get_balance())
    assert balance.cash == cash_after_buy + 75_000 * 4


def test_full_sell_removes_position():
    broker = MockBrokerAdapter()
    run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=2)))
    run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=2)))
    assert run(broker.get_positions()) == []


def test_buy_averages_existing_position():
    broker = MockBrokerAdapter()
    run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=10)))
    broker.set_price("005930", 85_000)
    run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=10)))
    positions = run(broker.get_positions())
    assert positions[0].quantity == 20
    assert positions[0].avg_price == 80_000


def test_set_price_updates_market_price_on_existing_position():
    broker = MockBrokerAdapter()
    run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)))
    broker.set_price("005930", 90_000)
    positions = run(broker.get_positions())
    assert positions[0].market_price == 90_000


def test_cancel_known_order():
    broker = MockBrokerAdapter()
    placed = run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)))
    canceled = run(broker.cancel_order(placed.order_id))
    assert canceled.status == OrderStatus.CANCELED


def test_cancel_unknown_order_returns_rejected():
    broker = MockBrokerAdapter()
    result = run(broker.cancel_order("does-not-exist"))
    assert result.status == OrderStatus.REJECTED
    assert "not found" in result.message


def test_get_order_status_returns_recorded_result():
    broker = MockBrokerAdapter()
    placed = run(broker.place_order(OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)))
    status = run(broker.get_order_status(placed.order_id))
    assert status.order_id == placed.order_id
    assert status.status == OrderStatus.FILLED
