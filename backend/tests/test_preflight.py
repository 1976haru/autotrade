"""V8: 출발 전 점검(preflight) — 6항목 OK/FAIL/WARN, 신규 KIS 호출은 잔고 1회뿐."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import Position
from app.main import app


class _Bal:
    cash, equity, buying_power, currency = 100_000_000, 100_000_000, 100_000_000, "KRW"


class _Broker:
    def __init__(self, fail=False):
        self.fail = fail
        self.balance_calls = 0
        self.position_calls = 0

    async def get_balance(self):
        self.balance_calls += 1
        if self.fail:
            raise RuntimeError("kis down")
        return _Bal()

    async def get_positions(self):
        self.position_calls += 1
        return [Position(symbol="005930", quantity=3, avg_price=70000, market_price=75000, name="삼성전자")]


@pytest.fixture
def _wire():
    def _setup(broker, *, estop=False):
        app.dependency_overrides[get_broker] = lambda: broker
        app.dependency_overrides[get_risk_manager] = lambda: SimpleNamespace(emergency_stop=estop)
    yield _setup
    app.dependency_overrides.pop(get_broker, None)
    app.dependency_overrides.pop(get_risk_manager, None)


def test_preflight_all_items_present(_wire):
    b = _Broker()
    _wire(b)
    with TestClient(app) as c:
        r = c.get("/api/preflight")
    assert r.status_code == 200
    body = r.json()
    keys = {i["key"] for i in body["items"]}
    assert keys == {"env", "safety_flags", "kis", "token_cache", "holdings", "emergency_stop"}
    assert body["is_live_authorization"] is False
    # ⑤ 가 ③ 잔고와 같은 캐시에 편승 — get_balance 1회(신규 KIS 호출 1).
    assert b.balance_calls == 1


def test_preflight_kis_fail_marks_fail(_wire):
    _wire(_Broker(fail=True))
    with TestClient(app) as c:
        body = c.get("/api/preflight").json()
    kis = next(i for i in body["items"] if i["key"] == "kis")
    assert kis["status"] == "fail"
    assert body["all_ok"] is False


def test_preflight_emergency_on_warns(_wire):
    _wire(_Broker(), estop=True)
    with TestClient(app) as c:
        body = c.get("/api/preflight").json()
    es = next(i for i in body["items"] if i["key"] == "emergency_stop")
    assert es["status"] == "warn"
    assert "켜짐" in es["detail"]
