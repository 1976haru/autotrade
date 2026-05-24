"""4-02: KIS 모의 자동주문 .env 조합 확인.

EXE 기본 `.env` 조합만으로 KIS 모의투자 자동주문이 *안전하게 준비* 되는지
검증한다. `.env.example` 의 기본 조합 + `/auto/status` 의 READY/BLOCKED 판정 +
secret/account 원문 미노출 + 안전 flag 유지(LIVE/AI/FUTURES OFF) 를 lock.
실거래 0건 — pure settings / read-only API 검사, KIS 실 API 호출 0건.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kis_paper.readiness import evaluate_readiness
from app.main import app

_ENV = Path(__file__).resolve().parents[1] / ".env.example"


def _pairs() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


# ── 1. .env.example 기본 조합 ──

@pytest.mark.parametrize("key,expected", [
    ("DEFAULT_MODE",                   "PAPER"),
    ("KIS_IS_PAPER",                   "true"),
    ("PAPER_BROKER_KIND",              "KIS_PAPER"),
    ("ENABLE_AI_PAPER_BACKGROUND_TICK", "true"),
    ("ENABLE_KIS_PAPER_AUTO_TRADING",  "true"),
    ("KIS_PAPER_AUTO_ORDER_DRY_RUN",   "false"),
    ("KIS_PAPER_FILL_POLLING",         "true"),
    ("ENABLE_LIVE_TRADING",            "false"),
    ("ENABLE_AI_EXECUTION",            "false"),
    ("ENABLE_FUTURES_LIVE_TRADING",    "false"),
    ("KIS_PRODUCT_CODE",               "01"),
])
def test_env_example_combination(key, expected):
    assert _pairs().get(key) == expected, f"{key} must be {expected}"


def test_env_example_secrets_blank():
    p = _pairs()
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"):
        assert p.get(k, "") == "", f"{k} must be blank in .env.example"


def test_env_example_no_real_secret_patterns():
    raw = _ENV.read_text(encoding="utf-8")
    for pat in (r"sk-[A-Za-z0-9]{20,}", r"\b\d{6,}-\d{2,}\b", r"PST[A-Za-z0-9]{20,}"):
        assert not re.search(pat, raw), f"secret pattern in .env.example: /{pat}/"


# ── 2. readiness READY / BLOCKED (자격 조건) ──

def _settings(**overrides) -> dict:
    base = {
        "kis_is_paper":                True,
        "enable_live_trading":         False,
        "enable_ai_execution":         False,
        "enable_futures_live_trading": False,
        "default_mode":                "PAPER",
        "paper_broker_kind":           "KIS_PAPER",
        "enable_ai_paper_background_tick": True,
        "enable_kis_paper_auto_trading": True,
        "kis_paper_auto_order_dry_run":  False,
        "kis_paper_fill_polling":        True,
        "kis_app_key":                 "FAKE_key_0001",
        "kis_app_secret":              "FAKE_secret_0001",
        "kis_account_no":              "12345678-01",
        "kis_product_code":            "01",
    }
    base.update(overrides)
    return base


def test_credentials_present_means_ready():
    rd = evaluate_readiness(_settings())
    assert rd.credentials_present is True
    assert rd.can_run_kis_paper is True       # READY for KIS paper auto.
    assert rd.ready is True


def test_missing_credentials_means_blocked():
    rd = evaluate_readiness(_settings(kis_app_key="", kis_app_secret=""))
    assert rd.credentials_present is False
    assert rd.can_run_kis_paper is False      # BLOCKED.
    assert "KIS_APP_KEY" in rd.missing_credentials


# ── 3. /auto/status 조합 필드 + READY/BLOCKED + invariant ──

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_auto_status_carries_combination_fields(client):
    b = client.get("/api/kis-paper/auto/status").json()
    for key in ("default_mode", "kis_is_paper", "paper_broker_kind",
                "enable_ai_paper_background_tick", "enable_kis_paper_auto_trading",
                "dry_run", "fill_polling", "enable_live_trading",
                "enable_ai_execution", "enable_futures_live_trading",
                "credentials_present", "kis_paper_auto_ready",
                "broker_order_type", "is_live_authorization"):
        assert key in b, f"auto/status missing field: {key}"


def test_auto_status_invariants(client):
    b = client.get("/api/kis-paper/auto/status").json()
    assert b["is_live_authorization"] is False
    assert b["contains_secret"] is False
    assert b["broker_order_type"] == "KIS_PAPER"


def test_auto_status_ready_field_is_boolean(client):
    b = client.get("/api/kis-paper/auto/status").json()
    assert isinstance(b["kis_paper_auto_ready"], bool)
    # 자격 미설정 환경(테스트 process)이면 READY=False (BLOCKED).
    if not b["credentials_present"]:
        assert b["kis_paper_auto_ready"] is False


def test_auto_status_no_secret_or_account_value(client):
    raw = client.get("/api/kis-paper/auto/status").text
    for pat in (r"sk-[A-Za-z0-9]{20,}", r"\b\d{6,}-\d{2,}\b"):
        assert not re.search(pat, raw), f"secret/account 노출 의심: /{pat}/"
    # credential 키는 *_present boolean 만 노출.
    b = client.get("/api/kis-paper/auto/status").json()
    for key in b:
        kl = key.lower()
        if any(t in kl for t in ("app_key", "app_secret", "account_no")):
            assert kl.endswith("_present")
            assert isinstance(b[key], bool)
        assert "access_token" not in kl
