from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.brokers.base import (
    Balance,
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
    Quote,
)


class MockBrokerAdapter(BrokerAdapter):
    """실제 주문 없이 백엔드/프론트/리스크 흐름을 검증하기 위한 브로커."""

    def __init__(self, initial_cash: int = 10_000_000) -> None:
        self.cash = initial_cash
        self.orders: dict[str, OrderResult] = {}
        self.positions: dict[str, Position] = {}
        self.prices: dict[str, int] = {
            "005930": 75000,
            "000660": 185000,
            "035420": 205000,
            "035720": 61000,
            "005380": 245000,
        }
        # 143: 테스트용 stale price 시뮬레이터 — 특정 symbol에 대해 get_price가
        # N초 전의 timestamp를 가진 Quote를 반환하도록 강제. 운영에서는 항상 비어
        # 있다. value > 0만 적용된다.
        self.stale_age_overrides: dict[str, float] = {}

    def set_price(self, symbol: str, price: int) -> None:
        self.prices[symbol] = price
        if symbol in self.positions:
            pos = self.positions[symbol]
            self.positions[symbol] = pos.model_copy(update={"market_price": price})

    def set_stale_price_for_test(self, symbol: str, age_seconds: float) -> None:
        """143 testing aid: subsequent get_price(symbol) responses carry a
        timestamp that is `age_seconds` in the past. RiskManager의 stale 검사
        활성화 검증용 — 운영 코드에서는 호출되지 않아야 한다."""
        self.stale_age_overrides[symbol] = age_seconds

    async def get_price(self, symbol: str) -> Quote:
        price = self.prices.get(symbol, 50_000)
        now = datetime.now(timezone.utc)
        age = self.stale_age_overrides.get(symbol, 0.0)
        ts  = now - timedelta(seconds=age) if age > 0 else now
        return Quote(
            symbol=symbol,
            price=price,
            timestamp=ts.isoformat(),
            source="mock",
        )

    async def get_balance(self) -> Balance:
        market_value = sum(pos.quantity * pos.market_price for pos in self.positions.values())
        equity = self.cash + market_value
        return Balance(cash=self.cash, equity=equity, buying_power=self.cash)

    async def get_positions(self) -> list[Position]:
        return list(self.positions.values())

    async def place_order(self, order: OrderRequest) -> OrderResult:
        quote = await self.get_price(order.symbol)
        fill_price = order.limit_price if order.limit_price is not None else quote.price
        notional = fill_price * order.quantity
        order_id = str(uuid4())

        if order.side == OrderSide.BUY:
            if self.cash < notional:
                result = OrderResult(
                    order_id=order_id,
                    status=OrderStatus.REJECTED,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    message="insufficient cash",
                )
                self.orders[order_id] = result
                return result
            self.cash -= notional
            prev = self.positions.get(order.symbol)
            if prev:
                new_qty = prev.quantity + order.quantity
                new_avg = int(((prev.avg_price * prev.quantity) + notional) / new_qty)
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    quantity=new_qty,
                    avg_price=new_avg,
                    market_price=quote.price,
                )
            else:
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    avg_price=fill_price,
                    market_price=quote.price,
                )
        else:
            prev = self.positions.get(order.symbol)
            if not prev or prev.quantity < order.quantity:
                result = OrderResult(
                    order_id=order_id,
                    status=OrderStatus.REJECTED,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    message="insufficient position",
                )
                self.orders[order_id] = result
                return result
            self.cash += notional
            remain = prev.quantity - order.quantity
            if remain == 0:
                del self.positions[order.symbol]
            else:
                self.positions[order.symbol] = prev.model_copy(update={"quantity": remain})

        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.FILLED,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.quantity,
            avg_fill_price=fill_price,
            message="mock filled",
        )
        self.orders[order_id] = result
        return result

    async def cancel_order(self, order_id: str) -> OrderResult:
        result = self.orders.get(order_id)
        if not result:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                symbol="UNKNOWN",
                side=OrderSide.BUY,
                quantity=0,
                message="order not found",
            )
        updated = result.model_copy(update={"status": OrderStatus.CANCELED, "message": "mock canceled"})
        self.orders[order_id] = updated
        return updated

    async def get_order_status(self, order_id: str) -> OrderResult:
        result = self.orders.get(order_id)
        if not result:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                symbol="UNKNOWN",
                side=OrderSide.BUY,
                quantity=0,
                message="order not found",
            )
        return result
