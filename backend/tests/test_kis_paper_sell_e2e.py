"""3-07: SELL 주문 KIS 모의 API E2E.

3-02~3-06 SELL 판단 → KisPaperAutoDecision(side=SELL) → sanctioned route_order →
KIS 모의 SELL 주문(KIS_PAPER_SUBMITTED + broker_order_no + order/fill status).
FILLED / PARTIALLY_FILLED / UNFILLED / REJECTED 상태 + 차단(readiness/보유없음/
수량0) + AgentDecisionLog/decision_episode/order_quality/ledger 연결 검증.

실거래 0건: KIS_IS_PAPER=true / ENABLE_LIVE_TRADING=false / broker_order_type=
KIS_PAPER / is_live_authorization=false. route_order 는 fake (실 KIS API 0건).
"""

from __future__ import annotations

import asyncio
import json
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
from app.kis_paper.order_quality import build_order_quality_log
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


# ── route_order fixtures (fake sanctioned path — no real KIS API) ──

def _filled_route(no="KIS-PAPER-SELL-0001"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=61, broker_order_id=no, broker_status="FILLED",
                                filled_quantity=13, avg_fill_price=73500, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _partial_route(no="KIS-PAPER-SELL-0002"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=62, broker_order_id=no, broker_status="PARTIAL",
                                filled_quantity=7, avg_fill_price=73500, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _unfilled_route(no="KIS-PAPER-SELL-0003"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=63, broker_order_id=no, broker_status="PENDING",
                                filled_quantity=0, avg_fill_price=None, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _rejected_route():
    async def _fn(**kw):
        audit = SimpleNamespace(id=64, broker_order_id=None, broker_status=None,
                                filled_quantity=0, avg_fill_price=None, executed=False)
        return SimpleNamespace(decision=RiskDecision.REJECTED,
                               reasons=["notional too high"], audit=audit)
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
                average_entry_price=75000, current_price=73500, stop_loss=73500)
    base.update(kw)
    return PositionContext(**base)


def _sell_decision(**pos_kw):
    council = run_agent_council(_market(current_price=pos_kw.get("current_price", 73500)),
                                risk_profile="BALANCED", position=_held(**pos_kw))
    assert council.final_action == CouncilAction.SELL
    return council, council.to_kis_paper_decision(quantity=13, price=73500)


def _run(db, *, decision, settings=None, route=None, credentials_present=True):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision, settings=settings or _settings(),
        broker=MockBrokerAdapter(), risk=object(), broker_is_kis_paper=True,
        credentials_present=credentials_present,
        route_order_fn=route or _filled_route(), now=OPEN_TIME, chain_id="ep-sell-e2e"))


# ── SELL → KIS Paper FILLED E2E ──

def test_sell_filled_e2e(db):
    council, kd = _sell_decision()
    assert kd.side == "SELL" and kd.symbol == "005930"
    assert kd.quantity <= 13 and kd.is_short_entry is False and kd.short_position is False
    assert kd.sell_reason_code == "STOP_LOSS"
    assert kd.held_position is True

    r = _run(db, decision=kd, route=_filled_route())
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.submitted is True
    assert r.broker_order_sent is True
    assert r.broker_order_no == "KIS-PAPER-SELL-0001"
    assert r.broker_order_type == "KIS_PAPER"
    assert r.is_live_authorization is False
    assert r.order_status == "FILLED"
    assert r.fill_status == "FILLED"
    assert r.filled_quantity == 13      # FILLED → filled == quantity.
    # AgentDecisionLog 연결.
    log = db.query(AgentDecisionLog).one()
    assert log.meta["sell_reason_code"] == "STOP_LOSS"
    assert log.meta["broker_order_no"] == "KIS-PAPER-SELL-0001"
    assert log.meta["held_position"] is True
    assert log.meta["is_short_entry"] is False
    assert log.meta["order_created"] is True
    assert log.meta["broker_order_type"] == "KIS_PAPER"
    # order_quality 연결.
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["order_status"] == "FILLED"
    assert q["broker_order_no"] == "KIS-PAPER-SELL-0001"
    assert q["requested_at"] and q["submitted_at"]
    assert q["is_live_authorization"] is False


# ── PARTIALLY_FILLED / UNFILLED / REJECTED ──

