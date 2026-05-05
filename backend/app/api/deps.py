from functools import lru_cache

from app.ai.client import AiClient
from app.brokers.base import BrokerAdapter
from app.brokers.kis import KisBrokerAdapter
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.market.base import MarketDataAdapter
from app.market.mock import MockMarketData
from app.risk.risk_manager import RiskManager, RiskPolicy


@lru_cache
def get_mock_broker() -> MockBrokerAdapter:
    return MockBrokerAdapter()


@lru_cache
def _get_kis_broker() -> KisBrokerAdapter:
    return KisBrokerAdapter()


def get_broker() -> BrokerAdapter:
    """Returns the broker for the current operation mode.

    LIVE_SHADOW → KIS (read-only quotes / balance / positions). RiskManager
    rejects every order in this mode, so KisBrokerAdapter.place_order is
    unreachable through the order route.

    PAPER → KIS in is_paper=True mode (KIS 모의투자). Orders are real KIS
    API calls but execute against the KIS paper account with virtual money.
    Defense in depth: this factory refuses PAPER unless KIS_IS_PAPER=true,
    and KisBrokerAdapter.place_order also re-checks is_paper.

    All other modes → Mock. Real LIVE order routing for
    LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST lands in follow-up PRs.
    """
    settings = get_settings()
    mode = settings.default_mode

    if mode == OperationMode.PAPER:
        if not settings.kis_is_paper:
            raise RuntimeError(
                "DEFAULT_MODE=PAPER requires KIS_IS_PAPER=true to avoid "
                "routing paper-mode orders to the KIS live server. "
                "Set KIS_IS_PAPER=true or change DEFAULT_MODE."
            )
        return _get_kis_broker()

    if mode == OperationMode.LIVE_SHADOW:
        return _get_kis_broker()

    return get_mock_broker()


@lru_cache
def get_risk_manager() -> RiskManager:
    return RiskManager(RiskPolicy())


@lru_cache
def get_market_data() -> MarketDataAdapter:
    if get_settings().market_data_provider == "yfinance":
        from app.market.yfinance_adapter import YfinanceMarketData
        return YfinanceMarketData()
    return MockMarketData()


@lru_cache
def get_ai_client() -> AiClient:
    return AiClient()
