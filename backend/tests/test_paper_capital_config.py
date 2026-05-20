"""P-01: Paper 시드머니 설정 — 단위 + 정적 invariant + API 통합 테스트.

검증 항목:
1. 기본값 = 10,000,000
2. 허용 옵션: 10,000,000 / 30,000,000 / 50,000,000
3. 허용 옵션 외 값은 거부 (InvalidPaperCapitalError) 또는 fallback
4. `is_paper_only=True` / `is_live_authorization=False` 영구
5. 통화 = KRW 영구
6. capital_config 모듈이 broker / OrderExecutor / KIS / 외부 HTTP import 0건
7. API `GET /api/auto-paper/capital-config` + `POST` 동작 + 400 검증
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.capital_config import (
    ALLOWED_PAPER_INITIAL_CASH,
    DEFAULT_PAPER_INITIAL_CASH,
    InvalidPaperCapitalError,
    PaperCapitalConfig,
    PAPER_CAPITAL_CURRENCY,
    get_paper_capital_config,
    is_allowed_initial_cash,
    reset_paper_capital_for_tests,
    set_paper_capital_config,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_store():
    """매 테스트마다 store 를 default 로 격리."""
    reset_paper_capital_for_tests()
    yield
    reset_paper_capital_for_tests()


@pytest.fixture
def api_client():
    """In-memory SQLite + TestClient — 실제 DB / 외부 호출 0건."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False,
    )

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 상수 + 기본값
# ─────────────────────────────────────────────────────────────────────────────


class TestConstantsAndDefaults:
    def test_default_initial_cash_is_10m(self):
        assert DEFAULT_PAPER_INITIAL_CASH == 10_000_000

    def test_allowed_options_are_exactly_three(self):
        assert ALLOWED_PAPER_INITIAL_CASH == (10_000_000, 30_000_000, 50_000_000)

    def test_default_is_in_allowed(self):
        assert DEFAULT_PAPER_INITIAL_CASH in ALLOWED_PAPER_INITIAL_CASH

    def test_currency_is_krw(self):
        assert PAPER_CAPITAL_CURRENCY == "KRW"

    def test_is_allowed_helper(self):
        assert is_allowed_initial_cash(10_000_000) is True
        assert is_allowed_initial_cash(30_000_000) is True
        assert is_allowed_initial_cash(50_000_000) is True
        assert is_allowed_initial_cash(20_000_000) is False
        assert is_allowed_initial_cash(0) is False
        assert is_allowed_initial_cash(-1) is False
        assert is_allowed_initial_cash("bogus") is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 2. PaperCapitalConfig dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_default_snapshot(self):
        cfg = get_paper_capital_config()
        assert cfg.initial_cash == DEFAULT_PAPER_INITIAL_CASH
        assert cfg.allowed_initial_cash_options == ALLOWED_PAPER_INITIAL_CASH
        assert cfg.currency == "KRW"
        assert cfg.is_paper_only is True
        assert cfg.is_live_authorization is False

    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only must be True"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=ALLOWED_PAPER_INITIAL_CASH,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization must be False"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=ALLOWED_PAPER_INITIAL_CASH,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_currency_must_be_krw(self):
        with pytest.raises(ValueError, match="currency must be 'KRW'"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=ALLOWED_PAPER_INITIAL_CASH,
                currency="USD",
            )

    def test_initial_cash_must_be_in_allowed_options(self):
        with pytest.raises(ValueError, match="allowed_initial_cash_options"):
            PaperCapitalConfig(
                initial_cash=99_999_999,
                allowed_initial_cash_options=ALLOWED_PAPER_INITIAL_CASH,
            )

    def test_to_dict_payload_contract(self):
        cfg = get_paper_capital_config()
        d = cfg.to_dict()
        for key in (
            "initial_cash", "allowed_initial_cash_options", "currency",
            "is_paper_only", "is_live_authorization", "updated_at",
        ):
            assert key in d, f"missing key: {key}"
        assert d["allowed_initial_cash_options"] == [10_000_000, 30_000_000, 50_000_000]
        assert d["is_paper_only"] is True
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. set_paper_capital_config 동작
# ─────────────────────────────────────────────────────────────────────────────


class TestSetCapitalConfig:
    @pytest.mark.parametrize("value", [10_000_000, 30_000_000, 50_000_000])
    def test_allowed_values_succeed(self, value):
        cfg, fallback_used = set_paper_capital_config(value)
        assert cfg.initial_cash == value
        assert fallback_used is False
        assert cfg.updated_at  # ISO 8601 non-empty

    def test_unauthorized_value_raises(self):
        with pytest.raises(InvalidPaperCapitalError):
            set_paper_capital_config(20_000_000)

    def test_unauthorized_value_fallback_to_default(self):
        cfg, fallback_used = set_paper_capital_config(
            20_000_000, fallback_to_default=True,
        )
        assert cfg.initial_cash == DEFAULT_PAPER_INITIAL_CASH
        assert fallback_used is True

    def test_negative_value_rejected(self):
        with pytest.raises(InvalidPaperCapitalError):
            set_paper_capital_config(-1)

    def test_zero_value_rejected(self):
        with pytest.raises(InvalidPaperCapitalError):
            set_paper_capital_config(0)

    def test_huge_value_rejected(self):
        # 5천만 외 큰 값은 차단 — 실거래 한도 우회 시도 방지.
        with pytest.raises(InvalidPaperCapitalError):
            set_paper_capital_config(10_000_000_000)

    def test_set_then_get_roundtrip(self):
        set_paper_capital_config(30_000_000)
        assert get_paper_capital_config().initial_cash == 30_000_000
        set_paper_capital_config(50_000_000)
        assert get_paper_capital_config().initial_cash == 50_000_000

    def test_reset_for_tests_restores_default(self):
        set_paper_capital_config(50_000_000)
        reset_paper_capital_for_tests()
        assert get_paper_capital_config().initial_cash == DEFAULT_PAPER_INITIAL_CASH