def test_sell_partially_filled(db):
    _council, kd = _sell_decision()
    r = _run(db, decision=kd, route=_partial_route())
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.fill_status == "PARTIALLY_FILLED"
    assert r.filled_quantity == 7 and r.filled_quantity < kd.quantity   # 부분.
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["order_status"] == "PARTIALLY_FILLED"
    assert q["unfilled_quantity"] == kd.quantity - 7
    assert q["partial_fill"] is True


def test_sell_unfilled(db):
    _council, kd = _sell_decision()
    r = _run(db, decision=kd, route=_unfilled_route())
    assert r.submitted is True
    assert r.filled_quantity == 0       # UNFILLED → filled 0.
    assert r.fill_status is None
    assert r.is_live_authorization is False


def test_sell_rejected(db):
    _council, kd = _sell_decision()
    r = _run(db, decision=kd, route=_rejected_route())
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.submitted is False
    assert r.broker_order_sent is False     # REJECTED → 전송 안 됨.
    assert r.broker_order_no is None
    assert r.is_live_authorization is False
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["order_status"] == "REJECTED"


# ── 차단: readiness / 보유 없음 / 수량 0 ──

def test_readiness_blocked_no_sell_order(db):
    _council, kd = _sell_decision()
    called = {"n": 0}

    async def _route(**kw):
        called["n"] += 1
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                               audit=SimpleNamespace(id=1))

    r = _run(db, decision=kd, route=_route, credentials_present=False)
    db.commit()
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False
    assert r.broker_order_no is None
    assert called["n"] == 0                 # route_order 호출 0건.


def test_no_held_no_sell_decision():
    # 보유 없음 → council SELL 미생성 (강세장이면 BUY, 약세면 HOLD) → SELL 주문 0.
    council = run_agent_council(
        _market(current_price=71000, recent_closes=(75000, 74000, 73000, 72000, 71000),
                market_regime="TREND_DOWN"),
        risk_profile="AGGRESSIVE", position=PositionContext(held_position=False))
    assert council.final_action != CouncilAction.SELL


def test_zero_quantity_no_sell():
    council = run_agent_council(
        _market(current_price=73500),
        risk_profile="BALANCED",
        position=PositionContext(held_position=True, quantity=0, available_quantity=0,
                                 average_entry_price=75000, current_price=73500,
                                 stop_loss=73500))
    assert council.final_action == CouncilAction.HOLD   # 청산 가능 수량 0 → SELL 불가.


# ── 수량 제한 / 숏 아님 ──

def test_sell_quantity_capped_to_held():
    council, _ = _sell_decision(quantity=13, available_quantity=8)
    kd = council.to_kis_paper_decision(quantity=999, price=73500)
    assert kd.quantity == 8                 # available 8 이하.
    assert kd.is_short_entry is False


# ── decision_episode 연결 (council.to_dict carry) ──

def test_decision_episode_carry():
    council, _kd = _sell_decision()
    cd = council.to_dict()
    assert cd["sell_reason"]["reason_code"] == "STOP_LOSS"
    assert cd["held_position"] is True
    assert cd["is_short_entry"] is False
    assert cd["position_quantity"] == 13


# ── ledger 연결 + secret 미노출 ──

def test_ledger_and_no_secret(db, monkeypatch):
    import app.kis_paper.auto_executor as ax
    calls = []
    monkeypatch.setattr(ax, "record_paper_event",
                        lambda **kw: calls.append(kw) or SimpleNamespace())
    _council, kd = _sell_decision()
    r = _run(db, decision=kd, route=_filled_route())
    db.commit()
    assert len(calls) >= 1
    assert calls[0]["metadata"]["broker_order_type"] == "KIS_PAPER"
    log = db.query(AgentDecisionLog).one()
    flat = json.dumps(log.meta).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in flat
    assert r.broker_order_type == "KIS_PAPER"


# ── 전 상태에서 is_live_authorization=false ──

@pytest.mark.parametrize("route", [_filled_route(), _partial_route(),
                                   _unfilled_route(), _rejected_route()])
def test_all_states_not_live(db, route):
    _council, kd = _sell_decision()
    r = _run(db, decision=kd, route=route)
    assert r.is_live_authorization is False
    assert r.broker_order_type == "KIS_PAPER"
