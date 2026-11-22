"""3-05: 트레일링 스탑 SELL 자동화.

보유 종목이 상승해 최고가(high_watermark)를 갱신한 뒤, 최고가 대비
trailing_stop_pct 이상 하락하면 수익 보호 SELL(TRAILING_STOP) 이 생성되고 KIS
모의 SELL 경로로 이어지는지 검증. 우선순위 STOP_LOSS > TAKE_PROFIT > TRAILING_STOP.

수익 보호 — 신규 숏 아님, 보유 없으면 SELL 금지, 실거래 0건.
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
    infer_position_sell_reason,
    is_trailing_stop_triggered,
)
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.models import AgentDecisionLog, Base
from app.kis_paper.auto_executor import execute_kis_paper_auto_order
from app.risk.risk_manager import RiskDecision

_KST = timezone(timedelta(hours=9))
OPEN_TIME = datetime(2026, 5, 22, 10, 0, tzinfo=_KST).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_runtime_config(tmp_path, monkeypatch):
    # 2026-06-12: 라이브 runtime_overrides.json(take_profit 3.5%) 오염 격리. C1 이후
    #   take_profit 3.5% 가 +4.5% 시나리오를 익절로 먼저 가로채(TAKE_PROFIT > TRAILING_STOP)
    #   트레일링 단독 검증이 불가했다. tmp 격리 + take_profit 높여 트레일링이 binding 되게.
    import app.core.runtime_config as rc
    monkeypatch.setattr(rc, "overrides_path", lambda: tmp_path / "ro.json")
    monkeypatch.setattr(rc, "_legacy_overrides_path", lambda: tmp_path / "legacy.json")
    rc.reset_runtime_overrides_for_tests()
    rc.set_runtime_overrides(take_profit_pct=10.0, stop_loss_pct=2.0)
    yield
    rc.reset_runtime_overrides_for_tests()


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


def _approved_sell_route(no="KIS-PAPER-SELL-TS-0001"):
    async def _fn(**kw):
        audit = SimpleNamespace(id=41, broker_order_id=no, broker_status="FILLED",
                                filled_quantity=13, avg_fill_price=78400, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _market(**kw):
    base = dict(
        symbol="005930", current_price=78400, prev_close=80000, open_price=80000,
        vwap=79000, opening_range_high=80500, opening_range_low=78000,
        recent_closes=(78000, 79000, 80000, 79000, 78400),
        current_volume=120.0, avg_volume=100.0,
        market_regime="SIDEWAYS", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _held(**kw):
    base = dict(held_position=True, symbol="005930", quantity=13, available_quantity=13,
                average_entry_price=75000, high_watermark=80000, trailing_stop_pct=2.0)
    base.update(kw)
    return PositionContext(**base)


def _run(db, *, decision, settings=None, route=None, credentials_present=True):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision, settings=settings or _settings(),
        broker=MockBrokerAdapter(), risk=object(), broker_is_kis_paper=True,
        credentials_present=credentials_present,
        route_order_fn=route or _approved_sell_route(), now=OPEN_TIME,
        chain_id="ep-ts-1"))


# ── 시나리오 A: 상승 후 trailing stop 도달 → SELL E2E ──

def test_trailing_stop_to_price_and_sell(db):
    # entry 75,000 / hwm 80,000 / trail 2.0% → 트레일가 78,400. 현재 78,400 → TRAILING_STOP.
    pos = _held(current_price=78400)
    ts_price, src = pos.resolve_trailing_stop_price()
    assert ts_price == 78400.0 and src == "pct"
    council = run_agent_council(_market(current_price=78400), risk_profile="BALANCED",
                                position=pos)
    assert council.final_action == CouncilAction.SELL
    assert council.sell_reason["reason_code"] == "TRAILING_STOP"
    assert council.held_position is True

    kd = council.to_kis_paper_decision(quantity=13, price=78400)
    assert kd.side == "SELL" and kd.quantity <= 13 and kd.is_short_entry is False
    r = _run(db, decision=kd)
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.side == "SELL"
    assert r.broker_order_no == "KIS-PAPER-SELL-TS-0001"
    assert r.broker_order_sent is True
    assert r.broker_order_type == "KIS_PAPER"
    assert r.is_live_authorization is False
    log = db.query(AgentDecisionLog).one()
    assert log.meta["sell_reason_code"] == "TRAILING_STOP"
    assert log.meta["held_position"] is True
    assert log.meta["is_short_entry"] is False


def test_trailing_below_triggers_sell():
    pos = _held(current_price=78000)   # 트레일가 78400 아래.
    d = run_agent_council(_market(current_price=78000), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "TRAILING_STOP"


def test_ratio_trailing_pct_normalized():
    pos = _held(current_price=78400, trailing_stop_pct=0.02)   # 0.02 → 2%.
    assert pos.resolve_trailing_stop_price()[0] == 78400.0
    assert is_trailing_stop_triggered(pos)["triggered"] is True


# ── 시나리오 B: 미도달 ──

def test_above_trailing_no_sell():
    pos = _held(current_price=79000)   # 트레일가 78400, 현재 79000.
    d = run_agent_council(_market(current_price=79000), risk_profile="BALANCED", position=pos)
    assert d.sell_reason.get("reason_code") != "TRAILING_STOP"


# ── 시나리오 C: 수익 구간 아님 (hwm <= entry) ──

def test_high_watermark_below_entry_no_trailing():
    # hwm 74,500 <= entry 75,000 → 수익 보호 구간 아님 → TRAILING_STOP 미발동.
    pos = _held(current_price=73000, high_watermark=74500, stop_loss=70000)
    ts, src = pos.resolve_trailing_stop_price()
    assert ts is None and src == "TRAILING_STOP_NOT_IN_PROFIT"
    assert infer_position_sell_reason(pos) != "TRAILING_STOP"


# ── 시나리오 D: 보유 없음 ──

def test_no_held_no_trailing_sell():
    pos = PositionContext(held_position=False, average_entry_price=75000,
                          current_price=78400, high_watermark=80000, trailing_stop_pct=2.0)
    d = run_agent_council(_market(current_price=78400), risk_profile="BALANCED", position=pos)
    assert d.final_action != CouncilAction.SELL


# ── 시나리오 E: 수량 제한 ──

def test_trailing_sell_quantity_capped():
    pos = _held(current_price=78400, quantity=13, available_quantity=8)
    d = run_agent_council(_market(current_price=78400), risk_profile="BALANCED", position=pos)
    kd = d.to_kis_paper_decision(quantity=999, price=78400)
    assert kd.quantity == 8
    assert kd.is_short_entry is False


# ── 시나리오 F: trailing_stop_pct invalid ──

def test_trailing_not_configured():
    pos = _held(current_price=78400, trailing_stop_pct=None)
    ts, src = pos.resolve_trailing_stop_price()
    assert ts is None and src == "TRAILING_STOP_NOT_CONFIGURED"


def test_invalid_trailing_pct_no_crash():
    for bad in (-1.0, 0.0):
        pos = _held(current_price=78400, trailing_stop_pct=bad)
        assert pos.resolve_trailing_stop_price()[0] is None


# ── 시나리오 G: 우선순위 ──

def test_stop_loss_priority_over_trailing():
    pos = _held(current_price=78400, stop_loss=78400)   # SL+trail 동시.
    assert infer_position_sell_reason(pos) == "STOP_LOSS"
    d = run_agent_council(_market(current_price=78400), risk_profile="BALANCED", position=pos)
    assert d.sell_reason["reason_code"] == "STOP_LOSS"


def test_take_profit_priority_over_trailing():
    # TAKE_PROFIT(절대 78000) + TRAILING(78400) 동시 — current 78400 ≥ tp 78000 → TAKE_PROFIT.
    pos = _held(current_price=78400, take_profit=78000)
    assert infer_position_sell_reason(pos) == "TAKE_PROFIT"


# ── readiness 차단 ──

def test_readiness_blocked_no_trailing_order(db):
    pos = _held(current_price=78400)
    council = run_agent_council(_market(current_price=78400), risk_profile="BALANCED",
                                position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=78400)
    r = _run(db, decision=kd, credentials_present=False)
    db.commit()
    assert r.reason_code != "KIS_PAPER_SUBMITTED"
    assert r.broker_order_sent is False


# ── ledger / order_quality / secret ──

def test_trailing_ledger_quality_no_secret(db, monkeypatch):
    import json

    import app.kis_paper.auto_executor as ax
    from app.kis_paper.order_quality import build_order_quality_log
    calls = []
    monkeypatch.setattr(ax, "record_paper_event",
                        lambda **kw: calls.append(kw) or SimpleNamespace())
    pos = _held(current_price=78400)
    council = run_agent_council(_market(current_price=78400), risk_profile="BALANCED",
                                position=pos)
    kd = council.to_kis_paper_decision(quantity=13, price=78400)
    r = _run(db, decision=kd)
    db.commit()
    assert len(calls) >= 1
    q = build_order_quality_log(result=r, decision=kd).to_dict()
    assert q["broker_order_no"] == "KIS-PAPER-SELL-TS-0001"
    assert q["is_live_authorization"] is False
    log = db.query(AgentDecisionLog).one()
    flat = json.dumps(log.meta).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in flat