# ─────────────────────────────────────────────────────────────────────────────
# 4. 정적 import 가드 — broker / OrderExecutor / KIS / 외부 HTTP 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestNoBrokerOrLiveImports:
    def test_capital_config_module_imports(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "app" / "auto_paper" / "capital_config.py"
        )
        text = src.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(src))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                for banned in (
                    "app.brokers.kis", "app.brokers.kis_client",
                    "app.brokers.live_broker",
                    "app.execution.executor", "app.execution.order_router",
                    "app.kis_paper.engine",
                    "anthropic", "openai", "httpx", "requests",
                    "app.core.config",  # P-01 격리 — settings 변동에 영향받지 않음
                ):
                    assert not m.startswith(banned), (
                        f"capital_config.py 가 금지 모듈 '{m}' import — "
                        "Paper 시드머니가 실거래 / settings 와 결합됨"
                    )

    def test_capital_config_source_has_no_broker_calls(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "app" / "auto_paper" / "capital_config.py"
        )
        text = src.read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"\bKisClient\s*\(",
            r"\bKisBrokerAdapter\s*\(",
            r"OrderExecutor\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
        ):
            assert not re.search(pat, text), (
                f"capital_config.py 에 금지 패턴 발견: /{pat}/"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestCapitalConfigAPI:
    def test_get_returns_default(self, api_client):
        res = api_client.get("/api/auto-paper/capital-config")
        assert res.status_code == 200
        body = res.json()
        assert body["initial_cash"] == DEFAULT_PAPER_INITIAL_CASH
        assert body["allowed_initial_cash_options"] == [
            10_000_000, 30_000_000, 50_000_000,
        ]
        assert body["currency"] == "KRW"
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False
        # 사용자에게 보이는 안내가 *실전 무관* 명시.
        assert "모의매매 전용" in body["notice"]
        assert "실전 계좌와 무관" in body["notice"]

    @pytest.mark.parametrize("value", [10_000_000, 30_000_000, 50_000_000])
    def test_post_accepts_allowed(self, api_client, value):
        res = api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": value, "fallback_to_default": False},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["initial_cash"] == value
        assert body["fallback_used"] is False
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_post_rejects_non_allowed_value_with_400(self, api_client):
        res = api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 20_000_000, "fallback_to_default": False},
        )
        assert res.status_code == 400
        body = res.json()
        # FastAPI HTTPException wraps in "detail"
        detail = body.get("detail", body)
        assert detail.get("error") == "invalid_paper_initial_cash"
        assert detail.get("allowed_initial_cash_options") == [
            10_000_000, 30_000_000, 50_000_000,
        ]
        # store 는 변경되지 않음.
        res2 = api_client.get("/api/auto-paper/capital-config")
        assert res2.json()["initial_cash"] == DEFAULT_PAPER_INITIAL_CASH

    def test_post_fallback_to_default_when_requested(self, api_client):
        res = api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 99_999_999, "fallback_to_default": True},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["initial_cash"] == DEFAULT_PAPER_INITIAL_CASH
        assert body["fallback_used"] is True

    def test_set_then_get_roundtrip(self, api_client):
        api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 50_000_000, "fallback_to_default": False},
        )
        res = api_client.get("/api/auto-paper/capital-config")
        assert res.json()["initial_cash"] == 50_000_000

    def test_payload_contains_no_secret_strings(self, api_client):
        res = api_client.get("/api/auto-paper/capital-config")
        import json
        text = json.dumps(res.json()).lower()
        for needle in ("kis_app_key", "kis_app_secret", "anthropic_api_key",
                       "openai_api_key", "telegram_bot_token", "sk-",
                       "bearer ", "kis_account_no"):
            assert needle not in text


# ─────────────────────────────────────────────────────────────────────────────
# 6. 안전 flag 변경 invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagsUntouched:
    """capital_config 변경이 .env / settings 의 안전 flag 를 mutate 하지 않음."""

    def test_settings_enable_flags_untouched_after_set(self):
        # set_paper_capital_config 가 settings 를 import 하지 않으므로
        # 본 테스트는 *정적 import 가드* + *config_path 변경 0건* 으로
        # invariant 를 확보. settings runtime mutation 0건 확인.
        from app.core.config import get_settings
        before = get_settings()
        set_paper_capital_config(50_000_000)
        after = get_settings()
        assert before.enable_live_trading == after.enable_live_trading is False
        assert before.enable_ai_execution == after.enable_ai_execution is False
        assert before.enable_futures_live_trading == after.enable_futures_live_trading is False
        assert before.kis_is_paper == after.kis_is_paper is True
