from abc import ABC, abstractmethod
from enum import StrEnum
from pydantic import BaseModel, Field


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    RECEIVED = "RECEIVED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class Quote(BaseModel):
    symbol: str
    price: int
    timestamp: str
    source: str = "mock"


class Position(BaseModel):
    symbol: str
    quantity: int
    avg_price: int
    market_price: int


class OrderRequest(BaseModel):
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: int | None = Field(default=None, ge=0)
    client_order_id: str | None = None
    # 134: 진입/청산 사유. 자유 문자열 — 'strategy_signal', 'stop_loss',
    # 'manual', 'ai_recommendation' 등. None이면 audit row에서 NULL로 surface
    # 되어 운영자가 '미명시 주문'을 식별할 수 있다.
    trade_reason: str | None = None


class OrderResult(BaseModel):
    order_id: str
    status: OrderStatus
    symbol: str
    side: OrderSide
    quantity: int
    filled_quantity: int = 0
    avg_fill_price: int | None = None
    message: str = ""


class Balance(BaseModel):
    cash: int
    equity: int
    buying_power: int
    currency: str = "KRW"


class BrokerAdapter(ABC):
    @abstractmethod
    async def get_price(self, symbol: str) -> Quote:
        raise NotImplementedError

    @abstractmethod
    async def get_balance(self) -> Balance:
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError
