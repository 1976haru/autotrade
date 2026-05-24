"""3-09: 거래 없음(no-trade) 사유 누적 검증.

자동매매 tick/cycle 에서 *실제 주문이 발생하지 않은* 경우에도 "왜 거래가
없었는지" 를 누적 기록·집계하는지 검증. NO_SIGNAL / NO_MARKET_DATA /
MARKET_CLOSED / PRICE_STALE / BLOCKED_BY_* / EXIT_PLAN_* / RISK_FLAGS_EXCEEDED /
buy block reason 이 정규화·집계되고, 거래 없음 cycle 도 cycle_count 에 누적되며,
broker_order_no 없음 / broker_order_sent=False / KIS_PAPER_SUBMITTED 미발생이
보장되는지 확인. 실거래 0건 — 본 모듈은 read-only 집계만 수행.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.auto_paper.no_trade_reasons import (
    NoTradeSummary,
    classify_no_trade_event,
    normalize_no_trade_reason_code,
    summarize_no_trade_reasons,
    title_for,
)

_MODULE = pathlib.Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "no_trade_reasons.py"


# ── event 생성 헬퍼 (PaperLoopEvent.to_dict() 형태) ──

def _ev(
    *,
    action="HOLD",
    fill="NA",
    symbol="005930",
    strategy="sma_crossover",
    reason="",
    reason_code=None,
    risk_flags=None,
    ts="2026-05-23T04:00:00+00:00",
):
    meta = {}
    if reason_code is not None:
        meta["reason_code"] = reason_code
    return {
        "event_id": f"ev-{symbol}-{action}-{ts}",
        "timestamp": ts,
        "loop_state": "RUNNING",
        "strategy": strategy,
        "symbol": symbol,
        "decision_action": action,
        "confidence": 0.5,
        "reason": reason,
        "risk_flags": list(risk_flags or []),
        "paper_order_id": None,
        "paper_fill_status": fill,
        "virtual_position_delta": 0,
        "pnl_estimate": 0.0,
        "metadata": meta,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "is_live_authorization": False,
    }


def _filled(**kw):
    e = _ev(action="BUY", fill="PAPER_FILLED", **kw)
    e["paper_order_id"] = "paper-001"
    e["virtual_position_delta"] = 10
    return e


# ── 1~3. HOLD cycle 기록 + broker 미발생 ──

def test_hold_cycle_recorded():
    s = summarize_no_trade_reasons([_ev(action="HOLD")])
    assert s.no_trade_count == 1
    assert s.last_no_trade is not None


def test_hold_cycle_no_broker_order_sent():
    rec = classify_no_trade_event(_ev(action="HOLD"))
    assert rec["broker_order_sent"] is False


def test_hold_cycle_no_broker_order_no():
    rec = classify_no_trade_event(_ev(action="HOLD"))
    assert rec["broker_order_no"] is None


# ── 4~12. 각 reason_code 저장 ──

@pytest.mark.parametrize("code", [
    "NO_SIGNAL", "NO_STRATEGY_SIGNAL", "NO_MARKET_DATA", "MARKET_CLOSED",
    "PRICE_STALE", "INVALID_PRICE", "BLOCKED_BY_RISK_MANAGER",
    "BLOCKED_BY_PERMISSION_GATE", "BLOCKED_BY_KIS_READINESS",
    "PAPER_EXECUTION_DISABLED", "EXIT_PLAN_MISSING", "EXIT_PLAN_INVALID",
    "RISK_FLAGS_EXCEEDED",
])
def test_each_reason_code_stored(code):
    s = summarize_no_trade_reasons([_ev(action="HOLD", reason_code=code)])
    assert s.by_reason.get(code) == 1
    assert s.last_no_trade_reason == code
    assert title_for(code) and "거래" in title_for(code) or "차단" in title_for(code) or "HOLD" in title_for(code)


def test_no_signal_from_bare_hold():
    # reason_code 없이 HOLD → NO_SIGNAL 로 fallback.
    s = summarize_no_trade_reasons([_ev(action="HOLD", reason="trend not confirmed")])
    assert s.by_reason.get("NO_SIGNAL") == 1


def test_no_op_classified_as_no_signal():
    s = summarize_no_trade_reasons([_ev(action="NO_OP")])
    assert s.by_reason.get("NO_SIGNAL") == 1


# ── 13. buy block reason carry ──

def test_duplicate_position_buy_blocked_stored():
    s = summarize_no_trade_reasons([
        _ev(action="HOLD", reason_code="DUPLICATE_POSITION_BUY_BLOCKED"),
    ])
    assert s.by_reason.get("DUPLICATE_POSITION_BUY_BLOCKED") == 1


def test_kis_paper_alias_normalized():
    # KIS gate sentinel → canonical code.
    s = summarize_no_trade_reasons([
        _ev(action="HOLD", reason_code="KIS_PAPER_ORDER_LIMIT_EXCEEDED"),
    ])
    assert s.by_reason.get("DAILY_ORDER_LIMIT_EXCEEDED") == 1


def test_risk_flags_from_risk_flags_field():
    # metadata.reason_code 없이 risk_flags[0] 에서 추출.
    s = summarize_no_trade_reasons([_ev(action="HOLD", risk_flags=["RISK_FLAGS_EXCEED"])])
    assert s.by_reason.get("RISK_FLAGS_EXCEEDED") == 1


# ── 14~17. cycle_count / no_trade_count / by_reason / last ──

def test_cycle_count_increases_without_orders():
    events = [_ev(action="HOLD") for _ in range(5)]
    s = summarize_no_trade_reasons(events)
    assert s.cycle_count == 5
    assert s.order_count == 0
    assert s.no_trade_count == 5


def test_cycle_count_orders_plus_no_trade():
    events = [_filled(), _ev(action="HOLD"), _ev(action="HOLD")]
    s = summarize_no_trade_reasons(events)
    assert s.order_count == 1
    assert s.no_trade_count == 2
    assert s.cycle_count == 3


def test_by_reason_aggregation():
    events = [
        _ev(action="HOLD", reason_code="NO_SIGNAL"),
        _ev(action="HOLD", reason_code="NO_SIGNAL"),
        _ev(action="HOLD", reason_code="MARKET_CLOSED"),
        _ev(action="HOLD", reason_code="BLOCKED_BY_RISK_MANAGER"),
    ]
    s = summarize_no_trade_reasons(events)
    assert s.by_reason == {
        "NO_SIGNAL": 2, "MARKET_CLOSED": 1, "BLOCKED_BY_RISK_MANAGER": 1,
    }


def test_last_no_trade_reason_is_most_recent():
    events = [
        _ev(action="HOLD", reason_code="NO_SIGNAL", ts="2026-05-23T04:00:00+00:00"),
        _ev(action="HOLD", reason_code="MARKET_CLOSED", ts="2026-05-23T04:01:00+00:00"),
    ]
    s = summarize_no_trade_reasons(events)
    assert s.last_no_trade_reason == "MARKET_CLOSED"
    assert s.last_no_trade_symbol == "005930"


# ── 18~20. filled 거래는 no-trade 아님 + invariant ──

def test_filled_order_not_counted_as_no_trade():
    s = summarize_no_trade_reasons([_filled(), _filled()])
    assert s.order_count == 2
    assert s.no_trade_count == 0


def test_paper_rejected_counted_with_reason():
    e = _ev(action="BUY", fill="PAPER_REJECTED", reason_code="BLOCKED_BY_RISK_MANAGER")
    s = summarize_no_trade_reasons([e])
    assert s.no_trade_count == 1
    assert s.by_reason.get("BLOCKED_BY_RISK_MANAGER") == 1


def test_paper_rejected_unknown_reason_kept_unknown():
    e = _ev(action="BUY", fill="PAPER_REJECTED", reason="opaque")
    rec = classify_no_trade_event(e)
    assert rec["reason_code"] == "UNKNOWN"


# ── 21. KIS_PAPER_SUBMITTED 미발생 ──

def test_no_kis_paper_submitted_in_records():
    events = [_ev(action="HOLD", reason_code="NO_SIGNAL"), _filled()]
    s = summarize_no_trade_reasons(events)
    for rec in s.recent:
        assert rec["reason_code"] != "KIS_PAPER_SUBMITTED"
        assert rec.get("broker_order_no") is None
        assert rec.get("broker_order_sent") is False


# ── 22. summary invariant ──

def test_summary_invariants_locked():
    s = summarize_no_trade_reasons([_ev(action="HOLD")])
    d = s.to_dict()
    assert d["is_order_signal"] is False
    assert d["is_live_authorization"] is False
    assert d["auto_apply_allowed"] is False
    assert d["contains_secret"] is False
    # spec §6 alias 필드.
    assert d["total_no_trade"] == d["no_trade_count"]
    assert d["by_no_trade_reason"] == d["by_reason"]


def test_invariant_cannot_be_flipped():
    with pytest.raises(ValueError):
        NoTradeSummary(
            cycle_count=1, order_count=0, no_trade_count=1,
            by_reason={}, recent=[], is_order_signal=True,
        )
    with pytest.raises(ValueError):
        NoTradeSummary(
            cycle_count=1, order_count=0, no_trade_count=1,
            by_reason={}, recent=[], contains_secret=True,
        )


# ── 23. 빈 입력 / None 안전 ──

def test_empty_and_none_safe():
    for inp in (None, []):
        s = summarize_no_trade_reasons(inp)
        assert s.cycle_count == 0
        assert s.no_trade_count == 0
        assert s.last_no_trade is None


def test_recent_limit_respected():
    events = [_ev(action="HOLD", reason_code="NO_SIGNAL") for _ in range(20)]
    s = summarize_no_trade_reasons(events, limit=3)
    assert len(s.recent) == 3
    assert s.no_trade_count == 20


# ── 24. normalize 동작 ──

def test_normalize_unknown_safe():
    assert normalize_no_trade_reason_code("SOMETHING_WEIRD") == "UNKNOWN"
    assert normalize_no_trade_reason_code(None) == "UNKNOWN"
    assert normalize_no_trade_reason_code("") == "UNKNOWN"


def test_normalize_hold_to_no_signal():
    assert normalize_no_trade_reason_code("HOLD") == "NO_SIGNAL"


# ── 25. 정적 invariant: broker / OrderExecutor / route_order import 0건 ──

def test_module_imports_no_broker_or_executor():
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    forbidden = (
        "app.brokers.kis", "app.brokers.mock_broker", "app.execution.executor",
        "app.execution.order_router", "app.execution.order_executor",
        "anthropic", "openai", "httpx", "requests",
    )
    for mod in imported:
        for bad in forbidden:
            assert bad not in mod, f"forbidden import: {mod}"


def test_module_no_db_write_or_place_order():
    src = _MODULE.read_text(encoding="utf-8")
    for bad in ("place_order", "route_order(", "db.add(", "db.commit(",
                "session.add(", "OrderRequest"):
        assert bad not in src, f"forbidden token in module: {bad}"


def test_no_secret_like_field_in_record():
    rec = classify_no_trade_event(_ev(action="HOLD", reason_code="NO_SIGNAL"))
    keys = " ".join(rec.keys()).lower()
    for bad in ("app_secret", "account_no", "api_key", "access_token"):
        assert bad not in keys


# ── 26. API endpoint (read-only) ──

@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.auto_paper.ledger import reset_ledger_for_tests
    from app.db.base import Base
    from app.db.session import get_db
    from app.main import app

    reset_ledger_for_tests()
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    TestSession = sessionmaker(bind=eng, autoflush=False, autocommit=False,
                               expire_on_commit=False)

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        reset_ledger_for_tests()


def _seed_no_trade():
    from app.auto_paper.events import DecisionAction, PaperFillStatus
    from app.auto_paper.ledger import record_paper_event
    # HOLD cycles (no reason_code → NO_SIGNAL) + explicit codes via risk_flags.
    record_paper_event(loop_state="RUNNING", strategy="sma", symbol="005930",
                       decision_action=DecisionAction.HOLD, reason="no signal yet")
    record_paper_event(loop_state="RUNNING", strategy="sma", symbol="000660",
                       decision_action=DecisionAction.HOLD, reason="no signal yet")
    record_paper_event(loop_state="RUNNING", strategy="sma", symbol="035720",
                       decision_action=DecisionAction.BUY,
                       reason="[paper-flow] BLOCKED_BY_RISK_MANAGER: blocked",
                       risk_flags=["BLOCKED_BY_RISK_MANAGER"],
                       paper_fill_status=PaperFillStatus.PAPER_REJECTED)
    # filled order — not a no-trade.
    record_paper_event(loop_state="RUNNING", strategy="sma", symbol="051910",
                       decision_action=DecisionAction.BUY, reason="filled",
                       paper_order_id="paper-x", virtual_position_delta=10,
                       paper_fill_status=PaperFillStatus.PAPER_FILLED)


class TestNoTradeApi:
    def test_empty(self, api_client):
        r = api_client.get("/api/auto-paper/no-trade-reasons/today")
        assert r.status_code == 200
        b = r.json()
        assert b["no_trade_count"] == 0
        assert b["cycle_count"] == 0
        assert b["is_order_signal"] is False
        assert b["is_live_authorization"] is False
        assert b["contains_secret"] is False

    def test_after_seed(self, api_client):
        _seed_no_trade()
        r = api_client.get("/api/auto-paper/no-trade-reasons/today?limit=10")
        assert r.status_code == 200
        b = r.json()
        assert b["no_trade_count"] == 3            # 2 HOLD + 1 rejected.
        assert b["order_count"] == 1               # 1 filled.
        assert b["cycle_count"] == 4
        assert b["by_reason"]["NO_SIGNAL"] == 2
        assert b["by_reason"]["BLOCKED_BY_RISK_MANAGER"] == 1
        assert b["total_no_trade"] == 3            # alias.

    def test_no_secret_fields(self, api_client):
        _seed_no_trade()
        raw = api_client.get("/api/auto-paper/no-trade-reasons/today").text.lower()
        for banned in ("app_secret", "api_key", "account_no", "access_token"):
            assert banned not in raw

    def test_disclaimer_and_scope(self, api_client):
        r = api_client.get("/api/auto-paper/no-trade-reasons/today")
        b = r.json()
        assert "거래" in b["advisory_disclaimer"]
        assert b["scope"] == "today"
        # KIS_PAPER_SUBMITTED 미발생 + 성공 주문처럼 표시 0건.
        assert "KIS_PAPER_SUBMITTED" not in r.text
