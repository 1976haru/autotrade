"""pytest 공통 fixture.

★테스트 격리(hermetic) — KIS-PAPER-EOD-SAFETY-HARDENING-V2 (2026-10-23)
조사 결과 기존 suite 가 운영자의 *실제* 데이터에 쓰고 있었다:
  - backend/.env 의 DATABASE_URL(=운영 SQLite)을 그대로 사용 → TestClient(app)/SessionLocal
    테스트가 운영 DB 에 행을 commit (agent_decision_episode 오염 확인).
  - backend/.env 값(MARKET_DATA_PROVIDER=kis 등)이 default 검증 테스트를 깨뜨림.
  - %APPDATA%/Autotrade 의 runtime_overrides.json / kis_token_cache.json /
    config/paper_capital_state.json 을 읽거나 덮어씀.
아래 블록은 *어떤 app 모듈 import 보다 먼저* 실행되어(app.db.session 이 import 시점에
engine 을 만든다) 임시 DB·임시 설정 디렉토리·.env 미로딩·안전 플래그를 강제한다.
APPDATA 자체는 바꾸지 않는다(사용자 site-packages 의 pytest/httpx/ruff 가 거기 있음 —
subprocess 테스트가 깨진다). 대신 해당 경로 함수를 session fixture 로 패치한다.
"""
import os
import tempfile
from pathlib import Path

_HERMETIC_DIR = Path(tempfile.mkdtemp(prefix="autotrade-pytest-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_HERMETIC_DIR / "test.db").as_posix()
os.environ["AGENT_TRADER_CONFIG_DIR"] = str(_HERMETIC_DIR / "config")
os.environ.update({
    "DEFAULT_MODE": "SIMULATION", "MARKET_DATA_PROVIDER": "mock",
    "USE_REAL_TICK_RUNNER": "false", "PAPER_CAPITAL_PERSIST": "false",
    "ENABLE_LIVE_TRADING": "false", "ENABLE_AI_EXECUTION": "false",
    "ENABLE_FUTURES_LIVE_TRADING": "false", "KIS_IS_PAPER": "true",
})

from app.core import config as _hermetic_cfg  # noqa: E402

_hermetic_cfg.Settings.model_config["env_file"] = None   # backend/.env 를 절대 읽지 않음
_hermetic_cfg.get_settings.cache_clear()

import pytest  # noqa: E402
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


# ── hermetic: 임시 DB 스키마 + %APPDATA%/Autotrade 상태 파일 격리 ──────────────


def _hermetic_create_schema() -> None:
    """임시 DB 스키마를 *실제 alembic 마이그레이션* 으로 생성 — 앱 lifespan 의
    upgrade head 와 충돌하지 않고, 신규 마이그레이션(0027 등)도 함께 검증된다."""
    import logging

    from app.db.session import apply_migrations, engine
    assert str(engine.url).endswith("test.db"), f"test DB 격리 실패: {engine.url}"
    # root 에 handler 가 없으면 alembic env.py 가 fileConfig(alembic.ini) 를 적용해
    #   *기존 logger 를 비활성화* 한다(caplog 테스트 붕괴). 운영 lifespan 과 같은 조건
    #   (root handler 존재)으로 맞춰 fileConfig 를 건너뛰게 한다.
    _root = logging.getLogger()
    _guard = logging.NullHandler()
    _root.addHandler(_guard)
    try:
        apply_migrations()
    finally:
        _root.removeHandler(_guard)


_hermetic_create_schema()


@pytest.fixture(autouse=True, scope="session")
def _isolate_appdata_state():
    """runtime_overrides / KIS 토큰 캐시를 임시 경로로 — 운영 %APPDATA% 미접촉."""
    d = _HERMETIC_DIR / "appdata" / "Autotrade"
    d.mkdir(parents=True, exist_ok=True)
    mp = pytest.MonkeyPatch()
    mp.setattr("app.core.runtime_config.overrides_path",
               lambda: d / "runtime_overrides.json")
    mp.setattr("app.core.runtime_config._legacy_overrides_path",
               lambda: _HERMETIC_DIR / "legacy_runtime_overrides.json")
    mp.setattr("app.brokers.kis_client._token_cache_path",
               lambda: d / "kis_token_cache.json")
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _restore_os_environ():
    """테스트가 os.environ 을 바꾸고(예: desktop launcher 가 실제 .env 를 주입) 원복하지
    않으면 이후 테스트가 운영자 설정값을 보게 된다(순서 의존 실패). 매 테스트 후 원복."""
    saved = dict(os.environ)
    yield
    if os.environ != saved:
        os.environ.clear()
        os.environ.update(saved)
        from app.core.config import get_settings as _gs
        _gs.cache_clear()
