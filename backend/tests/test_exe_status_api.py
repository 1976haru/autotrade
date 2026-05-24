"""#53 / 7-01 — `GET /api/system/exe-status` 정합성 + 안전 invariant 테스트.

본 endpoint 는 EXE 운영 상태(backend_api_reachable / sidecar_status /
diagnostics_status / db_status / kis_paper_readiness)를 *분리된* 표준 enum 으로
emit 하는 read-only 상태 표시용이다.

검증:
- 9개 표준 필드 존재 + enum 값 유효
- is_live_authorization=False / contains_secret=False 불변
- Secret / 계좌번호 / API key 원문 0건 (settings 에 가짜 secret 주입해도 누출 X)
- broker.place_order 호출 0건 (MockBroker.orders 비어 있음)
- kis_paper_readiness 가 안전 설정에서 READY, live flag 위반 시 BLOCKED 매핑
- 기존 /api/kis-paper/readiness, /api/system/diagnostics 무회귀
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import app.api.routes_system as routes_system


_VALID_SIDECAR = {"RUNNING", "STARTING", "STOPPED", "UNKNOWN"}
_VALID_DIAGNOSTICS = {"OK", "DEGRADED", "FAIL", "UNKNOWN"}
_VALID_DB = {"OK", "FAIL", "UNKNOWN"}
_VALID_KIS = {"READY", "BLOCKED", "UNKNOWN"}

_REQUIRED_FIELDS = (
    "backend_api_reachable",
    "sidecar_status",
    "diagnostics_status",
    "db_status",
    "kis_paper_readiness",
    "checked_at",
    "last_error_message",
    "is_live_authorization",
    "contains_secret",
)


def _safe_settings(**overrides) -> SimpleNamespace:
    """기본 안전 설정(SimpleNamespace). 운영자 위반 케이스 시뮬은 overrides 로."""
    base = dict(
        kis_is_paper=True,
        enable_live_trading=False,
        enable_ai_execution=False,
        enable_futures_live_trading=False,
        default_mode="PAPER",
        kis_app_key="FAKE-APP-KEY-PLACEHOLDER-0000",
        kis_app_secret="FAKE-APP-SECRET-PLACEHOLDER-0000",
        kis_account_no="00000000-00",
        kis_product_code="01",
        enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False,
        kis_paper_fill_polling=True,
        paper_broker_kind="KIS_PAPER",
        market_data_provider="mock",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_exe_status_returns_all_required_fields(client):
    r = client.get("/api/system/exe-status")
    assert r.status_code == 200
    body = r.json()
    for field in _REQUIRED_FIELDS:
        assert field in body, f"missing field: {field}"


def test_exe_status_enums_are_valid(client):
    body = client.get("/api/system/exe-status").json()
    assert body["sidecar_status"] in _VALID_SIDECAR
    assert body["diagnostics_status"] in _VALID_DIAGNOSTICS
    assert body["db_status"] in _VALID_DB
    assert body["kis_paper_readiness"] in _VALID_KIS


def test_exe_status_backend_api_reachable_true_when_responding(client):
    # endpoint 가 응답한 이상 backend 는 reachable — body 는 항상 True.
    body = client.get("/api/system/exe-status").json()
    assert body["backend_api_reachable"] is True


def test_exe_status_safety_invariants(client):
    body = client.get("/api/system/exe-status").json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_exe_status_does_not_leak_secret_or_account(client, monkeypatch):
    # settings 에 진짜처럼 보이는 secret / 계좌번호를 주입해도 응답 본문에
    # 원문이 절대 나오면 안 된다 (readiness 가 boolean 만 산출).
    secret_blob = "REALSECRET_abcdef0123456789_DO_NOT_LEAK"
    account_blob = "98765432-11"
    monkeypatch.setattr(
        routes_system, "get_settings",
        lambda: _safe_settings(
            kis_app_secret=secret_blob,
            kis_app_key="REALKEY_abcdef0123456789",
            kis_account_no=account_blob,
        ),
    )
    text = json.dumps(client.get("/api/system/exe-status").json(), ensure_ascii=False)
    assert secret_blob not in text
    assert account_blob not in text
    assert "REALKEY_abcdef0123456789" not in text


def test_exe_status_no_broker_order_placed(client):
    client.get("/api/system/exe-status")
    # read-only — broker 에 어떤 주문도 들어가면 안 됨.
    assert len(client.test_broker.orders) == 0


def test_exe_status_kis_paper_ready_when_safe(client, monkeypatch):
    monkeypatch.setattr(
        routes_system, "get_settings", lambda: _safe_settings(),
    )
    body = client.get("/api/system/exe-status").json()
    assert body["kis_paper_readiness"] == "READY"


def test_exe_status_kis_paper_blocked_when_live_enabled(client, monkeypatch):
    # ENABLE_LIVE_TRADING=true 시뮬(in-memory 테스트 전용 — .env 변경 아님).
    monkeypatch.setattr(
        routes_system, "get_settings",
        lambda: _safe_settings(enable_live_trading=True),
    )
    body = client.get("/api/system/exe-status").json()
    assert body["kis_paper_readiness"] == "BLOCKED"


def test_exe_status_kis_paper_ready_even_without_credentials(client, monkeypatch):
    # 자격 미구성은 *blocker 가 아니라 capability* — mock 으로는 여전히 실행
    # 가능하므로 readiness 는 READY 로 유지된다 (live flag 위반만 BLOCKED).
    # 자격 구성 여부는 KisPaperEnvStatusCard 가 별도로 표시.
    monkeypatch.setattr(
        routes_system, "get_settings",
        lambda: _safe_settings(
            kis_app_key="", kis_app_secret="", kis_account_no="",
        ),
    )
    body = client.get("/api/system/exe-status").json()
    assert body["kis_paper_readiness"] == "READY"


def test_exe_status_sidecar_unknown_without_marker(client, monkeypatch):
    monkeypatch.delenv("AUTOTRADE_DESKTOP_SIDECAR", raising=False)
    body = client.get("/api/system/exe-status").json()
    assert body["sidecar_status"] == "UNKNOWN"


def test_exe_status_sidecar_running_with_marker(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_DESKTOP_SIDECAR", "1")
    body = client.get("/api/system/exe-status").json()
    assert body["sidecar_status"] == "RUNNING"


def test_kis_paper_readiness_endpoint_no_regression(client):
    # 기존 endpoint 가 그대로 동작 (본 PR 가 깨뜨리지 않음).
    r = client.get("/api/kis-paper/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_system_diagnostics_endpoint_no_regression(client):
    r = client.get("/api/system/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False
