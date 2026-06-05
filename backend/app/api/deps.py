from functools import lru_cache

from app.ai.client import AiClient
from app.brokers.base import BrokerAdapter
from app.brokers.kis import KisBrokerAdapter
from app.brokers.kis_client import KisClient
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.core.rate_limiter import get_kis_rate_limiter
from app.market.base import MarketDataAdapter
from app.market.mock import MockMarketData
from app.risk.risk_manager import RiskManager, RiskPolicy


@lru_cache
def get_mock_broker() -> MockBrokerAdapter:
    return MockBrokerAdapter()


@lru_cache
def _get_kis_broker() -> KisBrokerAdapter:
    settings = get_settings()
    if not settings.kis_app_key or not settings.kis_app_secret:
        # No credentials yet — return a bare adapter; the first real call
        # raises a clear error. Operator must set KIS_APP_KEY / KIS_APP_SECRET.
        return KisBrokerAdapter()
    # 계좌 단위 공유 limiter — 모든 KisClient 가 동일 인스턴스를 써야 aggregate
    # 호출이 KIS 한도 이하로 묶인다(per-instance limiter 는 합산 시 초과 → EGW00201).
    client = KisClient(
        settings.kis_app_key,
        settings.kis_app_secret,
        is_paper=settings.kis_is_paper,
        rate_limiter=get_kis_rate_limiter(),
    )
    return KisBrokerAdapter(client=client)


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
    return RiskManager(RiskPolicy.from_settings(get_settings()))


@lru_cache
def get_market_data() -> MarketDataAdapter:
    if get_settings().market_data_provider == "yfinance":
        from app.market.yfinance_adapter import YfinanceMarketData
        return YfinanceMarketData()
    return MockMarketData()


@lru_cache
def get_ai_client() -> AiClient:
    return AiClient()
