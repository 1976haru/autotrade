from app.futures.base import FuturesBrokerAdapter
from app.futures.types import (
    FuturesBalance,
    FuturesOrderRequest,
    FuturesOrderResult,
    FuturesPosition,
    FuturesQuote,
)


_STUB_MESSAGE = (
    "Futures broker adapter is a stub. Real futures order routing requires "
    "FuturesRiskManager + PermissionGate validation in a follow-up PR. "
    "ENABLE_FUTURES_LIVE_TRADING stays False by default (CLAUDE.md)."
)


class MockFuturesBroker(FuturesBrokerAdapter):
    """선물 broker 어댑터 — 모든 메서드가 의도적으로 NotImplementedError.

    선물 모듈 구조를 미리 잡되 실제 주문 경로를 절대 노출하지 않기 위한
    안전 장치다. 실제 KIS/한투 선물 API 연동은 별도 PR에서 SHADOW 모드부터
    추가한다.
    """

    async def get_quote(self, contract_code: str) -> FuturesQuote:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_balance(self) -> FuturesBalance:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_positions(self) -> list[FuturesPosition]:
        raise NotImplementedError(_STUB_MESSAGE)

    async def place_order(self, order: FuturesOrderRequest) -> FuturesOrderResult:
        raise NotImplementedError(
            "Futures place_order is intentionally not implemented. Live "
            "futures orders require ENABLE_FUTURES_LIVE_TRADING plus "
            "FuturesRiskManager + PermissionGate validation, in a follow-up PR."
        )

    async def cancel_order(self, order_id: str) -> FuturesOrderResult:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_order_status(self, order_id: str) -> FuturesOrderResult:
        raise NotImplementedError(_STUB_MESSAGE)
