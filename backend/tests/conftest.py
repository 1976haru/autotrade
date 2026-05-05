import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.client import AiClient, AiResponse
from app.api.deps import get_ai_client, get_broker, get_market_data, get_risk_manager
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.market.mock import MockMarketData
from app.risk.risk_manager import RiskManager, RiskPolicy


class _FakeAiClient(AiClient):
    """기본 픽스처용. analyze()가 더미 응답을 반환한다."""

    def __init__(self):
        self.api_key = "test-key"
        self.model = "fake-model"

    async def analyze(self, *, system: str, prompt: str, max_tokens: int = 1024) -> AiResponse:
        return AiResponse(
            text='{"tech":50,"trend":50,"news":50,"flow":50,"total":50,'
                 '"signal":"관망","conf":50,"entry":0,"target":0,"stop":0}\n'
                 '기본 fake AI 응답입니다.',
            model="fake-model",
            input_tokens=10,
            output_tokens=20,
        )


@pytest.fixture
def client():
    broker = MockBrokerAdapter()
    risk = RiskManager(RiskPolicy())
    market = MockMarketData()
    ai_client = _FakeAiClient()

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_broker] = lambda: broker
    app.dependency_overrides[get_risk_manager] = lambda: risk
    app.dependency_overrides[get_market_data] = lambda: market
    app.dependency_overrides[get_ai_client] = lambda: ai_client
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.test_db_factory = TestSession
        c.test_risk_manager = risk
        yield c
    app.dependency_overrides.clear()
