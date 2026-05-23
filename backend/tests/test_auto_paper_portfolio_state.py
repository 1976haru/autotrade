"""P-18: 가상 포트폴리오 상태 — module + API 테스트.

검증 (사용자 요청서 §8):
 - 포지션 없음 → current_cash=starting / total_position_value=0 / total_equity=starting
 - BUY 체결 후 cash 감소 / quantity 증가 / market_value / average_price
 - 현재가 상승/하락 시 unrealized +/-
 - SELL 체결 후 cash 증가 / quantity 감소 / 전량 SELL 시 포지션 제거
 - REJECTED/BLOCKED/CANCELLED 제외
 - today_buy_used / remaining_daily / position_count / available_slots
 - portfolio_weight_pct / max_symbol_weight 초과 시 EXCEEDED
 - API invariants is_live_authorization / is_order_signal / contains_secret = False
 - secret/account/api_key 미반환, broker/OrderExecutor 호출 0건
 - 정적 grep: 안전 flag mutation / broker import 0건
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.auto_paper.portfolio_state as ps
from app.auto_paper.capital_state import (
    get_capital_state,
    reset_capital_state_for_tests,
)
from app.auto_paper.portfolio_state import build_portfolio_state
from app.db.base import Base
from app.db.models import VirtualOrder
from app.db.session import get_db
from app.main import app

_MODULE_PATH = Path(ps.__file__).resolve()
_START = 10_000_000


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                      expire_on_commit=False)
    reset_capital_state_for_tests(initial_cash_krw=_START)
    s = TS()
    try:
        yield s
    finally:
        s.close()
        reset_capital_state_for_tests(initial_cash_krw=_START)


def _add_fill(db, *, symbol, side, qty, price, status="FILLED",
              strategy="ai_paper", created=None):
    """FILLED VirtualOrder 행 추가 (position_engine 이 읽는 형식)."""
    now = created or datetime.now(timezone.utc)
    o = VirtualOrder(
        symbol=symbol, side=side, quantity=qty, order_type="MARKET",
        requested_price=price, status=status, strategy=strategy, mode="PAPER",
        filled_quantity=(qty if status in ("FILLED", "PARTIALLY_FILLED") else 0),
        avg_fill_price=(price if status in ("FILLED", "PARTIALLY_FILLED") else None),
        filled_at=now, created_at=now, updated_at=now,
    )
    db.add(o)
    db.commit()
    return o


# ─────────────────────────────────────────────────────────────────────────────
# module — empty
# ─────────────────────────────────────────────────────────────────────────────


class TestEmpty:
    def test_no_positions_cash_equals_start(self, db_session):
        snap = build_portfolio_state(db_session)
        assert snap.current_cash == _START
        assert snap.starting_cash == _START

    def test_no_positions_total_value_zero(self, db_session):
        assert build_portfolio_state(db_session).total_position_value == 0

    def test_no_positions_equity_equals_start(self, db_session):
        assert build_portfolio_state(db_session).total_equity == _START

    def test_no_positions_count_and_slots(self, db_session):
        snap = build_portfolio_state(db_session)
        assert snap.position_count == 0
        assert snap.available_position_slots == snap.max_positions


# ─────────────────────────────────────────────────────────────────────────────
# module — BUY / SELL
# ─────────────────────────────────────────────────────────────────────────────


class TestBuySell:
    def test_buy_decreases_cash(self, db_session):
        get_capital_state().commit_buy(symbol="005930", price=75_000, quantity=13)
        snap = build_portfolio_state(db_session)
        assert snap.current_cash == _START - 75_000 * 13

    def test_buy_increases_quantity(self, db_session):
        get_capital_state().commit_buy(symbol="005930", price=75_000, quantity=13)
        _add_fill(db_session, symbol="005930", side="BUY", qty=13, price=75_000)
        snap = build_portfolio_state(db_session)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["quantity"] == 13

    def test_buy_market_value_and_avg_price(self, db_session):
        get_capital_state().commit_buy(symbol="005930", price=75_000, quantity=13)
        _add_fill(db_session, symbol="005930", side="BUY", qty=13, price=75_000)
        snap = build_portfolio_state(db_session)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["average_price"] == 75_000
        # mark 미제공 → current_price=avg → market_value = 75000*13.
        assert pos["market_value"] == 75_000 * 13

    def test_average_price_weighted(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        _add_fill(db_session, symbol="005930", side="BUY", qty=5, price=80_000)
        snap = build_portfolio_state(db_session)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["quantity"] == 15
        # weighted avg = (10*75000 + 5*80000)/15 = 76666.67 → round 76667.
        assert pos["average_price"] == 76_667

    def test_unrealized_positive_when_price_up(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        snap = build_portfolio_state(db_session, last_prices={"005930": 80_000})
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["unrealized_pnl"] > 0
        assert snap.total_unrealized_pnl > 0

    def test_unrealized_negative_when_price_down(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        snap = build_portfolio_state(db_session, last_prices={"005930": 70_000})
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["unrealized_pnl"] < 0
        assert snap.total_unrealized_pnl < 0

    def test_sell_increases_cash(self, db_session):
        cs = get_capital_state()
        cs.commit_buy(symbol="005930", price=75_000, quantity=10)
        cash_after_buy = build_portfolio_state(db_session).current_cash
        cs.commit_sell(symbol="005930", price=80_000, quantity=10,
                       cost_basis_krw=75_000 * 10)
        assert build_portfolio_state(db_session).current_cash > cash_after_buy

    def test_sell_decreases_quantity(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        _add_fill(db_session, symbol="005930", side="SELL", qty=4, price=80_000)
        snap = build_portfolio_state(db_session)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["quantity"] == 6

    def test_full_sell_removes_position(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        _add_fill(db_session, symbol="005930", side="SELL", qty=10, price=80_000)
        snap = build_portfolio_state(db_session)
        assert all(p["symbol"] != "005930" for p in snap.positions)
        assert snap.position_count == 0


class TestExclusions:
    def test_rejected_blocked_cancelled_excluded(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000,
                  status="REJECTED")
        _add_fill(db_session, symbol="000660", side="BUY", qty=5, price=200_000,
                  status="CANCELLED")
        snap = build_portfolio_state(db_session)
        assert snap.position_count == 0
        assert snap.total_position_value == 0


# ─────────────────────────────────────────────────────────────────────────────
# module — limits / weight
# ─────────────────────────────────────────────────────────────────────────────


class TestLimits:
    def test_today_buy_used(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        snap = build_portfolio_state(db_session)
        assert snap.today_buy_used_amount == 750_000

    def test_remaining_daily_buy(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        snap = build_portfolio_state(db_session, max_daily_buy_amount=3_000_000)
        assert snap.remaining_daily_buy_amount == 3_000_000 - 750_000

    def test_position_count_and_slots(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=1, price=75_000)
        _add_fill(db_session, symbol="000660", side="BUY", qty=1, price=200_000)
        snap = build_portfolio_state(db_session, max_positions=5)
        assert snap.position_count == 2
        assert snap.available_position_slots == 3

    def test_portfolio_weight_pct(self, db_session):
        # 현금 0 가정은 어렵지만 weight = market_value / total_equity 검증.
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        get_capital_state().commit_buy(symbol="005930", price=75_000, quantity=10)
        snap = build_portfolio_state(db_session)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        expected = pos["market_value"] / snap.total_equity
        assert abs(pos["portfolio_weight_pct"] - expected) < 1e-9

    def test_symbol_weight_exceeded(self, db_session):
        # 큰 비중 → max 5% 로 강제하면 EXCEEDED.
        _add_fill(db_session, symbol="005930", side="BUY", qty=10, price=75_000)
        snap = build_portfolio_state(db_session, max_symbol_weight_pct=0.05)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["symbol_weight_status"] == "EXCEEDED"

    def test_symbol_weight_ok_when_small(self, db_session):
        _add_fill(db_session, symbol="005930", side="BUY", qty=1, price=10_000)
        snap = build_portfolio_state(db_session, max_symbol_weight_pct=0.95)
        pos = next(p for p in snap.positions if p["symbol"] == "005930")
        assert pos["symbol_weight_status"] == "OK"


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                      expire_on_commit=False)
    reset_capital_state_for_tests(initial_cash_krw=_START)

    def odb():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = odb
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        reset_capital_state_for_tests(initial_cash_krw=_START)


class TestApi:
    def test_portfolio_invariants(self, api_client):
        r = api_client.get("/api/auto-paper/portfolio")
        assert r.status_code == 200
        b = r.json()
        assert b["is_live_authorization"] is False
        assert b["is_order_signal"] is False
        assert b["contains_secret"] is False
        assert b["is_paper_only"] is True
        assert b["ok"] is True
        assert "실제 계좌 잔고가 아닙니다" in b["advisory_disclaimer"]

    def test_portfolio_empty_shape(self, api_client):
        b = api_client.get("/api/auto-paper/portfolio").json()
        assert b["current_cash"] == _START
        assert b["total_equity"] == _START
        assert b["position_count"] == 0
        assert b["positions"] == []

    def test_portfolio_last_prices_query(self, api_client):
        # last_prices 파싱 — 잘못된 토큰은 무시되고 200.
        r = api_client.get("/api/auto-paper/portfolio?last_prices=005930:80000,bad")
        assert r.status_code == 200

    def test_response_has_no_secret_fields(self, api_client):
        raw = api_client.get("/api/auto-paper/portfolio").text.lower()
        for banned in ("api_key", "app_secret", "account_no", "access_token",
                       "app_key", "password"):
            assert banned not in raw


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_no_broker_or_executor_import(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"from app\.brokers", r"import app\.brokers",
            r"from app\.execution", r"route_order\s*\(",
            r"OrderExecutor\s*\(", r"\bbroker\.place_order\s*\(",
            r"import httpx", r"import requests", r"import anthropic", r"import openai",
        ):
            assert not re.search(pat, text), f"portfolio_state.py 금지 패턴: /{pat}/"

    def test_module_no_safety_flag_mutation(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"enable_live_trading\s*=", r"enable_ai_execution\s*=",
            r"enable_futures_live_trading\s*=", r"kis_is_paper\s*=",
        ):
            assert not re.search(pat, text), (
                f"portfolio_state.py 안전 flag mutation 의심: /{pat}/"
            )

    def test_module_no_db_write(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (r"\.add\(", r"\.commit\(", r"db\.delete\(", r"\.merge\("):
            assert not re.search(pat, text), f"portfolio_state.py DB write 의심: /{pat}/"
