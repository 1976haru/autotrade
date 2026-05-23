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


@pytest.fixture
def safe_default_flags(monkeypatch):
    """안전 flag 를 *코드 default* 로 강제 + get_settings 캐시 clear.

    운영자가 로컬 backend/.env 에서 KIS Paper Auto / background tick 을 켜 두면
    (완료 조건대로) `Settings()` 가 그 값을 읽어 *default 검증* 테스트가 로컬에서
    실패한다. 본 fixture 는 os.environ 으로 default 값을 주입(.env 보다 우선)해
    테스트를 hermetic 하게 만든다. CI(.env 없음)에서는 동일하게 default 적용.

    실거래 flag 는 *항상* false/true(KIS_IS_PAPER) 안전값으로 고정 — 본 fixture
    가 실거래를 켜는 일은 결코 없다.
    """
    from app.core.config import get_settings
    defaults = {
        "ENABLE_LIVE_TRADING": "false",
        "ENABLE_AI_EXECUTION": "false",
        "KIS_IS_PAPER": "true",
        "ENABLE_AI_PAPER_BACKGROUND_TICK": "false",
        "AI_PAPER_TICK_DRY_RUN": "true",
        "AI_PAPER_TICK_MAX_PER_DAY": "0",
        "AI_PAPER_TICK_INTERVAL_SECONDS": "30",
        "AI_PAPER_ALLOW_SIMULATED_FILLS": "false",
        "ENABLE_KIS_PAPER_AUTO_TRADING": "false",
        "KIS_PAPER_AUTO_ORDER_DRY_RUN": "true",
        "KIS_PAPER_FILL_POLLING": "false",
    }
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
        # 143: tests that need to inject stale prices (or otherwise mutate the
        # broker fixture) reach for it via this attribute.
        c.test_broker = broker
        yield c
    app.dependency_overrides.clear()
