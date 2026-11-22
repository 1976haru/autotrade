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
        self.call_log: list[str] = []   # 배치 라우트가 순차 호출하는지 검증용
        self.fail_symbols: set[str] = set()

    async def get_price(self, symbol):
        self.call_log.append(symbol)
        if symbol in self.fail_symbols:
            raise RuntimeError("KIS quote endpoint returned 500: EGW00201")
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


# ---------------------------------------------------------------------------
# ratelimit_fix v2 1단계: GET /broker/prices (다종목 일괄) — N+1콜→1콜의 핵심.
# ---------------------------------------------------------------------------


def test_prices_batch_returns_all_symbols_in_one_call(fake):
    """프론트가 보유종목마다 개별 호출하는 대신 1번의 HTTP 요청으로 전부 받는다."""
    with TestClient(app) as c:
        r = c.get("/api/broker/prices?symbols=005930,000660,373220")
    assert r.status_code == 200
    body = r.json()
    assert set(body["quotes"].keys()) == {"005930", "000660", "373220"}
    for sym, q in body["quotes"].items():
        assert q["symbol"] == sym
        assert q["price"] == 71_500
        assert q["stale"] is False
    # 백엔드→KIS 호출은 여전히 종목당 1번(배치가 줄이는 건 프론트↔백엔드 왕복이지
    # 백엔드→KIS 호출 수가 아니다 — design_v2.md 문서화된 내용 그대로).
    assert fake.call_log == ["005930", "000660", "373220"]


def test_prices_batch_calls_broker_sequentially_not_concurrently(fake):
    """동시발사(gather)하면 이번 개선의 핵심(N개 동시요청 문제)이 백엔드 안에서
    재발한다 — 반드시 순차 호출이어야 한다. call_log 순서가 입력 순서와 일치하는지로
    직렬 실행을 검증(동시 실행이면 완료 순서가 뒤섞일 여지가 생긴다)."""
    with TestClient(app) as c:
        r = c.get("/api/broker/prices?symbols=A,B,C,D,E")
    assert r.status_code == 200
    assert fake.call_log == ["A", "B", "C", "D", "E"]


def test_prices_batch_partial_failure_does_not_drop_other_symbols(fake):
    """한 종목이 캐시조차 없이 실패해도(kis_error) 나머지 종목은 정상 반환 —
    배치 전체를 500으로 무효화하지 않는다(usePortfolio 의 부분실패 요구사항)."""
    fake.fail_symbols = {"000660"}
    with TestClient(app) as c:
        r = c.get("/api/broker/prices?symbols=005930,000660")
    assert r.status_code == 200
    body = r.json()
    assert body["quotes"]["005930"]["price"] == 71_500
    assert body["quotes"]["005930"]["stale"] is False
    assert body["quotes"]["000660"]["kis_error"] is True
    assert body["quotes"]["000660"]["stale"] is True


def test_prices_batch_stale_fallback_per_symbol(fake):
    """레이트리밋으로 실패한 종목은(캐시가 있으면) stale=true 로 옛 정상값을
    유지 — /price/{symbol} 과 동일한 fallback 로직을 공유한다."""
    with TestClient(app) as c:
        c.get("/api/broker/prices?symbols=005930")   # 1차: 캐시 확보
        fake.mode = "ratelimited"
        r = c.get("/api/broker/prices?symbols=005930")
    assert r.status_code == 200
    q = r.json()["quotes"]["005930"]
    assert q["price"] == 71_500
    assert q["stale"] is True


def test_prices_batch_empty_symbols_returns_empty_quotes(fake):
    with TestClient(app) as c:
        r = c.get("/api/broker/prices?symbols=")
    assert r.status_code == 200
    assert r.json() == {"quotes": {}}
    assert fake.call_log == []
