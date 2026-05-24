"""4-01: KIS 모의투자 자격 입력/검증 (readiness credential gate).

backend/.env 의 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO / KIS_PRODUCT_CODE
가 설정됐는지 검증하고, API / 응답 어디에도 secret / account_no 원문이 노출되지
않음을 lock. 실거래 0건 — pure settings 검사, broker / KIS 실 API 호출 0건.
"""

from __future__ import annotations

import pathlib

import pytest

from app.kis_paper.readiness import KisPaperReadiness, evaluate_readiness

_READINESS_SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app" / "kis_paper" / "readiness.py"
)


def _settings(**overrides) -> dict:
    base = {
        "kis_is_paper":                True,
        "enable_live_trading":         False,
        "enable_ai_execution":         False,
        "enable_futures_live_trading": False,
        "default_mode":                "PAPER",
        "paper_broker_kind":           "KIS_PAPER",
        "enable_kis_paper_auto_trading": True,
        "kis_paper_auto_order_dry_run":  False,
        "kis_paper_fill_polling":        True,
        "kis_app_key":                 "FAKE_paper_key_0001",
        "kis_app_secret":              "FAKE_paper_secret_0001",
        "kis_account_no":              "12345678-01",
        "kis_product_code":            "01",
    }
    base.update(overrides)
    return base


# ── credentials_present 종합 ──

def test_all_credentials_present():
    rd = evaluate_readiness(_settings())
    assert rd.credentials_present is True
    assert rd.missing_credentials == ()
    assert rd.kis_key_present is True
    assert rd.kis_secret_present is True
    assert rd.kis_account_present is True
    assert rd.kis_product_code_present is True


def test_no_credentials_means_not_present():
    rd = evaluate_readiness(_settings(
        kis_app_key="", kis_app_secret="", kis_account_no="", kis_product_code=""))
    assert rd.credentials_present is False
    assert set(rd.missing_credentials) == {
        "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_PRODUCT_CODE",
    }


def test_only_app_key_present_is_false():
    rd = evaluate_readiness(_settings(
        kis_app_secret="", kis_account_no="", kis_product_code=""))
    assert rd.credentials_present is False
    assert rd.kis_key_present is True
    assert "KIS_APP_SECRET" in rd.missing_credentials


def test_missing_secret_makes_false():
    rd = evaluate_readiness(_settings(kis_app_secret=""))
    assert rd.credentials_present is False
    assert rd.missing_credentials == ("KIS_APP_SECRET",)


def test_missing_account_makes_false():
    rd = evaluate_readiness(_settings(kis_account_no=""))
    assert rd.credentials_present is False
    assert "KIS_ACCOUNT_NO" in rd.missing_credentials


def test_missing_product_code_makes_false():
    rd = evaluate_readiness(_settings(kis_product_code=""))
    assert rd.credentials_present is False
    assert "KIS_PRODUCT_CODE" in rd.missing_credentials


def test_product_code_defaults_present_when_unspecified():
    # dict 에 kis_product_code 키 자체가 없으면 config default "01" 로 본다.
    s = _settings()
    del s["kis_product_code"]
    rd = evaluate_readiness(s)
    assert rd.kis_product_code_present is True


# ── readiness BLOCKED / READY (can_run_kis_paper 기준) ──

def test_credentials_missing_blocks_kis_paper_order():
    rd = evaluate_readiness(_settings(kis_app_key="", kis_app_secret=""))
    # 자격 미설정 → KIS 모의 주문 진입 불가 (can_run_kis_paper=False).
    assert rd.can_run_kis_paper is False


def test_credentials_present_allows_kis_paper_order_stage():
    rd = evaluate_readiness(_settings())
    assert rd.can_run_kis_paper is True
    assert rd.ready is True


# ── 안전 flag 검증 ──

