"""P-16: Paper 자금 설정 영구 저장 — module + API 테스트.

검증 매트릭스:
 - 파일 부재 → DEFAULT
 - save → reload round-trip (PERSISTED)
 - reset → 파일 삭제 + DEFAULT
 - AGENT_TRADER_CONFIG_DIR override 적용
 - config dir auto-create
 - atomic write (임시 파일 잔여 0건)
 - 깨진 JSON → DEFAULT_CORRUPTED fallback
 - 검증 (범위 / per_symbol<=total / daily<=total / max_positions int / weight (0,1])
 - allow_additional_buy default False / boolean only
 - risk_profile default BALANCED / 알 수 없는 값 → BALANCED
 - secret-like key reject / secret value reject (fail-closed)
 - API GET / POST / reset
 - invariants is_live_authorization / is_order_signal / contains_secret = False
 - 저장 파일에 api_key / secret / account_no 0건
 - 정적 grep: 모듈 안전 flag mutation / broker import 0건
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.settings.persistent_settings as ps
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.settings.persistent_settings import (
    DEFAULT_SETTINGS,
    SecretInSettingsError,
    load_paper_capital_settings,
    reset_paper_capital_settings,
    save_paper_capital_settings,
    scan_for_secrets,
    validate_paper_capital_settings,
)


_MODULE_PATH = Path(ps.__file__).resolve()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """AGENT_TRADER_CONFIG_DIR override → 테스트 격리 (실 OS 폴더 미오염)."""
    d = tmp_path / "agenttrader_config"
    monkeypatch.setenv("AGENT_TRADER_CONFIG_DIR", str(d))
    return d


# ─────────────────────────────────────────────────────────────────────────────
# load / save / reset
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadSaveReset:
    def test_load_default_when_file_absent(self, config_dir):
        res = load_paper_capital_settings()
        assert res.source == "DEFAULT"
        assert res.settings == DEFAULT_SETTINGS
        assert res.is_live_authorization is False
        assert res.is_order_signal is False
        assert res.contains_secret is False

    def test_save_then_reload_round_trip(self, config_dir):
        save_paper_capital_settings({
            "total_paper_capital":  30_000_000,
            "per_symbol_allocation": 2_000_000,
            "max_positions":        8,
            "max_daily_buy_amount": 5_000_000,
            "max_symbol_weight_pct": 0.3,
            "allow_additional_buy": True,
            "risk_profile":         "AGGRESSIVE",
        })
        res = load_paper_capital_settings()
        assert res.source == "PERSISTED"
        assert res.settings["total_paper_capital"] == 30_000_000
        assert res.settings["max_positions"] == 8
        assert res.settings["allow_additional_buy"] is True
        assert res.settings["risk_profile"] == "AGGRESSIVE"

    def test_save_creates_config_dir(self, config_dir):
        assert not config_dir.exists()
        save_paper_capital_settings({"max_positions": 7})
        assert config_dir.exists()
        assert (config_dir / "paper_capital_settings.json").exists()

    def test_save_uses_override_dir(self, config_dir):
        save_paper_capital_settings({"max_positions": 3})
        path = config_dir / "paper_capital_settings.json"
        assert path.exists()
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["version"] == ps.SCHEMA_VERSION
        assert doc["settings"]["max_positions"] == 3
        assert doc["safety"]["is_live_authorization"] is False

    def test_reset_removes_file_and_returns_default(self, config_dir):
        save_paper_capital_settings({"max_positions": 9})
        path = config_dir / "paper_capital_settings.json"
        assert path.exists()
        res = reset_paper_capital_settings()
        assert res.source == "DEFAULT"
        assert res.settings == DEFAULT_SETTINGS
        assert not path.exists()

    def test_atomic_write_leaves_no_temp_files(self, config_dir):
        save_paper_capital_settings({"max_positions": 4})
        leftovers = list(config_dir.glob(".tmp_*"))
        assert leftovers == []

    def test_corrupted_json_falls_back_to_default(self, config_dir):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "paper_capital_settings.json").write_text(
            "}{ not json", encoding="utf-8",
        )
        res = load_paper_capital_settings()
        assert res.source == "DEFAULT_CORRUPTED"
        assert res.settings == DEFAULT_SETTINGS
        assert any("corrupted" in e for e in res.errors)


# ─────────────────────────────────────────────────────────────────────────────
# validation
# ─────────────────────────────────────────────────────────────────────────────


class TestValidation:
    def test_empty_input_returns_defaults(self):
        out, errors = validate_paper_capital_settings({})
        assert out == DEFAULT_SETTINGS
        assert errors == []

    def test_total_below_min_rejected(self):
        out, errors = validate_paper_capital_settings({"total_paper_capital": 50_000})
        assert out["total_paper_capital"] == DEFAULT_SETTINGS["total_paper_capital"]
        assert errors

    def test_per_symbol_over_total_rejected(self):
        out, errors = validate_paper_capital_settings({
            "total_paper_capital": 1_000_000,
            "per_symbol_allocation": 2_000_000,
        })
        assert out["per_symbol_allocation"] == DEFAULT_SETTINGS["per_symbol_allocation"]
        assert any("per_symbol_allocation" in e for e in errors)

    def test_daily_over_total_rejected(self):
        out, errors = validate_paper_capital_settings({
            "total_paper_capital": 1_000_000,
            "max_daily_buy_amount": 5_000_000,
        })
        assert out["max_daily_buy_amount"] == DEFAULT_SETTINGS["max_daily_buy_amount"]
        assert any("max_daily_buy_amount" in e for e in errors)

    def test_max_positions_must_be_int_in_range(self):
        assert validate_paper_capital_settings({"max_positions": 0})[1]
        assert validate_paper_capital_settings({"max_positions": 101})[1]
        # bool is not a valid int here.
        assert validate_paper_capital_settings({"max_positions": True})[1]
        out, errors = validate_paper_capital_settings({"max_positions": 10})
        assert out["max_positions"] == 10 and errors == []

    def test_weight_pct_bounds(self):
        assert validate_paper_capital_settings({"max_symbol_weight_pct": 0})[1]
        assert validate_paper_capital_settings({"max_symbol_weight_pct": 1.5})[1]
        out, errors = validate_paper_capital_settings({"max_symbol_weight_pct": 1.0})
        assert out["max_symbol_weight_pct"] == 1.0 and errors == []

    def test_allow_additional_buy_default_false_and_boolean_only(self):
        assert DEFAULT_SETTINGS["allow_additional_buy"] is False
        out, errors = validate_paper_capital_settings({"allow_additional_buy": "yes"})
        assert out["allow_additional_buy"] is False
        assert errors

    def test_risk_profile_default_balanced_and_unknown_falls_back(self):
        assert DEFAULT_SETTINGS["risk_profile"] == "BALANCED"
        out, errors = validate_paper_capital_settings({"risk_profile": "nonsense"})
        assert out["risk_profile"] == "BALANCED"
        assert errors
        out2, errors2 = validate_paper_capital_settings({"risk_profile": "aggressive"})
        assert out2["risk_profile"] == "AGGRESSIVE"
        assert errors2 == []


# ─────────────────────────────────────────────────────────────────────────────
# secret scan (fail-closed)
# ─────────────────────────────────────────────────────────────────────────────


class TestSecretScan:
    @pytest.mark.parametrize("key", [
        "api_key", "app_secret", "account_no", "access_token",
        "refresh_token", "password", "anthropic_api_key", "kis_app_key",
    ])
    def test_secret_like_key_rejected(self, key):
        with pytest.raises(SecretInSettingsError):
            scan_for_secrets({key: "whatever"})

    @pytest.mark.parametrize("value", [
        "sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        "sk-ant-ABCDEFGHIJKLMNOPQRSTUVWX",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "12345678-01",                       # 한국 계좌번호 형태
    ])
    def test_secret_value_pattern_rejected(self, value):
        with pytest.raises(SecretInSettingsError):
            scan_for_secrets({"note": value})

    def test_clean_capital_settings_pass(self):
        # 정상 자금 기준은 통과.
        scan_for_secrets({
            "total_paper_capital": 10_000_000,
            "max_positions": 5,
            "risk_profile": "BALANCED",
        })

    def test_save_rejects_secret_key(self, config_dir):
        with pytest.raises(SecretInSettingsError):
            save_paper_capital_settings({"api_key": "sk-xxx", "max_positions": 5})
        # 거부했으므로 파일 미작성.
        assert not (config_dir / "paper_capital_settings.json").exists()


# ─────────────────────────────────────────────────────────────────────────────
# API endpoints
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client(config_dir):
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


class TestApi:
    def test_get_returns_default_when_absent(self, api_client):
        r = api_client.get("/api/auto-paper/paper-capital-settings")
        assert r.status_code == 200
        b = r.json()
        assert b["source"] == "DEFAULT"
        assert b["ok"] is True
        assert b["is_live_authorization"] is False
        assert b["is_order_signal"] is False
        assert b["contains_secret"] is False
        assert ".env" in b["notice"]

    def test_post_then_get_persisted(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings",
            json={
                "total_paper_capital": 30_000_000,
                "max_positions": 8,
                "risk_profile": "AGGRESSIVE",
            },
        )
        assert r.status_code == 200
        assert r.json()["source"] == "PERSISTED"
        assert r.json()["settings"]["total_paper_capital"] == 30_000_000

        r2 = api_client.get("/api/auto-paper/paper-capital-settings")
        assert r2.json()["source"] == "PERSISTED"
        assert r2.json()["settings"]["max_positions"] == 8
        assert r2.json()["settings"]["risk_profile"] == "AGGRESSIVE"

    def test_post_secret_blocked_400(self, api_client, config_dir):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings",
            json={"api_key": "sk-secretvalue1234567890", "max_positions": 5},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "secret_in_settings_blocked"
        assert detail["is_live_authorization"] is False
        # 저장 파일 미작성.
        assert not (config_dir / "paper_capital_settings.json").exists()

    def test_reset_endpoint(self, api_client, config_dir):
        api_client.post(
            "/api/auto-paper/paper-capital-settings",
            json={"max_positions": 9},
        )
        assert (config_dir / "paper_capital_settings.json").exists()
        r = api_client.post("/api/auto-paper/paper-capital-settings/reset")
        assert r.status_code == 200
        assert r.json()["source"] == "DEFAULT"
        assert not (config_dir / "paper_capital_settings.json").exists()

    def test_saved_file_has_no_secret_fields(self, api_client, config_dir):
        api_client.post(
            "/api/auto-paper/paper-capital-settings",
            json={"max_positions": 6, "risk_profile": "CONSERVATIVE"},
        )
        raw = (config_dir / "paper_capital_settings.json").read_text(encoding="utf-8")
        low = raw.lower()
        for banned in ("api_key", "app_secret", "account_no", "access_token",
                       "refresh_token", "password"):
            assert banned not in low


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_no_broker_or_executor_import(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"from app\.brokers",
            r"import app\.brokers",
            r"from app\.execution",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"\bbroker\.place_order\s*\(",
            r"import httpx",
            r"import requests",
            r"import anthropic",
            r"import openai",
        ):
            assert not re.search(pat, text), f"persistent_settings.py 금지 패턴: /{pat}/"

    def test_module_no_safety_flag_mutation(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"enable_live_trading\s*=",
            r"enable_ai_execution\s*=",
            r"enable_futures_live_trading\s*=",
            r"kis_is_paper\s*=",
        ):
            assert not re.search(pat, text), (
                f"persistent_settings.py 안전 flag mutation 의심: /{pat}/"
            )
