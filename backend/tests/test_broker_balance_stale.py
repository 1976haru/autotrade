"""T2/T4: balance stale 폴백 — 레이트리밋/일시 실패 시 마지막 정상값(가짜 0 금지)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routes_broker as rb
from app.api.deps import get_broker
from app.brokers.kis_client import KisTokenRateLimitedError
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
        return _Bal(100_000_000, 100_066_651)


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

        fake.mode = "ratelimited"          # 토큰 레이트리밋
        r2 = c.get("/api/broker/balance")
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["cash"] == 100_000_000   # 마지막 정상값(가짜 0 아님)
        assert b2["stale"] is True and b2["broker_healthy"] is False
        assert b2["as_of_kst"] == b1["as_of_kst"]   # 기준시각 carry


def test_no_cache_failure_returns_503_broker_unhealthy(fake):
    fake.mode = "down"
    with TestClient(app) as c:
        r = c.get("/api/broker/balance")
    assert r.status_code == 503
    body = r.json()
    assert body["broker_healthy"] is False and body["kis_error"] is True
