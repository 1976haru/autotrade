from abc import ABC, abstractmethod

from app.futures.types import (
    FuturesBalance,
    FuturesOrderRequest,
    FuturesOrderResult,
    FuturesPosition,
    FuturesQuote,
)


class FuturesBrokerAdapter(ABC):
    """선물 브로커 어댑터 인터페이스.

    주식 BrokerAdapter와 분리된 별도 ABC로 둔다 — 증거금/만기/레버리지 등
    선물 고유 상태가 함수 시그니처에 반영되어야 하기 때문.
    """

    @abstractmethod
    async def get_quote(self, contract_code: str) -> FuturesQuote:
        raise NotImplementedError

    @abstractmethod
    async def get_balance(self) -> FuturesBalance:
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[FuturesPosition]:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, order: FuturesOrderRequest) -> FuturesOrderResult:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, order_id: str) -> FuturesOrderResult:
        raise NotImplementedError

    @abstractmethod
    async def get_order_status(self, order_id: str) -> FuturesOrderResult:
        raise NotImplementedError
