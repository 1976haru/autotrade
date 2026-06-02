"""P-02: 종목당 최대 투자금 설정 — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서 §9 매트릭스):
- 기본 종목당 한도 1,000,000 KRW
- 200만원 / 10% 선택 가능
- 시드머니 1000만원 + 10% = 100만원 (effective_per_symbol_cap_krw)
- 시드머니 3000만원 + 10% = 300만원
- 시드머니 5000만원 + 10% = 500만원
- 잘못된 mode / krw / pct 거부 (또는 fallback)
- is_paper_only=true / is_live_authorization=false / 실거래 호출 0건
- capital_config 가 broker / OrderExecutor / KIS / 외부 HTTP / settings import 0건
- API GET/POST 동작 + 400 검증
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
    ALLOWED_PER_SYMBOL_MAX_KRW,
    ALLOWED_PER_SYMBOL_MAX_PCT,
    DEFAULT_PER_SYMBOL_MAX_KRW,
    DEFAULT_PER_SYMBOL_MAX_PCT,
    DEFAULT_PER_SYMBOL_MODE,
    InvalidPerSymbolAllocationError,
    PaperCapitalConfig,
    PerSymbolAllocationMode,
    compute_effective_per_symbol_cap_krw,
    get_paper_capital_config,
    is_allowed_per_symbol_max_krw,
    is_allowed_per_symbol_max_pct,
    reset_paper_capital_for_tests,
    set_paper_capital_config,
    set_per_symbol_allocation,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_store():
    reset_paper_capital_for_tests()
    yield
    reset_paper_capital_for_tests()


@pytest.fixture
def api_client():
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


class TestConstants:
    def test_default_per_symbol_max_krw_is_1m(self):
        assert DEFAULT_PER_SYMBOL_MAX_KRW == 1_000_000

    def test_allowed_per_symbol_max_krw_options(self):
        assert ALLOWED_PER_SYMBOL_MAX_KRW == (1_000_000, 2_000_000, 3_000_000, 5_000_000)

    def test_default_pct_is_10_percent(self):
        assert DEFAULT_PER_SYMBOL_MAX_PCT == 0.10

    def test_allowed_pct_options_contain_10_percent(self):
        assert 0.10 in ALLOWED_PER_SYMBOL_MAX_PCT

    def test_default_mode_is_fixed_krw(self):
        assert DEFAULT_PER_SYMBOL_MODE == PerSymbolAllocationMode.FIXED_KRW

    def test_modes_enum_has_two_values(self):
        assert PerSymbolAllocationMode.FIXED_KRW.value == "FIXED_KRW"
        assert PerSymbolAllocationMode.PCT_OF_EQUITY.value == "PCT_OF_EQUITY"


# ─────────────────────────────────────────────────────────────────────────────
# 2. compute_effective_per_symbol_cap_krw — pure helper
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeEffectiveCap:
    def test_fixed_krw_returns_krw_value(self):
        cap = compute_effective_per_symbol_cap_krw(
            mode=PerSymbolAllocationMode.FIXED_KRW,
            per_symbol_max_krw=2_000_000,
            per_symbol_max_pct=0.10,
            initial_cash=50_000_000,
        )
        assert cap == 2_000_000

    def test_pct_with_10m_equity_yields_1m(self):
        cap = compute_effective_per_symbol_cap_krw(
            mode=PerSymbolAllocationMode.PCT_OF_EQUITY,
            per_symbol_max_krw=1_000_000,
            per_symbol_max_pct=0.10,
            initial_cash=10_000_000,
        )
        assert cap == 1_000_000

    def test_pct_with_30m_equity_yields_3m(self):
        cap = compute_effective_per_symbol_cap_krw(
            mode=PerSymbolAllocationMode.PCT_OF_EQUITY,
            per_symbol_max_krw=1_000_000,
            per_symbol_max_pct=0.10,
            initial_cash=30_000_000,
        )
        assert cap == 3_000_000

    def test_pct_with_50m_equity_yields_5m(self):
        cap = compute_effective_per_symbol_cap_krw(
            mode=PerSymbolAllocationMode.PCT_OF_EQUITY,
            per_symbol_max_krw=1_000_000,
            per_symbol_max_pct=0.10,
            initial_cash=50_000_000,
        )
        assert cap == 5_000_000

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            compute_effective_per_symbol_cap_krw(
                mode="BOGUS",  # type: ignore[arg-type]
                per_symbol_max_krw=1_000_000,
                per_symbol_max_pct=0.10,
                initial_cash=10_000_000,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. PaperCapitalConfig — per_symbol fields + invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_default_snapshot_has_per_symbol_fields(self):
        cfg = get_paper_capital_config()
        assert cfg.per_symbol_mode == PerSymbolAllocationMode.FIXED_KRW
        assert cfg.per_symbol_max_krw == DEFAULT_PER_SYMBOL_MAX_KRW
        assert cfg.per_symbol_max_pct == DEFAULT_PER_SYMBOL_MAX_PCT
        assert cfg.effective_per_symbol_cap_krw == 1_000_000  # FIXED_KRW default
        assert list(cfg.allowed_per_symbol_max_krw_options) == [1_000_000, 2_000_000, 3_000_000, 5_000_000]
        assert 0.10 in cfg.allowed_per_symbol_max_pct_options

    def test_to_dict_carries_per_symbol_fields(self):
        cfg = get_paper_capital_config()
        d = cfg.to_dict()
        for key in (
            "per_symbol_mode", "per_symbol_max_krw", "per_symbol_max_pct",
            "effective_per_symbol_cap_krw",
            "allowed_per_symbol_max_krw_options",
            "allowed_per_symbol_max_pct_options",
        ):
            assert key in d, f"missing P-02 field: {key}"
        assert d["per_symbol_mode"] == "FIXED_KRW"
        assert d["effective_per_symbol_cap_krw"] == 1_000_000

    def test_invalid_per_symbol_krw_rejected_at_dataclass(self):
        with pytest.raises(ValueError, match="per_symbol_max_krw"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=(10_000_000, 30_000_000, 50_000_000),
                per_symbol_max_krw=999_999,  # not in allowed
            )

    def test_invalid_per_symbol_pct_rejected_at_dataclass(self):
        with pytest.raises(ValueError, match="per_symbol_max_pct"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=(10_000_000, 30_000_000, 50_000_000),
                per_symbol_max_pct=0.25,  # not in allowed
            )

    def test_invalid_mode_type_rejected(self):
        with pytest.raises(ValueError, match="per_symbol_mode"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=(10_000_000, 30_000_000, 50_000_000),
                per_symbol_mode="FIXED_KRW",  # type: ignore[arg-type]
            )

    def test_paper_only_invariant_still_holds(self):
        cfg = get_paper_capital_config()
        assert cfg.is_paper_only is True
        assert cfg.is_live_authorization is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. set_per_symbol_allocation 동작
# ─────────────────────────────────────────────────────────────────────────────


class TestSetPerSymbolAllocation:
    def test_default_state_after_reset(self):
        cfg = get_paper_capital_config()
        assert cfg.per_symbol_max_krw == 1_000_000
        assert cfg.per_symbol_mode == PerSymbolAllocationMode.FIXED_KRW

    @pytest.mark.parametrize("value", [1_000_000, 2_000_000])
    def test_set_allowed_krw(self, value):
        cfg, fallback = set_per_symbol_allocation(per_symbol_max_krw=value)
        assert cfg.per_symbol_max_krw == value
        assert fallback is False

    def test_set_allowed_pct(self):
        cfg, fallback = set_per_symbol_allocation(per_symbol_max_pct=0.10)
        assert cfg.per_symbol_max_pct == 0.10
        assert fallback is False

    def test_set_mode_to_pct_yields_correct_effective_cap(self):
        # 시드머니 1000만원 (default) + 10% mode → 100만원.
        cfg, _ = set_per_symbol_allocation(
            mode=PerSymbolAllocationMode.PCT_OF_EQUITY,
        )
        assert cfg.per_symbol_mode == PerSymbolAllocationMode.PCT_OF_EQUITY
        assert cfg.effective_per_symbol_cap_krw == 1_000_000

    def test_pct_mode_recomputes_when_initial_cash_changes(self):
        # 시드머니를 3000만원으로 바꾸고 mode 를 PCT 로 → 300만원 effective.
        set_paper_capital_config(30_000_000)
        cfg, _ = set_per_symbol_allocation(
            mode=PerSymbolAllocationMode.PCT_OF_EQUITY,
        )
        assert cfg.effective_per_symbol_cap_krw == 3_000_000

    def test_fixed_krw_mode_ignores_initial_cash(self):
        set_paper_capital_config(50_000_000)
        cfg, _ = set_per_symbol_allocation(
            mode=PerSymbolAllocationMode.FIXED_KRW,
            per_symbol_max_krw=2_000_000,
        )
        # 시드머니 5000만원이어도 FIXED_KRW 모드에서는 200만원 그대로.
        assert cfg.effective_per_symbol_cap_krw == 2_000_000

    def test_reject_unknown_mode(self):
        with pytest.raises(InvalidPerSymbolAllocationError):
            set_per_symbol_allocation(mode="BOGUS_MODE")

    def test_reject_unknown_krw(self):
        with pytest.raises(InvalidPerSymbolAllocationError):
            set_per_symbol_allocation(per_symbol_max_krw=999_999)

    def test_reject_unknown_pct(self):
        with pytest.raises(InvalidPerSymbolAllocationError):
            set_per_symbol_allocation(per_symbol_max_pct=0.25)

    def test_fallback_to_default_on_unknown_krw(self):
        cfg, fallback = set_per_symbol_allocation(
            per_symbol_max_krw=999_999,
            fallback_to_default=True,
        )
        assert cfg.per_symbol_max_krw == DEFAULT_PER_SYMBOL_MAX_KRW
        assert fallback is True

    def test_partial_update_preserves_other_fields(self):
        # mode 만 변경 → krw / pct 는 default 유지.
        cfg, _ = set_per_symbol_allocation(mode=PerSymbolAllocationMode.PCT_OF_EQUITY)
        assert cfg.per_symbol_max_krw == DEFAULT_PER_SYMBOL_MAX_KRW
        assert cfg.per_symbol_max_pct == DEFAULT_PER_SYMBOL_MAX_PCT

    def test_is_allowed_helpers(self):
        assert is_allowed_per_symbol_max_krw(1_000_000) is True
        assert is_allowed_per_symbol_max_krw(2_000_000) is True
        assert is_allowed_per_symbol_max_krw(999_999) is False
        assert is_allowed_per_symbol_max_pct(0.10) is True
        assert is_allowed_per_symbol_max_pct(0.25) is False
        assert is_allowed_per_symbol_max_krw("bogus") is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 5. 정적 import 가드 — broker / OrderExecutor / KIS / settings 0건 유지
# ─────────────────────────────────────────────────────────────────────────────


class TestNoBrokerOrLiveImports:
    def test_capital_config_module_no_banned_imports(self):
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
                    "app.core.config",
                ):
                    assert not m.startswith(banned), (
                        f"capital_config.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
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
                f"capital_config.py 에 금지 패턴: /{pat}/"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_get_returns_default_per_symbol_fields(self, api_client):
        res = api_client.get("/api/auto-paper/capital-config")
        assert res.status_code == 200
        body = res.json()
        # P-02 신규 필드 모두 carry.
        assert body["per_symbol_mode"] == "FIXED_KRW"
        assert body["per_symbol_max_krw"] == 1_000_000
        assert body["per_symbol_max_pct"] == 0.10
        assert body["effective_per_symbol_cap_krw"] == 1_000_000
        assert body["allowed_per_symbol_max_krw_options"] == [1_000_000, 2_000_000, 3_000_000, 5_000_000]
        assert 0.10 in body["allowed_per_symbol_max_pct_options"]

    @pytest.mark.parametrize("krw", [1_000_000, 2_000_000])
    def test_post_accepts_allowed_krw(self, api_client, krw):
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"per_symbol_max_krw": krw, "fallback_to_default": False},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["per_symbol_max_krw"] == krw
        assert body["fallback_used"] is False
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_post_switch_to_pct_mode_with_10m_equity(self, api_client):
        # 시드머니 default 1000만원 + 10% → 100만원.
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "PCT_OF_EQUITY"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["per_symbol_mode"] == "PCT_OF_EQUITY"
        assert body["effective_per_symbol_cap_krw"] == 1_000_000

    def test_post_pct_mode_with_30m_equity_yields_3m(self, api_client):
        api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 30_000_000, "fallback_to_default": False},
        )
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "PCT_OF_EQUITY"},
        )
        assert res.json()["effective_per_symbol_cap_krw"] == 3_000_000

    def test_post_pct_mode_with_50m_equity_yields_5m(self, api_client):
        api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 50_000_000, "fallback_to_default": False},
        )
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "PCT_OF_EQUITY"},
        )
        assert res.json()["effective_per_symbol_cap_krw"] == 5_000_000

    def test_post_invalid_mode_400(self, api_client):
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "BOGUS_MODE"},
        )
        assert res.status_code == 400
        detail = res.json().get("detail", {})
        assert detail.get("error") == "invalid_per_symbol_allocation"
        assert "FIXED_KRW" in detail.get("allowed_modes", [])
        assert "PCT_OF_EQUITY" in detail.get("allowed_modes", [])

    def test_post_invalid_krw_400(self, api_client):
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"per_symbol_max_krw": 999_999},
        )
        assert res.status_code == 400
        detail = res.json().get("detail", {})
        assert detail.get("allowed_per_symbol_max_krw_options") == [
            1_000_000, 2_000_000, 3_000_000, 5_000_000,
        ]

    def test_post_fallback_to_default(self, api_client):
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={
                "per_symbol_max_krw": 999_999,
                "fallback_to_default": True,
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["fallback_used"] is True
        assert body["per_symbol_max_krw"] == DEFAULT_PER_SYMBOL_MAX_KRW

    def test_payload_contains_no_secret_strings(self, api_client):
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "FIXED_KRW", "per_symbol_max_krw": 2_000_000},
        )
        import json
        text = json.dumps(res.json()).lower()
        for needle in ("kis_app_key", "kis_app_secret", "anthropic_api_key",
                       "openai_api_key", "telegram_bot_token", "sk-",
                       "bearer ", "kis_account_no"):
            assert needle not in text

    def test_notice_clarifies_paper_only_not_live(self, api_client):
        res = api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "FIXED_KRW", "per_symbol_max_krw": 1_000_000},
        )
        body = res.json()
        assert "모의매매 전용" in body["notice"]
        assert "실전 주문금액이" in body["notice"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. 안전 flag 변경 invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagsUntouched:
    def test_settings_enable_flags_untouched_after_set(self):
        from app.core.config import get_settings
        before = get_settings()
        set_per_symbol_allocation(per_symbol_max_krw=2_000_000)
        after = get_settings()
        assert before.enable_live_trading == after.enable_live_trading is False
        assert before.enable_ai_execution == after.enable_ai_execution is False
        assert before.enable_futures_live_trading == after.enable_futures_live_trading is False
        assert before.kis_is_paper == after.kis_is_paper is True
