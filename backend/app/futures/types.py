from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FuturesSide(StrEnum):
    BUY  = "BUY"
    SELL = "SELL"


class FuturesPositionSide(StrEnum):
    LONG  = "LONG"
    SHORT = "SHORT"


class FuturesOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"


class FuturesOrderStatus(StrEnum):
    RECEIVED         = "RECEIVED"
    FILLED           = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED         = "CANCELED"
    REJECTED         = "REJECTED"


class FuturesContract(BaseModel):
    """A specific futures contract (e.g. KOSPI200 March 2025)."""
    code:       str
    underlying: str
    expiry:     datetime
    multiplier: int


class FuturesQuote(BaseModel):
    contract:  str
    price:     int
    timestamp: str
    source:    str = "stub"


class FuturesPosition(BaseModel):
    contract:     str
    side:         FuturesPositionSide
    quantity:     int
    entry_price:  int
    market_price: int
    margin_used:  int
    # 151: 강제청산 가격. None이면 simulation engine 미주입 (예: 라이브 stub).
    liquidation_price: int | None = None


class FuturesBalance(BaseModel):
    cash:             int
    margin_used:      int
    margin_available: int
    equity:           int
    currency:         str = "KRW"


class FuturesOrderRequest(BaseModel):
    contract:    str
    side:        FuturesSide
    quantity:    int = Field(gt=0)
    order_type:  FuturesOrderType = FuturesOrderType.MARKET
    limit_price: int | None = Field(default=None, ge=0)


class FuturesOrderResult(BaseModel):
    order_id:        str
    status:          FuturesOrderStatus
    contract:        str
    side:            FuturesSide
    quantity:        int
    filled_quantity: int = 0
    avg_fill_price:  int | None = None
    margin_delta:    int = 0
    message:         str = ""
