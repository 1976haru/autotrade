"""T2/T4: balance stale 폴백 — 레이트리밋/일시 실패 시 마지막 정상값(가짜 0 금지)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routes_broker as rb
from app.api.deps import get_broker
from app.brokers.kis_client import KisTokenRateLimitedError
from app.brokers.kis_client import KisApiError
from app.main import app


class _Bal:
    def __init__(self, cash, equity):
        self.cash, self.equity, self.buying_power, self.currency = cash, equity, cash, "KRW"


class _FakeBroker:
    def __init__(self):
        self.mode = "ok"

    async def get_balance(self):
        if self.mode == "ratelimited":
            raise KisTokenRateLimitedError("EGW00133 backoff")
        if self.mode == "down":
            raise RuntimeError("kis down")
        if self.mode == "kis_diagnostic":
            raise KisApiError("safe protocol failure", diagnostic={
                "http_status": 200, "kis_rt_cd": "1", "kis_msg_cd": "EGW00201",
                "sanitized_error_summary": "KIS rate limit rejected the read-only inquiry.",
                "api_path": "/uapi/domestic-stock/v1/trading/inquire-balance",
                "tr_id": "VTTC8434R", "appkey": "must-not-leak",
            })
        return _Bal(100_000_000, 100_066_651)

    async def get_positions(self):
        if self.mode != "ok":
            raise RuntimeError("kis positions unavailable")
        return []


@pytest.fixture
def fake():
    b = _FakeBroker()
    rb._last_good_balance = None          # 모듈 전역 캐시 격리
    app.dependency_overrides[get_broker] = lambda: b
    yield b
    app.dependency_overrides.pop(get_broker, None)
    rb._last_good_balance = None


def test_success_then_ratelimited_returns_stale(fake):
    with TestClient(app) as c:
        r1 = c.get("/api/broker/balance")
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["cash"] == 100_000_000 and b1["stale"] is False and b1["broker_healthy"] is True
        assert b1["account_balance_verified"] is True and b1["account_state"] == "VERIFIED"

        fake.mode = "ratelimited"          # 토큰 레이트리밋
        r2 = c.get("/api/broker/balance")
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["cash"] == 100_000_000   # 마지막 정상값(가짜 0 아님)
        assert b2["stale"] is True and b2["broker_healthy"] is False
        assert b2["account_balance_verified"] is False
        assert b2["account_state"] == "ACCOUNT_BALANCE_UNVERIFIED"
        assert b2["as_of_kst"] == b1["as_of_kst"]   # 기준시각 carry


def test_no_cache_failure_returns_503_broker_unhealthy(fake):
    fake.mode = "down"
    with TestClient(app) as c:
        r = c.get("/api/broker/balance")
    assert r.status_code == 503
    body = r.json()
    assert body["broker_healthy"] is False and body["kis_error"] is True
    assert body["code"] == "ACCOUNT_BALANCE_UNVERIFIED"
    assert body["account_balance_verified"] is False


def test_balance_503_exposes_only_sanitized_kis_diagnostic(fake):
    fake.mode = "kis_diagnostic"
    with TestClient(app) as c:
        response = c.get("/api/broker/balance")
    assert response.status_code == 503
    body = response.json()
    assert body["diagnostic"]["http_status"] == 200
    assert body["diagnostic"]["kis_rt_cd"] == "1"
    assert body["diagnostic"]["kis_msg_cd"] == "EGW00201"
    assert "appkey" not in body["diagnostic"]


def test_positions_error_is_not_reported_as_empty_account(fake):
    fake.mode = "down"
    with TestClient(app) as c:
        r = c.get("/api/broker/positions")
    assert r.status_code == 503
    assert r.json()["code"] == "ACCOUNT_BALANCE_UNVERIFIED"
    assert r.json()["account_balance_verified"] is False
