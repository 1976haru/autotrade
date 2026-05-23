"""3-06: 장마감 강제 청산 SELL 후보 (오버나이트 리스크 회피).

지정 강제청산 시각(기본 15:20 KST) 도달 시 보유 종목을 SELL(MARKET_CLOSE_EXIT)
후보로 생성 → KIS 모의 SELL 경로. 강제청산 전엔 미발동, 보유 없으면 금지,
오버나이트 허용 전략은 미발동. 우선순위 STOP_LOSS>TAKE_PROFIT>TRAILING>MARKET_CLOSE.
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
from app.agents.position_context import PositionContext, infer_position_sell_reason
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
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="15:30",
        kis_paper_auto_min_confidence=0.0, kis_paper_auto_min_quality_score=0,
        market_data_provider="mock",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _approved_sell_route(no="KIS-PAPER-SELL-MC-0001"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=51, broker_order_id=no, broker_status="FILLED",
                                filled_quantity=13, avg_fill_price=75000, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _market(**kw):
    base = dict(
        symbol="005930", current_price=75000, prev_close=75000, open_price=75000,
        vwap=75000, opening_range_high=75500, opening_range_low=74500,
        recent_closes=(75000, 75000, 75000, 75000, 75000),
        current_volume=120.0, avg_volume=100.0,
        market_regime="SIDEWAYS", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _held(**kw):
    base = dict(held_position=True, symbol="005930", quantity=13, available_quantity=13,
                average_entry_price=75000, current_price=75000,
                market_close_exit_enabled=True, force_exit_time_kst="15:20")
    base.update(kw)
    return PositionContext(**base)


# 장마감 직전 주문 가능 시각으로 실행 (15:20).
CLOSE_RUN_TIME = datetime(2026, 5, 22, 15, 20, tzinfo=_KST).astimezone(timezone.utc)


def _run(db, *, decision, settings=None, route=None, credentials_present=True, now=CLOSE_RUN_TIME):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision, settings=settings or _settings(),
        broker=MockBrokerAdapter(), risk=object(), broker_is_kis_paper=True,
        credentials_present=credentials_present,
        route_order_fn=route or _approved_sell_route(), now=now,
        chain_id="ep-mc-1"))


# ── 시나리오 A: 15:20 도달 → MARKET_CLOSE_EXIT SELL E2E ──

def test_force_exit_time_triggers_sell(db):
    pos = _held(current_time_kst="15:20")
    council = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    assert council.final_action == CouncilAction.SELL
    assert council.sell_reason["reason_code"] == "MARKET_CLOSE_EXIT"
    assert council.held_position is True

    kd = council.to_kis_paper_decision(quantity=13, price=75000)
    assert kd.side == "SELL" and kd.quantity <= 13 and kd.is_short_entry is False
    r = _run(db, decision=kd)
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.side == "SELL"
    assert r.broker_order_no == "KIS-PAPER-SELL-MC-0001"
    assert r.broker_order_sent is True
    assert r.broker_order_type == "KIS_PAPER"
    assert r.is_live_authorization is False
    log = db.query(AgentDecisionLog).one()
    assert log.meta["sell_reason_code"] == "MARKET_CLOSE_EXIT"
    assert log.meta["held_position"] is True
    assert log.meta["is_short_entry"] is False


def test_after_force_exit_time_triggers():
    pos = _held(current_time_kst="15:25")   # 15:20 이후.
    d = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    assert d.sell_reason["reason_code"] == "MARKET_CLOSE_EXIT"


# ── 시나리오 B: 강제청산 전 미발동 ──

def test_before_force_exit_no_sell():
    pos = _held(current_time_kst="15:19")   # 15:20 미만.
    d = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    assert d.sell_reason.get("reason_code") != "MARKET_CLOSE_EXIT"


# ── 시나리오 C: 보유 없음 ──

def test_no_held_no_market_close_sell():
    pos = PositionContext(held_position=False, market_close_exit_enabled=True,
                          current_time_kst="15:25", current_price=75000)
    d = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.HOLD


# ── 시나리오 D: 오버나이트 허용 ──

def test_allow_overnight_no_force_exit():
    pos = _held(current_time_kst="15:25", allow_overnight=True)
    d = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    assert d.sell_reason.get("reason_code") != "MARKET_CLOSE_EXIT"
    assert infer_position_sell_reason(pos) != "MARKET_CLOSE_EXIT"


# ── 시나리오 E: 수량 제한 ──

def test_market_close_sell_quantity_capped():
    pos = _held(current_time_kst="15:20", quantity=13, available_quantity=8)
    d = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    kd = d.to_kis_paper_decision(quantity=999, price=75000)
    assert kd.quantity == 8
    assert kd.is_short_entry is False


# ── enabled=False / 미설정 ──

def test_not_enabled_no_force_exit():
    pos = _held(current_time_kst="15:25", market_close_exit_enabled=False)
    assert infer_position_sell_reason(pos) != "MARKET_CLOSE_EXIT"


def test_invalid_time_no_crash():
    for bad in ("nope", "", None, "99:99"):
        pos = _held(current_time_kst=bad)
        # 비정상 시각 → 시각 트리거 없음 (legacy phase 도 없으면 미발동).
        assert pos.is_market_close_exit_triggered() is False


# ── 시나리오 G: 우선순위 ──

def test_stop_loss_priority_over_market_close():
    pos = _held(current_time_kst="15:25", current_price=73000, stop_loss=73500)
    assert infer_position_sell_reason(pos) == "STOP_LOSS"


def test_take_profit_priority_over_market_close():
    pos = _held(current_time_kst="15:25", current_price=77300, take_profit=77250)
    assert infer_position_sell_reason(pos) == "TAKE_PROFIT"


def test_trailing_priority_over_market_close():
    pos = _held(current_time_kst="15:25", current_price=78400,
                high_watermark=80000, trailing_stop_pct=2.0)
    assert infer_position_sell_reason(pos) == "TRAILING_STOP"


def test_market_close_only_when_no_other_trigger():
    pos = _held(current_time_kst="15:25", current_price=75000)   # 다른 트리거 없음.
    assert infer_position_sell_reason(pos) == "MARKET_CLOSE_EXIT"


# ── readiness / ledger / secret ──

def test_readiness_blocked_no_mc_order(db):
    pos = _held(current_time_kst="15:20")
    council = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=75000)
    r = _run(db, decision=kd, credentials_present=False)
    db.commit()
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False


def test_mc_ledger_quality_no_secret(db, monkeypatch):
    import json

    import app.kis_paper.auto_executor as ax
    from app.kis_paper.order_quality import build_order_quality_log
    calls = []
    monkeypatch.setattr(ax, "record_paper_event",
                        lambda **kw: calls.append(kw) or SimpleNamespace())
    pos = _held(current_time_kst="15:20")
    council = run_agent_council(_market(), risk_profile="BALANCED", position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=75000)
    r = _run(db, decision=kd)
    db.commit()
    assert len(calls) >= 1
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["broker_order_no"] == "KIS-PAPER-SELL-MC-0001"
    assert q["is_live_authorization"] is False
    log = db.query(AgentDecisionLog).one()
    flat = json.dumps(log.meta).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in flat
