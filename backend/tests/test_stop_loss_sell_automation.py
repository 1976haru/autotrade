"""3-03: 손절 SELL 자동화 E2E.

BUY 후 보유 종목 가격이 평단 대비 stop_loss 손절선에 도달하면 Agent Council 이
SELL(STOP_LOSS) 을 만들고 → KisPaperAutoDecision(side=SELL, qty≤보유) → KIS 모의
SELL 주문(KIS_PAPER_SUBMITTED + broker_order_no) 까지 이어지는지 검증.

손실 확대 방지 — 신규 숏 아님, 보유 없으면 SELL 금지, 실거래 0건.
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
from app.agents.position_context import PositionContext
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


def _approved_sell_route(no="KIS-PAPER-SELL-0001"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=21, broker_order_id=no, broker_status="FILLED",
                                filled_quantity=13, avg_fill_price=73500, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _market(**kw):
    base = dict(
        symbol="005930", current_price=73500, prev_close=75000, open_price=75000,
        vwap=74000, opening_range_high=75500, opening_range_low=74500,
        recent_closes=(75000, 74500, 74000, 73800, 73500),
        current_volume=120.0, avg_volume=100.0,
        market_regime="SIDEWAYS", regime_decision="ALLOW",
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
        chain_id="ep-sl-1"))


# ── 시나리오 A: BUY 후 손절 도달 → SELL E2E ──

def test_stop_loss_pct_to_price_and_sell(db):
    # 평단 75,000 / stop_loss_pct 2.0% → 손절가 73,500. 현재가 73,500 → STOP_LOSS SELL.
    pos = _held(current_price=73500, stop_loss_pct=2.0)
    sl_price, src = pos.resolve_stop_loss_price()
    assert sl_price == 73500.0 and src == "pct"
    council = run_agent_council(_market(current_price=73500), risk_profile="BALANCED",
                                position=pos)
    assert council.final_action == CouncilAction.SELL
    assert council.sell_reason["reason_code"] == "STOP_LOSS"
    assert council.held_position is True

    kd = council.to_kis_paper_decision(quantity=13, price=73500)
    assert kd.side == "SELL" and kd.quantity <= 13 and kd.is_short_entry is False
    r = _run(db, decision=kd)
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.side == "SELL"
    assert r.broker_order_no == "KIS-PAPER-SELL-0001"
    assert r.broker_order_sent is True
    assert r.broker_order_type == "KIS_PAPER"
    assert r.is_live_authorization is False
    log = db.query(AgentDecisionLog).one()
    assert log.meta["sell_reason_code"] == "STOP_LOSS"
    assert log.meta["held_position"] is True
    assert log.meta["is_short_entry"] is False
    assert log.meta["broker_order_type"] == "KIS_PAPER"


def test_stop_loss_price_below_triggers_sell():
    # 현재가가 손절가보다 더 낮아도 SELL.
    pos = _held(current_price=73000, stop_loss_pct=2.0)   # 손절가 73500.
    d = run_agent_council(_market(current_price=73000), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "STOP_LOSS"


def test_absolute_stop_loss_priority():
    pos = _held(current_price=73600, stop_loss=73500, stop_loss_pct=5.0)
    # 절대 손절가 73500 우선 — 73600 > 73500 → 미도달 → SELL 아님.
    d = run_agent_council(_market(current_price=73600), risk_profile="BALANCED", position=pos)
    assert d.final_action != CouncilAction.SELL or d.sell_reason.get("reason_code") != "STOP_LOSS"


# ── 시나리오 B: 손절선 미도달 ──

def test_above_stop_loss_no_sell():
    pos = _held(current_price=74000, stop_loss_pct=2.0)   # 손절가 73500, 현재 74000.
    d = run_agent_council(_market(current_price=74000), risk_profile="BALANCED", position=pos)
    assert d.sell_reason.get("reason_code") != "STOP_LOSS"
    # 손절 트리거 없음 — SELL(STOP_LOSS) 미생성.


# ── 시나리오 C: 보유 없음 ──

def test_no_held_no_stop_loss_sell():
    pos = PositionContext(held_position=False, stop_loss_pct=2.0,
                          average_entry_price=75000, current_price=73000)
    d = run_agent_council(_market(current_price=73000), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.HOLD


# ── 시나리오 D: 수량 초과 방지 ──

def test_sell_quantity_capped(db):
    pos = _held(quantity=13, available_quantity=13, current_price=73500, stop_loss_pct=2.0)
    d = run_agent_council(_market(current_price=73500), risk_profile="BALANCED", position=pos)
    kd = d.to_kis_paper_decision(quantity=999, price=73500)   # 과다 요청.
    assert kd.quantity == 13
    assert kd.is_short_entry is False


# ── stop_loss 미설정 ──

def test_stop_loss_not_configured():
    pos = _held(current_price=73000)   # stop_loss / pct 모두 없음.
    sl, src = pos.resolve_stop_loss_price()
    assert sl is None and src == "STOP_LOSS_NOT_CONFIGURED"
    # default 도 없으면 STOP_LOSS 트리거 없음.
    from app.agents.position_context import infer_position_sell_reason
    assert infer_position_sell_reason(pos) is None


def test_invalid_stop_loss_pct_no_crash():
    for bad in (-1.0, 0.0, None):
        pos = _held(current_price=73000, stop_loss_pct=bad)
        sl, _src = pos.resolve_stop_loss_price()
        assert sl is None   # 비정상은 미설정 처리 (예외 없음).


# ── readiness 차단 ──

def test_readiness_blocked_no_sell_order(db):
    pos = _held(current_price=73500, stop_loss_pct=2.0)
    council = run_agent_council(_market(current_price=73500), risk_profile="BALANCED",
                                position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=73500)
    r = _run(db, decision=kd, credentials_present=False)   # 자격 미설정 → 차단.
    db.commit()
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False
    assert r.broker_order_no is None


# ── ledger / order_quality / secret ──

def test_ledger_and_quality_and_no_secret(db, monkeypatch):
    import json

    import app.kis_paper.auto_executor as ax
    from app.kis_paper.order_quality import build_order_quality_log
    calls = []
    monkeypatch.setattr(ax, "record_paper_event",
                        lambda **kw: calls.append(kw) or SimpleNamespace())
    pos = _held(current_price=73500, stop_loss_pct=2.0)
    council = run_agent_council(_market(current_price=73500), risk_profile="BALANCED",
                                position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=73500)
    r = _run(db, decision=kd)
    db.commit()
    assert len(calls) >= 1                                  # ledger 기록.
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["broker_order_no"] == "KIS-PAPER-SELL-0001"
    assert q["is_live_authorization"] is False
    log = db.query(AgentDecisionLog).one()
    flat = json.dumps(log.meta).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in flat
