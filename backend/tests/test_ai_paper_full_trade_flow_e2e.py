"""AI Paper 모의매매 *전체 흐름* E2E — VirtualOrder + 체결 + 포트폴리오.

run-once 판단을 실제 Paper 체결까지 연결하는 `execute_paper_trade_flow` +
`POST /api/auto-paper/run-once-trade` 검증.

성공 흐름: 시드 10,000,000 / 종목당 1,000,000 / 005930 @ 75,000 → quantity=13 /
notional=975,000 / VirtualOrder FILLED / cash 9,025,000 / position 13주 /
AgentDecisionLog 기록 / broker·live 호출 0회.

차단 흐름: 고가주 / 현금부족 / no-signal / stale / 중복보유 → 주문 미생성 +
decision log 기록.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.capital_state import (
    get_capital_state,
    reset_capital_state_for_tests,
)
from app.auto_paper.ledger import get_ledger, reset_ledger_for_tests
from app.auto_paper.paper_trade_flow import (
    PaperTradeFlowResult,
    execute_paper_trade_flow,
)
from app.db.base import Base
from app.db.models import AgentDecisionLog, VirtualOrder
from app.virtual.position_engine import compute_open_positions


_FLOW_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "paper_trade_flow.py"
)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean_state():
    reset_capital_state_for_tests(10_000_000)
    reset_ledger_for_tests()
    yield
    reset_capital_state_for_tests(10_000_000)
    reset_ledger_for_tests()


# ── 성공 흐름 ─────────────────────────────────────────────────────────────────


def test_successful_paper_trade_quantity_13(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    assert r.reason_code == "VIRTUAL_ORDER_CANDIDATE_CREATED"
    assert r.order_created is True
    assert r.filled is True
    assert r.quantity == 13
    assert r.notional_krw == 975_000
    assert r.filled_quantity == 13
    assert r.fill_price == 75_000
    assert r.fill_source == "PAPER_SIMULATOR"
    assert r.broker_order_sent is False
    assert r.is_live_authorization is False


def test_cash_reflected_10m_to_9_025m(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    assert r.cash_before == 10_000_000
    assert r.cash_after == 9_025_000
    assert get_capital_state().snapshot().available_cash_krw == 9_025_000


def test_position_13_shares_reflected(db):
    execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    positions = compute_open_positions(db)
    assert len(positions) == 1
    assert positions[0].symbol == "005930"
    assert positions[0].quantity == 13
    assert positions[0].avg_price == 75_000


def test_virtual_order_filled_in_db(db):
    execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    orders = db.query(VirtualOrder).all()
    assert len(orders) == 1
    o = orders[0]
    assert o.status == "FILLED"
    assert o.side == "BUY"
    assert o.filled_quantity == 13
    assert o.avg_fill_price == 75_000
    assert o.mode == "PAPER"
    assert o.filled_at is not None


def test_agent_decision_log_recorded(db):
    execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    logs = db.query(AgentDecisionLog).all()
    assert len(logs) == 1
    assert logs[0].decision == "BUY"
    assert logs[0].mode == "PAPER"
    assert logs[0].meta.get("broker_order_sent") is False
    assert logs[0].meta.get("is_live_authorization") is False


def test_ledger_records_filled_event(db):
    execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    events = get_ledger().recent(limit=5)
    assert any(e.decision_action.value == "BUY"
               and e.paper_fill_status.value == "PAPER_FILLED" for e in events)


# ── dry-run / 체결 비활성 ──────────────────────────────────────────────────────


def test_dry_run_does_not_create_order(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=True,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    assert r.order_created is False
    assert db.query(VirtualOrder).count() == 0
    # 판단 기록은 남음.
    assert db.query(AgentDecisionLog).count() == 1


def test_allow_fills_false_does_not_create_order(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=False, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    assert r.order_created is False
    assert db.query(VirtualOrder).count() == 0
    assert get_capital_state().snapshot().available_cash_krw == 10_000_000


# ── 차단 흐름 (주문 미생성 + decision log 기록) ────────────────────────────────


def _assert_blocked(db, r, expected_code):
    assert r.reason_code == expected_code
    assert r.order_created is False
    assert r.filled is False
    assert db.query(VirtualOrder).count() == 0
    assert db.query(AgentDecisionLog).count() == 1   # 기록은 남음.
    assert get_capital_state().snapshot().available_cash_krw == 10_000_000


def test_blocked_high_price(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=2_000_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    _assert_blocked(db, r, "MIN_LOT_NOT_AFFORDABLE")


def test_blocked_insufficient_cash(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=100_000,
    )
    db.commit()
    # cash override 100,000 — capital_state 는 10,000,000 이므로 cash 변화 0 확인.
    assert r.reason_code == "INSUFFICIENT_PAPER_CASH"
    assert r.order_created is False
    assert db.query(VirtualOrder).count() == 0


def test_blocked_no_signal(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, signal_present=False, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    _assert_blocked(db, r, "NO_STRATEGY_SIGNAL")


def test_blocked_stale_price(db):
    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, price_timestamp=old, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    _assert_blocked(db, r, "PRICE_STALE")


def test_blocked_duplicate_position(db):
    # 첫 매수 → 체결, 두 번째 동일 종목 → DUPLICATE.
    r1 = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    assert r1.filled is True
    r2 = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
    )
    db.commit()
    assert r2.reason_code == "DUPLICATE_POSITION_BUY_BLOCKED"
    assert r2.order_created is False
    assert db.query(VirtualOrder).count() == 1   # 두 번째는 미생성.


def test_blocked_by_risk_manager(db):
    r = execute_paper_trade_flow(
        db, symbol="005930", price=75_000, dry_run=False,
        allow_simulated_fills=True, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000,
        risk_check=lambda s, side, q, p: (False, "risk policy breach"),
    )
    db.commit()
    assert r.reason_code == "BLOCKED_BY_RISK_MANAGER"
    assert r.order_created is False
    assert db.query(VirtualOrder).count() == 0


# ── invariants / 정적 가드 ────────────────────────────────────────────────────


def test_result_invariants():
    with pytest.raises(ValueError):
        PaperTradeFlowResult(
            reason_code="X", reason_message="x", order_created=False,
            filled=False, symbol=None, side="BUY", quantity=0, price=None,
            notional_krw=0, dry_run=True, run_once_result_code="X",
            broker_order_sent=True,
        )


def test_static_no_broker_or_executor_imports():
    src = _FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = (
        "app.brokers.kis", "app.brokers.mock_broker",
        "app.execution.executor", "app.execution.order_router",
        "anthropic", "openai", "httpx", "requests",
    )
    for mod in imported:
        for bad in forbidden:
            assert bad not in (mod or ""), f"forbidden import: {mod}"
    assert "broker.place_order(" not in src
    assert "route_order(" not in src
    assert "OrderExecutor(" not in src
    assert ".place_order(" not in src


# ── API endpoint ─────────────────────────────────────────────────────────────


def test_api_run_once_trade_creates_filled_order(client):
    res = client.post("/api/auto-paper/run-once-trade", json={
        "symbol": "005930", "price": 75_000,
        "per_symbol_cap_krw": 1_000_000, "available_cash_krw": 10_000_000,
        "dry_run": False, "allow_simulated_fills": True,
    })
    assert res.status_code == 200
    j = res.json()
    assert j["reason_code"] == "VIRTUAL_ORDER_CANDIDATE_CREATED"
    assert j["order_created"] is True
    assert j["filled"] is True
    assert j["quantity"] == 13
    assert j["fill_price"] == 75_000
    assert j["broker_order_sent"] is False
    assert j["is_live_authorization"] is False
    # VirtualOrder 가 ledger API 에도 보여야 함 (/api/virtual/orders 는 list 반환).
    orders = client.get("/api/virtual/orders").json()
    order_list = orders if isinstance(orders, list) else orders.get("orders", [])
    assert any(o["symbol"] == "005930" and o["status"] == "FILLED"
               for o in order_list)


def test_api_run_once_trade_dry_run_default_no_order(client):
    # body 없이 → .env 기본값(dry_run=true, allow_fills=false) → 주문 미생성.
    res = client.post("/api/auto-paper/run-once-trade", json={
        "symbol": "005930", "price": 75_000,
        "per_symbol_cap_krw": 1_000_000, "available_cash_krw": 10_000_000,
    })
    assert res.status_code == 200
    j = res.json()
    assert j["order_created"] is False
    assert j["broker_order_sent"] is False


def test_api_run_once_trade_high_price_blocked(client):
    res = client.post("/api/auto-paper/run-once-trade", json={
        "symbol": "005930", "price": 2_000_000,
        "per_symbol_cap_krw": 1_000_000, "available_cash_krw": 10_000_000,
        "dry_run": False, "allow_simulated_fills": True,
    })
    assert res.status_code == 200
    assert res.json()["reason_code"] == "MIN_LOT_NOT_AFFORDABLE"
    assert res.json()["order_created"] is False
