"""GET /health endpoint — backendLauncher /api/status 실패 시 fallback.

CLAUDE.md invariant:
- 응답에 secret 키 / 패턴 0건
- DB / monitoring 의존성 0건 (최소 liveness probe)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def test_health_returns_200():
    res = _client().get("/health")
    assert res.status_code == 200


def test_health_response_shape():
    body = _client().get("/health").json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert "app" in body


def test_health_response_has_no_secret_keys():
    body = _client().get("/health").json()
    body_str = str(body).lower()
    for forbidden in (
        "api_key", "secret", "password", "token",
        "kis_app_key", "kis_app_secret", "kis_account_no",
        "anthropic", "openai", "telegram",
        "계좌번호",
    ):
        assert forbidden not in body_str


def test_health_is_lightweight_no_query_params_needed():
    """/api/status 와 달리 query parameter / auth header 없이 200 응답."""
    res = _client().get("/health")
    assert res.status_code == 200
    # body 가 4 키 정도로 작아야 — 디버그 dump 가 아님.
    body = res.json()
    assert len(body) <= 5
