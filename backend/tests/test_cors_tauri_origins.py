"""CORS 매트릭스 — Tauri 데스크톱 webview origin 이 허용되어야 한다.

fix/step1-backend-autoconnect-final: EXE 모드에서 Tauri webview 는 cross-origin
fetch (origin = `tauri://localhost` / `https://tauri.localhost`) 로 backend 에
접근한다. 기존 `cors_origins` 는 dev 서버 (localhost:5173) 만 포함해 EXE 모드의
모든 fetch 가 CORS 차단으로 실패 → frontend 가 backend 를 *offline* 으로 오인.

본 테스트는 CORSMiddleware 가 Tauri webview origin 들을 모두 허용하는지,
그리고 dev 서버 origin 도 *함께* 허용하는지 검증.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# Tauri webview 의 다양한 origin 변형 — 본 list 의 *모든* origin 이 허용되어야.
TAURI_ALLOWED_ORIGINS = [
    "tauri://localhost",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "http://localhost:5173",          # vite dev
    "http://127.0.0.1:5173",
    "http://localhost:8000",          # backend 자기 자신
    "http://127.0.0.1:8000",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
]


@pytest.mark.parametrize("origin", TAURI_ALLOWED_ORIGINS)
def test_cors_allows_tauri_and_local_origins(client: TestClient, origin: str):
    """CORS preflight + actual request 모두 origin 을 echo back 해야 한다."""
    # Preflight (OPTIONS).
    resp = client.options(
        "/api/status",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    # FastAPI CORSMiddleware 는 preflight 를 직접 처리해 200 / 204 응답.
    assert resp.status_code in (200, 204), (
        f"preflight failed for origin {origin!r}: status={resp.status_code} body={resp.text[:200]}"
    )
    allowed = resp.headers.get("access-control-allow-origin", "")
    assert allowed == origin, (
        f"preflight access-control-allow-origin mismatch for {origin!r}: got {allowed!r}"
    )

    # Actual GET.
    resp2 = client.get("/api/status", headers={"Origin": origin})
    assert resp2.status_code == 200
    allowed2 = resp2.headers.get("access-control-allow-origin", "")
    assert allowed2 == origin


def test_cors_rejects_arbitrary_external_origin(client: TestClient):
    """Tauri / localhost 외 외부 origin (예: evil.example.com) 은 거부."""
    resp = client.options(
        "/api/status",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Preflight 는 200 이지만 access-control-allow-origin 헤더가 *비어* 있어야.
    allowed = resp.headers.get("access-control-allow-origin", "")
    assert allowed != "https://evil.example.com", (
        f"external origin should not be echoed: got {allowed!r}"
    )
