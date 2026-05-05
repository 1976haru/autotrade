import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_mock_broker, get_risk_manager
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.risk.risk_manager import RiskManager, RiskPolicy


@pytest.fixture
def client():
    broker = MockBrokerAdapter()
    risk = RiskManager(RiskPolicy())

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

    app.dependency_overrides[get_mock_broker] = lambda: broker
    app.dependency_overrides[get_risk_manager] = lambda: risk
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.test_db_factory = TestSession
        yield c
    app.dependency_overrides.clear()
