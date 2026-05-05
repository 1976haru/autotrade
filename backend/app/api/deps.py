from functools import lru_cache

from app.brokers.mock_broker import MockBrokerAdapter
from app.risk.risk_manager import RiskManager, RiskPolicy


@lru_cache
def get_mock_broker() -> MockBrokerAdapter:
    return MockBrokerAdapter()


@lru_cache
def get_risk_manager() -> RiskManager:
    return RiskManager(RiskPolicy())
