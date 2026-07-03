"""수동 매매 엔드포인트 테스트 (Task D).

검증 목표:
  1. trade_reason 고정 (BUY→manual_buy, SELL→manual_sell)
  2. route_order 경유 (결과 status=SUBMITTED via MockBroker)
  3. naked SELL 차단 (보유 없음)
  4. 보유 초과 SELL 차단
  5. 현재가 조회 endpoint
  6. 봇 원장 — manual_buy 가 _bot_owned_symbols 에서 제외됨
  7. 보호 4계층 미수정 (broker.place_order 직접 호출 0)
"""
from __future__ import annotations

import pytest
from app.brokers.base import Position


# ─────────────────────────────────────────────────────────
# 보조 픽스처
# ─────────────────────────────────────────────────────────

@pytest.fixture
def _held(client):
    """005930 10주 보유 세팅."""
    client.test_broker.positions["005930"] = Position(
        symbol="005930", quantity=10, avg_price=75_000, market_price=75_000
    )
    return client


# ─────────────────────────────────────────────────────────
# 1. trade_reason 고정
# ─────────────────────────────────────────────────────────

def test_buy_trade_reason_is_manual_buy(client):
    res = client.post("/api/manual-order", json={"symbol": "005930", "side": "BUY", "quantity": 1})
    assert res.status_code == 200
    assert res.json()["trade_reason"] == "manual_buy"


def test_sell_trade_reason_is_manual_sell(_held):
    res = _held.post("/api/manual-order", json={"symbol": "005930", "side": "SELL", "quantity": 5})
    assert res.status_code == 200
    assert res.json()["trade_reason"] == "manual_sell"


# ─────────────────────────────────────────────────────────
# 2. route_order 경유 — 결과 SUBMITTED (MockBroker → APPROVED)
# ─────────────────────────────────────────────────────────

def test_buy_status_submitted(client):
    res = client.post("/api/manual-order", json={"symbol": "005930", "side": "BUY", "quantity": 2})
    assert res.status_code == 200
    assert res.json()["status"] == "SUBMITTED"


def test_sell_status_submitted(_held):
    res = _held.post("/api/manual-order", json={"symbol": "005930", "side": "SELL", "quantity": 3})
    assert res.status_code == 200
    assert res.json()["status"] == "SUBMITTED"


# ─────────────────────────────────────────────────────────
# 3. naked SELL 차단
# ─────────────────────────────────────────────────────────

def test_naked_sell_blocked(client):
    res = client.post("/api/manual-order", json={"symbol": "005930", "side": "SELL", "quantity": 1})
    assert res.status_code == 400
    assert "보유 수량이 없어" in res.json()["detail"]


# ─────────────────────────────────────────────────────────
# 4. 보유 초과 SELL 차단
# ─────────────────────────────────────────────────────────

def test_sell_exceeds_qty_blocked(_held):
    res = _held.post("/api/manual-order", json={"symbol": "005930", "side": "SELL", "quantity": 99})
    assert res.status_code == 400
    assert "초과" in res.json()["detail"]


def test_sell_exact_qty_allowed(_held):
    """보유량과 정확히 같은 수량은 허용."""
    res = _held.post("/api/manual-order", json={"symbol": "005930", "side": "SELL", "quantity": 10})
    assert res.status_code == 200


# ─────────────────────────────────────────────────────────
# 5. 현재가 조회
# ─────────────────────────────────────────────────────────

def test_quote_known_symbol(client):
    res = client.get("/api/manual-order/quote/005930")
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "005930"
    assert data["price"] > 0


def test_quote_unknown_symbol_returns_default(client):
    """MockBroker는 미등록 종목에 기본가(50000) 반환."""
    res = client.get("/api/manual-order/quote/UNKNOWN")
    assert res.status_code == 200
    assert res.json()["price"] >= 0


# ─────────────────────────────────────────────────────────
# 6. 봇 원장 — _bot_owned_symbols 분류
# ─────────────────────────────────────────────────────────

def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_manual_buy_not_in_bot_owned():
    """manual_buy 주문은 _bot_owned_symbols에 포함 안 됨 — 봇 청산 대상 제외."""
    from app.db.models import OrderAuditLog
    from app.kis_paper.driver_bridge import _bot_owned_symbols
    db = _make_db()
    db.add(OrderAuditLog(
        symbol="005930", side="BUY", trade_reason="manual_buy",
        decision="APPROVED", filled_quantity=5,
        mode="PAPER", quantity=5, order_type="MARKET", latest_price=75_000,
    ))
    db.commit()
    owned = _bot_owned_symbols(db)
    assert owned is not None
    assert "005930" not in owned
    db.close()


def test_kis_paper_auto_is_in_bot_owned():
    """kis_paper_auto 주문은 _bot_owned_symbols에 포함됨."""
    from app.db.models import OrderAuditLog
    from app.kis_paper.driver_bridge import _bot_owned_symbols
    db = _make_db()
    db.add(OrderAuditLog(
        symbol="000660", side="BUY", trade_reason="kis_paper_auto",
        decision="APPROVED", filled_quantity=3,
        mode="PAPER", quantity=3, order_type="MARKET", latest_price=185_000,
    ))
    db.commit()
    owned = _bot_owned_symbols(db)
    assert "000660" in owned
    db.close()


# ─────────────────────────────────────────────────────────
# 7. 보호 4계층 미수정 — broker.place_order 직접 호출 0
# ─────────────────────────────────────────────────────────

def test_route_does_not_call_broker_place_order_directly():
    import inspect
    import app.api.routes_manual_order as mod
    src = inspect.getsource(mod)
    assert "broker.place_order" not in src
    assert "route_order" in src


# ─────────────────────────────────────────────────────────
# 8. 유효성 검증
# ─────────────────────────────────────────────────────────

def test_invalid_side_rejected(client):
    res = client.post("/api/manual-order", json={"symbol": "005930", "side": "HOLD", "quantity": 1})
    assert res.status_code == 422


def test_zero_quantity_rejected(client):
    res = client.post("/api/manual-order", json={"symbol": "005930", "side": "BUY", "quantity": 0})
    assert res.status_code == 422
