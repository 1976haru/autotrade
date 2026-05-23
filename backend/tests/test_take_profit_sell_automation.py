"""3-04: 익절 SELL 자동화 E2E.

BUY 후 보유 종목 가격이 평단 대비 take_profit 목표수익선에 도달하면 Agent Council
이 SELL(TAKE_PROFIT) 을 만들고 → KisPaperAutoDecision(side=SELL, qty≤보유) → KIS
모의 SELL 주문(KIS_PAPER_SUBMITTED + broker_order_no) 까지 이어지는지 검증.

이익 실현 — 신규 숏 아님, 보유 없으면 SELL 금지, 실거래 0건.
stop_loss/take_profit 동시 성립(비정상)은 STOP_LOSS 우선.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.agent_council import (
    CouncilAction,
    StrategyMarketInput,
    run_agent_council,
)
from app.agents.position_context import (
    PositionContext,
    calculate_take_profit_price,
    infer_position_sell_reason,
    is_take_profit_triggered,
)
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.models import AgentDecisionLog, Base
from app.kis_paper.auto_executor import execute_kis_paper_auto_order
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
        kis_paper_auto_min_confidence=0.0, kis_paper_auto_min_quality_score=0,
        market_data_provider="mock",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _approved_sell_route(no="KIS-PAPER-SELL-TP-0001"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=31, broker_order_id=no, broker_status="FILLED",
                                filled_quantity=13, avg_fill_price=77250, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _market(**kw):
    base = dict(
        symbol="005930", current_price=77250, prev_close=75000, open_price=75000,
        vwap=76000, opening_range_high=76000, opening_range_low=75000,
        recent_closes=(75000, 75500, 76000, 76800, 77250),
        current_volume=120.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _held(**kw):
    base = dict(held_position=True, symbol="005930", quantity=13, available_quantity=13,
                average_entry_price=75000)
    base.update(kw)
    return PositionContext(**base)


def _run(db, *, decision, settings=None, route=None, credentials_present=True):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision, settings=settings or _settings(),
        broker=MockBrokerAdapter(), risk=object(), broker_is_kis_paper=True,
        credentials_present=credentials_present,
        route_order_fn=route or _approved_sell_route(), now=OPEN_TIME,
        chain_id="ep-tp-1"))


# ── 시나리오 A: BUY 후 익절 도달 → SELL E2E ──

def test_take_profit_pct_to_price_and_sell(db):
    # 평단 75,000 / take_profit_pct 3.0% → 익절가 77,250. 현재가 77,250 → TAKE_PROFIT SELL.
    pos = _held(current_price=77250, take_profit_pct=3.0)
    tp_price, src = calculate_take_profit_price(pos)
    assert tp_price == 77250.0 and src == "pct"
    council = run_agent_council(_market(current_price=77250), risk_profile="BALANCED",
                                position=pos)
    assert council.final_action == CouncilAction.SELL
    assert council.sell_reason["reason_code"] == "TAKE_PROFIT"
    assert council.held_position is True

    kd = council.to_kis_paper_decision(quantity=13, price=77250)
    assert kd.side == "SELL" and kd.quantity <= 13 and kd.is_short_entry is False
    r = _run(db, decision=kd)
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.side == "SELL"
    assert r.broker_order_no == "KIS-PAPER-SELL-TP-0001"
    assert r.broker_order_sent is True
    assert r.broker_order_type == "KIS_PAPER"
    assert r.is_live_authorization is False
    log = db.query(AgentDecisionLog).one()
    assert log.meta["sell_reason_code"] == "TAKE_PROFIT"
    assert log.meta["held_position"] is True
    assert log.meta["is_short_entry"] is False


def test_take_profit_price_above_triggers_sell():
    pos = _held(current_price=78000, take_profit_pct=3.0)   # 익절가 77250.
    d = run_agent_council(_market(current_price=78000), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "TAKE_PROFIT"


def test_absolute_take_profit_priority():
    pos = _held(current_price=77300, take_profit=77250, take_profit_pct=10.0)
    d = run_agent_council(_market(current_price=77300), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "TAKE_PROFIT"


def test_ratio_take_profit_pct_normalized():
    pos = _held(current_price=77250, take_profit_pct=0.03)   # 0.03 → 3% → 77250.
    assert calculate_take_profit_price(pos)[0] == 77250.0
    assert is_take_profit_triggered(pos)["triggered"] is True


# ── 시나리오 B: 익절선 미도달 ──

def test_below_take_profit_no_sell():
    pos = _held(current_price=76500, take_profit_pct=3.0)   # 익절가 77250, 현재 76500.
    d = run_agent_council(_market(current_price=76500), risk_profile="BALANCED", position=pos)
    assert d.sell_reason.get("reason_code") != "TAKE_PROFIT"


# ── 시나리오 C: 보유 없음 ──

def test_no_held_no_take_profit_sell():
    # 보유 없음: 익절선 위여도 TAKE_PROFIT SELL 금지 (강세장이면 신규 BUY 는 가능).
    pos = PositionContext(held_position=False, take_profit_pct=3.0,
                          average_entry_price=75000, current_price=78000)
    d = run_agent_council(_market(current_price=78000), risk_profile="BALANCED", position=pos)
    assert d.final_action != CouncilAction.SELL
    assert d.sell_reason.get("reason_code") != "TAKE_PROFIT"


# ── 시나리오 D/E: 수량 제한 ──

def test_take_profit_sell_quantity_capped():
    pos = _held(quantity=13, available_quantity=13, current_price=77250, take_profit_pct=3.0)
    d = run_agent_council(_market(current_price=77250), risk_profile="BALANCED", position=pos)
    kd = d.to_kis_paper_decision(quantity=100, price=77250)
    assert kd.quantity == 13


def test_take_profit_available_quantity_cap():
    pos = _held(quantity=13, available_quantity=8, current_price=77250, take_profit_pct=3.0)
    d = run_agent_council(_market(current_price=77250), risk_profile="BALANCED", position=pos)
    kd = d.to_kis_paper_decision(quantity=13, price=77250)
    assert kd.quantity == 8


# ── 시나리오 F: take_profit 미설정 / invalid ──

def test_take_profit_not_configured():
    pos = _held(current_price=80000)   # tp / pct 모두 없음.
    tp, src = calculate_take_profit_price(pos)
    assert tp is None and src == "TAKE_PROFIT_NOT_CONFIGURED"
    assert infer_position_sell_reason(pos) is None


def test_invalid_take_profit_pct_no_crash():
    for bad in (-1.0, 0.0, None):
        pos = _held(current_price=80000, take_profit_pct=bad)
        assert calculate_take_profit_price(pos)[0] is None


# ── 시나리오 G: stop_loss/take_profit 동시 → STOP_LOSS 우선 ──

def test_stop_loss_priority_over_take_profit():
    # 비정상: 둘 다 성립 (current == sl == tp). STOP_LOSS 우선.
    pos = _held(current_price=77250, stop_loss=77250, take_profit=77250)
    assert infer_position_sell_reason(pos) == "STOP_LOSS"
    d = run_agent_council(_market(current_price=77250), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "STOP_LOSS"


# ── readiness 차단 ──

def test_readiness_blocked_no_tp_sell_order(db):
    pos = _held(current_price=77250, take_profit_pct=3.0)
    council = run_agent_council(_market(current_price=77250), risk_profile="BALANCED",
                                position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=77250)
    r = _run(db, decision=kd, credentials_present=False)
    db.commit()
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False


# ── ledger / order_quality / secret ──

def test_tp_ledger_quality_no_secret(db, monkeypatch):
    import json

    import app.kis_paper.auto_executor as ax
    from app.kis_paper.order_quality import build_order_quality_log
    calls = []
    monkeypatch.setattr(ax, "record_paper_event",
                        lambda **kw: calls.append(kw) or SimpleNamespace())
    pos = _held(current_price=77250, take_profit_pct=3.0)
    council = run_agent_council(_market(current_price=77250), risk_profile="BALANCED",
                                position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=77250)
    r = _run(db, decision=kd)
    db.commit()
    assert len(calls) >= 1
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["broker_order_no"] == "KIS-PAPER-SELL-TP-0001"
    assert q["is_live_authorization"] is False
    log = db.query(AgentDecisionLog).one()
    flat = json.dumps(log.meta).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in flat
