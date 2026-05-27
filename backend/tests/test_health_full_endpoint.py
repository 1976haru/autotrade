"""/api/health + /api/health/full 엔드포인트 테스트 (read-only 컴포넌트 집계).

기존 root /health (backendLauncher fallback) 와는 별개 — 본 엔드포인트는
컴포넌트별 OK/WARN/FAIL 집계. CLAUDE.md invariant: secret 값 0건, 주문 0건,
is_live_authorization=False.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_health_liveness():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_health_full_shape_and_invariants():
    r = client.get("/api/health/full")
    assert r.status_code == 200
    d = r.json()
    assert d["overall"] in ("OK", "WARN", "FAIL")
    for k in ("api", "db", "kis_credentials", "safety_flags",
              "kis_paper_ready", "market_data"):
        assert k in d["checks"], f"missing check: {k}"
        assert d["checks"][k]["status"] in ("OK", "WARN", "FAIL")
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False
    assert isinstance(d["credentials_present"], bool)


def test_api_health_full_no_secret_values():
    body = client.get("/api/health/full").text
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", body)      # API key 패턴
    assert not re.search(r"\b\d{8}-\d{2}\b", body)          # 한국 계좌번호 8-2
    low = body.lower()
    for forbidden in ("kis_app_secret", "app_secret"):
        # 키 *이름* 은 안전 flag detail 에 없음 — secret 값/시크릿 키명 0건.
        assert forbidden not in low


def test_api_health_full_stability_block():
    """CHECKLIST-03 — stability 블록(watchdog/tick/uptime) 포함, 안전 불변."""
    d = client.get("/api/health/full").json()
    assert "stability" in d
    s = d["stability"]
    for k in ("watchdog_enabled", "backend_uptime_sec", "last_tick_at",
              "tick_stale", "engine_state", "errors_last_5min",
              "recovery_success_rate", "secret_leak_detected", "log_file_path"):
        assert k in s, f"missing stability field: {k}"
    assert s["secret_leak_detected"] is False
    assert isinstance(s["watchdog_enabled"], bool)
    assert isinstance(s["backend_uptime_sec"], (int, float))


def test_api_health_full_safety_flags_reported():
    d = client.get("/api/health/full").json()
    sf = d["checks"]["safety_flags"]
    # 안전 flag 현재값이 boolean 으로 노출 (값 변경 아님, 표시만).
    for k in ("enable_live_trading", "enable_ai_execution",
              "enable_futures_live_trading", "kis_is_paper"):
        assert isinstance(sf[k], bool)
