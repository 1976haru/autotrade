"""3-01: KIS 모의투자 BUY 주문 E2E 확인.

Agent Council BUY → KisPaperAutoDecision → execute_kis_paper_auto_order →
(fake sanctioned route_order) → KIS_PAPER_SUBMITTED + broker_order_no +
broker_order_sent=True + AgentDecisionLog meta(votes/selected_strategies) 연결.
readiness/exit_plan-invalid/risk-veto 시 주문 차단.

실거래 0건: KIS_IS_PAPER=true + ENABLE_LIVE_TRADING=false + broker_order_type=
KIS_PAPER + is_live_authorization=false. route_order 는 fake (실제 KIS API 0건).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.agent_council import StrategyMarketInput, run_agent_council
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.models import AgentDecisionLog, Base
from app.kis_paper.auto_executor import (
    build_kis_paper_decision_from_council,
    execute_kis_paper_auto_order,
)
from app.risk.risk_manager import RiskDecision

_KST = timezone(timedelta(hours=9))
OPEN_TIME = datetime(2026, 5, 22, 10, 0, tzinfo=_KST).astimezone(timezone.utc)


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _settings(**kw):
    base = dict(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=100_000_000,
        kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.4, kis_paper_auto_min_quality_score=40,
        market_data_provider="mock",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _approved_route(broker_order_no="KIS-PAPER-BUY-0001"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=11, broker_order_id=broker_order_no,
                                broker_status="FILLED", filled_quantity=10,
                                avg_fill_price=1100, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _strong_buy_input(**kw):
    base = dict(
        symbol="005930", current_price=1100, prev_close=1000, open_price=1010,
        vwap=1000, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 1030, 1060, 1090, 1100),
        current_volume=150.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _run(db, *, decision, settings=None, route=None, **kw):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision, settings=settings or _settings(),
        broker=MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route or _approved_route(), now=OPEN_TIME,
        chain_id="ep-e2e-1", **kw,
    ))


# ── 전체 E2E: council BUY → KIS_PAPER_SUBMITTED ──

def test_council_buy_e2e_submitted(db):
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    assert council.final_action.value == "BUY"
    assert council.exit_plan_validation["valid"] is True            # exit_plan valid.
    assert council.risk_veto_result["veto_applied"] is False        # risk_veto 미적용.

    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    assert kd is not None and kd.side == "BUY" and kd.symbol == "005930"
    assert kd.quantity == 10 and kd.price == 1100
    assert kd.selected_strategies                                   # 전달됨.
    assert 0.0 <= kd.confidence <= 1.0
    assert kd.quality_score >= 0

    r = _run(db, decision=kd)
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.submitted is True
    assert r.broker_order_no == "KIS-PAPER-BUY-0001"
    assert r.broker_order_sent is True
    assert r.broker_order_type == "KIS_PAPER"
    assert r.is_live_authorization is False
    assert r.side == "BUY"


def test_e2e_agent_decision_log_carries_vote_meta(db):
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    _run(db, decision=kd)
    db.commit()
    log = db.query(AgentDecisionLog).one()
    assert log.meta["reason_code"] == "KIS_PAPER_SUBMITTED"
    assert log.meta["broker_order_type"] == "KIS_PAPER"
    assert log.meta["broker_order_no"] == "KIS-PAPER-BUY-0001"
    assert log.meta["broker_order_sent"] is True
    assert log.meta["order_created"] is True
    assert log.meta["is_live_authorization"] is False
    assert log.meta["final_action"] == "BUY"
    assert len(log.meta["votes"]) == 4                              # 4전략 vote 보존.
    assert log.meta["selected_strategies"]
    assert log.meta["risk_profile"] == "AGGRESSIVE"
    assert log.meta["exit_plan_validation"]["valid"] is True


def test_e2e_order_quality_buildable(db):
    # order_quality(P-24) 가 결과로부터 산출 가능한지 — broker_order_no/FILLED.
    from app.kis_paper.order_quality import build_order_quality_log
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    r = _run(db, decision=kd)
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["broker_order_no"] == "KIS-PAPER-BUY-0001"
    assert q["order_status"] in ("FILLED", "PARTIALLY_FILLED")
    assert q["is_live_authorization"] is False


def test_e2e_ledger_recorded(db, monkeypatch):
    # 주문 결과가 paper ledger(record_paper_event)에 기록되는지 — 호출 spy.
    calls = []
    import app.kis_paper.auto_executor as ax

    def _spy(**kw):
        calls.append(kw)
        return SimpleNamespace()

    monkeypatch.setattr(ax, "record_paper_event", _spy)
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    _run(db, decision=kd)
    db.commit()
    assert len(calls) >= 1
    assert calls[0]["metadata"]["broker_order_type"] == "KIS_PAPER"


# ── 안전 차단 ──

def test_readiness_blocked_no_order(db):
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    called = {"n": 0}

    async def _route(**kw):
        called["n"] += 1
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                               audit=SimpleNamespace(id=1))

    # credentials 미설정 → 게이트 차단 → route_order 호출 0건.
    r = asyncio.run(execute_kis_paper_auto_order(
        db, decision=kd, settings=_settings(), broker=MockBrokerAdapter(),
        risk=object(), broker_is_kis_paper=True, credentials_present=False,
        route_order_fn=_route, now=OPEN_TIME, chain_id="ep-block"))
    db.commit()
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False
    assert r.broker_order_no is None
    assert called["n"] == 0


def test_auto_trading_disabled_no_order(db):
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    r = _run(db, decision=kd, settings=_settings(enable_kis_paper_auto_trading=False))
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False


def test_exit_plan_invalid_no_decision_no_order():
    # exit_plan 생성 실패(no price) → council HOLD → 변환 None → 주문 0건.
    council = run_agent_council(_strong_buy_input(current_price=None), risk_profile="AGGRESSIVE")
    assert council.final_action.value == "HOLD"
    assert council.exit_plan_validation.get("valid") is False
    assert build_kis_paper_decision_from_council(council, quantity=10, price=0) is None


def test_risk_veto_no_decision_no_order():
    # 2 flags + BALANCED → veto → HOLD → 변환 None → 주문 0건.
    council = run_agent_council(
        _strong_buy_input(current_volume=5.0, market_regime="HIGH_VOLATILITY"),
        risk_profile="BALANCED")
    assert council.final_action.value == "HOLD"
    assert council.risk_veto_result["veto_applied"] is True
    assert build_kis_paper_decision_from_council(council, quantity=10, price=1100) is None


# ── 실거래 / secret 안전 ──

def test_no_live_authorization_and_paper_only(db):
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    r = _run(db, decision=kd)
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["broker_order_type"] == "KIS_PAPER"


def test_no_secret_in_decision_log_meta(db):
    import json
    council = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(council, quantity=10, price=1100)
    _run(db, decision=kd)
    db.commit()
    log = db.query(AgentDecisionLog).one()
    flat = json.dumps(log.meta).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in flat
