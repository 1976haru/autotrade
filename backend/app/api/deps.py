from functools import lru_cache

from app.brokers.mock_broker import MockBrokerAdapter
from app.market.base import MarketDataAdapter
from app.market.mock import MockMarketData
from app.risk.risk_manager import RiskManager, RiskPolicy


@lru_cache
def get_mock_broker() -> MockBrokerAdapter:
    return MockBrokerAdapter()


@lru_cache
def get_risk_manager() -> RiskManager:
    return RiskManager(RiskPolicy())


@lru_cache
def get_market_data() -> MarketDataAdapter:
    return MockMarketData()
