"""M4: 라이브 포지션 + 수동 전량 매도 — route_order 경유 / 가드 / 긴급정지 / 피드."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import OrderSide, Position
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.main import app
from app.risk.risk_manager import RiskDecision


class _FakeBroker:
    def __init__(self, positions=None, raise_positions=False):
        self._positions = positions or []
        self._raise = raise_positions
        self.price_calls = 0
        self.place_calls = 0

    async def get_positions(self):
        if self._raise:
            raise RuntimeError("balance fetch failed")
        return self._positions

    async def get_price(self, symbol):
        self.price_calls += 1     # ★신규 시세 폴링 감시
        return SimpleNamespace(price=0)

    async def place_order(self, *a, **k):
        self.place_calls += 1     # ★broker 직접 호출 감시
        return SimpleNamespace(broker_order_id="X")


def _client(broker, *, risk=None, routing=None, monkeypatch=None):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, expire_on_commit=False)

    def _ovdb():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_broker] = lambda: broker
    app.dependency_overrides[get_risk_manager] = lambda: (risk or object())
    app.dependency_overrides[get_db] = _ovdb
    if routing is not None and monkeypatch is not None:
        import app.api.routes_positions as rp
        captured = {}
        async def _spy(**kw):
            captured.update(kw)
            return routing
        monkeypatch.setattr(rp, "route_order", _spy)
        return TestClient(app), TS, captured
    return TestClient(app), TS, None


def _cleanup():
    for dep in (get_broker, get_risk_manager, get_db):
        app.dependency_overrides.pop(dep, None)


def _pos(symbol, qty, avg, mkt):
    return Position(symbol=symbol, quantity=qty, avg_price=avg, market_price=mkt)


# ── M1: GET /positions/live ────────────────────────────────────────────────────

def test_live_positions_enriched_and_no_quote_polling():
    broker = _FakeBroker([_pos("005930", 3, 70000, 75000)])
    c, _TS, _ = _client(broker)
    try:
        r = c.get("/api/positions/live")
        assert r.status_code == 200
        b = r.json()
        assert b["available"] is True
        p = b["positions"][0]
        assert p["symbol"] == "005930" and p["name"] == "삼성전자"
        assert p["eval_pnl_krw"] == (75000 - 70000) * 3
        assert p["status"] == "sellable"
        assert broker.price_calls == 0   # ★시세 별도 폴링 미추가 (잔고 응답만)
    finally:
        _cleanup()


def test_live_positions_uses_kis_name_for_unknown_code():
    # V4: 정적 맵에 없는 071050 도 KIS 종목명(prdt_name)을 그대로 사용(지어내기 금지).
    p071 = Position(symbol="071050", quantity=1, avg_price=10000, market_price=11000, name="한국금융지주")
    broker = _FakeBroker([p071])
    c, _TS, _ = _client(broker)
    try:
        r = c.get("/api/positions/live")
        assert r.status_code == 200
        assert r.json()["positions"][0]["name"] == "한국금융지주"
    finally:
        _cleanup()


def test_live_positions_failure_flag_not_empty_list():
    broker = _FakeBroker(raise_positions=True)
    c, _TS, _ = _client(broker)
    try:
        b = c.get("/api/positions/live").json()
        assert b["available"] is False          # 실패와 보유 0 은 다르게
        assert b["reason"] == "FETCH_FAILED"
    finally:
        _cleanup()


def test_live_positions_marks_sell_in_progress():
    broker = _FakeBroker([_pos("005930", 3, 70000, 75000)])
    c, TS, _ = _client(broker)
    try:
        with TS() as s:
            s.add(OrderAuditLog(created_at=datetime.now(timezone.utc), mode="PAPER",
                                requested_by_ai=False, symbol="005930", side="SELL",
                                quantity=3, order_type="MARKET", decision="APPROVED",
                                executed=True, broker_order_id="O1", broker_status="RECEIVED", filled_quantity=0,
                                limit_price=75000, latest_price=75000,
                                trade_reason="manual_sell_all"))
            s.commit()
        p = c.get("/api/positions/live").json()["positions"][0]
        assert p["status"] == "sell_in_progress"
    finally:
        _cleanup()


# ── M2: POST sell-all ───────────────────────────────────────────────────────────

def test_sell_all_goes_through_route_order_with_manual_origin(monkeypatch):
    broker = _FakeBroker([_pos("005930", 3, 70000, 75000)])
    routing = SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                              audit=SimpleNamespace(broker_order_id="ODNO-9"), approval=None)
    c, TS, captured = _client(broker, routing=routing, monkeypatch=monkeypatch)
    try:
        r = c.post("/api/positions/005930/sell-all")
        assert r.status_code == 200
        b = r.json()
        assert b["status"] == "SUBMITTED"
        assert b["broker_order_no"] == "ODNO-9"
        assert "주문을 보냈어요" in b["message"]   # 체결 단정 금지
        # ★route_order 경유 + manual origin 메타(trade_reason) — broker.place_order 직접 0.
        order = captured["order"]
        assert order.side == OrderSide.SELL and order.quantity == 3
        assert order.order_type.value == "MARKET"
        assert order.trade_reason == "manual_sell_all"
        assert order.strategy is None and captured["requested_by_ai"] is False
        assert broker.place_calls == 0
    finally:
        _cleanup()


def test_sell_all_no_holding_400():
    broker = _FakeBroker([])  # 보유 0
    c, _TS, _ = _client(broker)
    try:
        r = c.post("/api/positions/005930/sell-all")
        assert r.status_code == 400
        assert "보유 수량" in r.json()["detail"]
    finally:
        _cleanup()


def test_sell_all_fetch_failure_400():
    broker = _FakeBroker(raise_positions=True)
    c, _TS, _ = _client(broker)
    try:
        r = c.post("/api/positions/005930/sell-all")
        assert r.status_code == 400
    finally:
        _cleanup()


def test_sell_all_open_sell_409(monkeypatch):
    broker = _FakeBroker([_pos("005930", 3, 70000, 75000)])
    routing = SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                              audit=SimpleNamespace(broker_order_id="X"), approval=None)
    c, TS, _ = _client(broker, routing=routing, monkeypatch=monkeypatch)
    try:
        with TS() as s:
            s.add(OrderAuditLog(created_at=datetime.now(timezone.utc), mode="PAPER",
                                requested_by_ai=False, symbol="005930", side="SELL",
                                quantity=3, order_type="MARKET", decision="APPROVED",
                                executed=True, broker_order_id="O1", broker_status="RECEIVED", filled_quantity=0,
                                limit_price=75000, latest_price=75000,
                                trade_reason="manual_sell_all"))
            s.commit()
        r = c.post("/api/positions/005930/sell-all")
        assert r.status_code == 409
        assert "이미 매도 주문이 진행 중" in r.json()["detail"]
    finally:
        _cleanup()


def test_sell_all_emergency_stop_blocked_message(monkeypatch):
    broker = _FakeBroker([_pos("005930", 3, 70000, 75000)])
    routing = SimpleNamespace(decision=RiskDecision.REJECTED,
                              reasons=["emergency stop is enabled"], audit=None, approval=None)
    c, TS, _ = _client(broker, routing=routing, monkeypatch=monkeypatch)
    try:
        r = c.post("/api/positions/005930/sell-all")
        assert r.status_code == 400
        assert "긴급정지 중이라" in r.json()["detail"]
        # 활동 피드에 거절 기록.
        from app.auto_paper.decision_log import query_paper_decision_log
        with TS() as s:
            texts = [e.reason for e in query_paper_decision_log(s, limit=10)]
        assert any("수동 매도가 거절됐어요" in t for t in texts)
    finally:
        _cleanup()


def test_sell_all_submit_records_activity_feed(monkeypatch):
    broker = _FakeBroker([_pos("005930", 3, 70000, 75000)])
    routing = SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                              audit=SimpleNamespace(broker_order_id="ODNO-9"), approval=None)
    c, TS, _ = _client(broker, routing=routing, monkeypatch=monkeypatch)
    try:
        c.post("/api/positions/005930/sell-all")
        from app.auto_paper.decision_log import query_paper_decision_log
        with TS() as s:
            texts = [e.reason for e in query_paper_decision_log(s, limit=10)]
        assert any("삼성전자 3주 전량 매도 주문을 보냈어요" in t for t in texts)
    finally:
        _cleanup()


def test_routes_positions_no_direct_broker_place_order():
    # M4 회귀: 라우트는 route_order 만 호출 — broker.place_order 직접 호출 0.
    import inspect
    import app.api.routes_positions as rp
    src = inspect.getsource(rp)
    assert "route_order" in src
    assert "place_order" not in src


# ── 설계 B 조각 1: POST manual-buy (수동 매수, route_order 경유, manual_buy 태깅) ──

def test_manual_buy_goes_through_route_order_with_manual_buy_tag(monkeypatch):
    broker = _FakeBroker([])
    routing = SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                              audit=SimpleNamespace(broker_order_id="ODNO-BUY-1"), approval=None)
    c, TS, captured = _client(broker, routing=routing, monkeypatch=monkeypatch)
    try:
        r = c.post("/api/positions/manual-buy", json={"symbol": "005930", "quantity": 5})
        assert r.status_code == 200
        b = r.json()
        assert b["status"] == "SUBMITTED" and b["source"] == "MANUAL"
        assert b["broker_order_no"] == "ODNO-BUY-1"
        assert "주문을 보냈어요" in b["message"]
        # ★route_order 경유 + manual_buy 태깅 — broker.place_order 직접 0, council/한도 우회.
        order = captured["order"]
        assert order.side == OrderSide.BUY and order.quantity == 5
        assert order.order_type.value == "MARKET"
        assert order.trade_reason == "manual_buy"
        assert order.strategy is None and captured["requested_by_ai"] is False
        assert broker.place_calls == 0
    finally:
        _cleanup()


def test_manual_buy_rejected_passes_through_riskmanager(monkeypatch):
    # ★긴급정지/PAPER/잔고는 route_order 안 RiskManager 가 평가 — REJECTED 면 400(우회 0).
    broker = _FakeBroker([])
    routing = SimpleNamespace(decision=RiskDecision.REJECTED,
                              reasons=["emergency stop active"], audit=None, approval=None)
    c, TS, captured = _client(broker, routing=routing, monkeypatch=monkeypatch)
    try:
        r = c.post("/api/positions/manual-buy", json={"symbol": "005930", "quantity": 5})
        assert r.status_code == 400
        assert "긴급정지" in r.json()["detail"]   # RiskManager 거부 사유 그대로 전달
        # route_order 는 호출됐다(우회 없이 게이트 통과 시도).
        assert captured["order"].trade_reason == "manual_buy"
    finally:
        _cleanup()


def test_manual_buy_quantity_validation():
    broker = _FakeBroker([])
    c, _TS, _ = _client(broker)
    try:
        assert c.post("/api/positions/manual-buy", json={"symbol": "005930", "quantity": 0}).status_code == 422
        assert c.post("/api/positions/manual-buy", json={"symbol": "", "quantity": 5}).status_code == 400
    finally:
        _cleanup()