def test_safety_flags_carry():
    rd = evaluate_readiness(_settings())
    sf = rd.safety_flags
    assert sf["kis_is_paper"] is True
    assert sf["default_mode"] == "PAPER"
    assert sf["paper_broker_kind"] == "KIS_PAPER"
    assert sf["enable_kis_paper_auto_trading"] is True
    assert sf["kis_paper_auto_order_dry_run"] is False
    assert sf["kis_paper_fill_polling"] is True
    assert sf["enable_live_trading"] is False
    assert sf["enable_ai_execution"] is False


def test_to_dict_exposes_flags_and_presence():
    d = evaluate_readiness(_settings()).to_dict()
    assert d["credentials_present"] is True
    assert d["kis_app_key_present"] is True
    assert d["kis_app_secret_present"] is True
    assert d["kis_account_no_present"] is True
    assert d["kis_product_code_present"] is True
    assert d["product_code_present"] is True
    assert d["default_mode"] == "PAPER"
    assert d["paper_broker_kind"] == "KIS_PAPER"
    assert d["dry_run"] is False
    assert d["fill_polling"] is True
    assert d["enable_live_trading"] is False
    assert d["enable_ai_execution"] is False
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False
    assert d["missing_credentials"] == []


# ── secret / account 원문 미노출 ──

def test_secret_value_never_in_output():
    s = _settings(kis_app_secret="SUPER_SECRET_VALUE_DO_NOT_LEAK_123456")
    blob = str(evaluate_readiness(s).to_dict())
    assert "SUPER_SECRET_VALUE_DO_NOT_LEAK_123456" not in blob


def test_account_no_value_never_in_output():
    s = _settings(kis_account_no="87654321-99")
    blob = str(evaluate_readiness(s).to_dict())
    assert "87654321-99" not in blob
    assert "87654321" not in blob


def test_app_key_value_never_in_output():
    s = _settings(kis_app_key="PKxxxAPPKEYxxxLEAKME999")
    blob = str(evaluate_readiness(s).to_dict())
    assert "PKxxxAPPKEYxxxLEAKME999" not in blob


def test_missing_credentials_only_key_names():
    rd = evaluate_readiness(_settings(
        kis_app_secret="LEAK_THIS_SECRET", kis_account_no="11112222-33"))
    # missing 에는 KIS_APP_SECRET 등 *키 이름만* — 값/길이 carry 0건.
    for name in rd.missing_credentials:
        assert name in {
            "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_PRODUCT_CODE",
        }


# ── invariant ──

def test_readiness_invariants_locked():
    with pytest.raises(ValueError):
        KisPaperReadiness(ready=True, can_run_kis_paper=True, can_run_mock=True,
                          is_live_authorization=True)
    with pytest.raises(ValueError):
        KisPaperReadiness(ready=True, can_run_kis_paper=True, can_run_mock=True,
                          contains_secret=True)


def test_module_does_not_import_broker_or_call_live():
    src = _READINESS_SRC.read_text(encoding="utf-8")
    for bad in ("place_order", "route_order(", "from app.brokers",
                "import app.brokers", "kis_client", "httpx", "requests"):
        assert bad not in src, f"forbidden token in readiness module: {bad}"


# ── API endpoint ──

@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def test_auto_status_credentials_fields(api_client):
    r = api_client.get("/api/kis-paper/auto/status")
    assert r.status_code == 200
    b = r.json()
    for key in ("credentials_present", "kis_app_key_present",
                "kis_app_secret_present", "kis_account_no_present",
                "kis_product_code_present", "missing_credentials",
                "default_mode", "paper_broker_kind", "dry_run",
                "fill_polling", "is_live_authorization"):
        assert key in b, f"missing field: {key}"
    assert b["is_live_authorization"] is False
    assert b["contains_secret"] is False


def test_readiness_endpoint_no_secret_leak(api_client):
    raw = api_client.get("/api/kis-paper/readiness").text.lower()
    for banned in ("app_secret\":", "secret_value", "access_token\":"):
        assert banned not in raw
