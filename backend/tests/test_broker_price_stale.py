"""ratelimit_fix 권고1: /broker/price stale 폴백 — /balance 와 동일 패턴
(test_broker_balance_stale.py 미러). 레이트리밋/일시 실패 시 마지막 정상 시세를
stale=true 로 반환해, 프런트(usePortfolio.js) 의 Promise.all 배치가 예외로 죽어
매 15초 전체 재조회를 반복하는 재시도 폭풍을 막는다(results/ratelimit_fix/design.md).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routes_broker as rb
from app.api.deps import get_broker
from app.main import app


class _Quote:
    def __init__(self, symbol, price):
        self.symbol = symbol
        self.price = price
        self.timestamp = "2026-07-09T00:00:00+00:00"
        self.source = "kis"


class _FakeBroker:
    def __init__(self):
        self.mode = "ok"
        self.price = 71_500

    async def get_price(self, symbol):
        if self.mode == "ratelimited":
            raise RuntimeError("KIS quote endpoint returned 500: EGW00201")
        if self.mode == "down":
            raise RuntimeError("kis down")
        return _Quote(symbol, self.price)


@pytest.fixture
def fake():
    b = _FakeBroker()
    rb._last_good_quotes = {}   # 모듈 전역 캐시 격리
    app.dependency_overrides[get_broker] = lambda: b
    yield b
    app.dependency_overrides.pop(get_broker, None)
    rb._last_good_quotes = {}


def test_success_then_ratelimited_returns_stale(fake):
    with TestClient(app) as c:
        r1 = c.get("/api/broker/price/005930")
        assert r1.status_code == 200
        q1 = r1.json()
        assert q1["price"] == 71_500 and q1["stale"] is False

        fake.mode = "ratelimited"          # EGW00201
        r2 = c.get("/api/broker/price/005930")
        assert r2.status_code == 200        # ★ 500 이 아니라 200 stale — 재시도 폭풍 방지 핵심
        q2 = r2.json()
        assert q2["price"] == 71_500        # 마지막 정상값(가짜 0/None 아님)
        assert q2["stale"] is True
        assert q2["as_of_kst"] == q1["as_of_kst"]   # 기준시각 carry


def test_no_cache_failure_returns_503(fake):
    fake.mode = "down"
    with TestClient(app) as c:
        r = c.get("/api/broker/price/999999")
    assert r.status_code == 503
    body = r.json()
    assert body["kis_error"] is True


def test_different_symbols_have_independent_stale_cache(fake):
    """한 종목의 캐시가 다른(한 번도 성공 못한) 종목까지 살려주면 안 된다."""
    with TestClient(app) as c:
        r1 = c.get("/api/broker/price/005930")
        assert r1.status_code == 200

        fake.mode = "ratelimited"
        r2 = c.get("/api/broker/price/000660")   # 캐시 없음 → 503
    assert r2.status_code == 503


def test_price_updates_after_recovery(fake):
    """레이트리밋 이후 정상화되면 다시 fresh(stale=False) + 새 값 반영."""
    with TestClient(app) as c:
        c.get("/api/broker/price/005930")
        fake.mode = "ratelimited"
        c.get("/api/broker/price/005930")
        fake.mode = "ok"
        fake.price = 72_000
        r = c.get("/api/broker/price/005930")
    assert r.status_code == 200
    body = r.json()
    assert body["price"] == 72_000 and body["stale"] is False
