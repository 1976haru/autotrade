"""KIS_PAPER_REAL_MARKET_DRYRUN — 실 KIS 시세 → 판단 → dry-run 결정 기록 검증.

핵심: force_dry_run=True 면 BUY/SELL 결정이 나와도 dry_run 강제 True 로 덮어써
route_order/broker 주문 호출 0건 (결정만 기록). price_source=kis 태깅 확인.

broker/KIS 실 API/실제 route_order 호출 0건 — fake KIS broker(get_price) +
route_order spy + in-memory SQLite + 일부 monkeypatch.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app.db.base import Base
from app.brokers.mock_broker import MockBrokerAdapter
from app.kis_paper import live_runner as lr
from app.kis_paper.auto_executor import KisPaperAutoDecision, execute_kis_paper_auto_order
from app.risk.risk_manager import RiskDecision


_KST = timezone(timedelta(hours=9))


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_KST).astimezone(timezone.utc)


OPEN_TIME = _utc(2026, 5, 22, 10, 0)   # 장중 (창 09:05~14:50 안)


@pytest.fixture
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
        kis_paper_auto_max_order_notional=5_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        market_data_provider="mock", kis_paper_auto_symbols="005930",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _buy_decision():
    return KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
    )


def _spy_route(decision=RiskDecision.APPROVED):
    calls = {"n": 0}

    async def _fn(*, order, **kw):
        calls["n"] += 1
        audit = types.SimpleNamespace(id=1, broker_order_id="PAPER-1",
                                      broker_status="FILLED", filled_quantity=order.quantity,
                                      executed=True)
        return types.SimpleNamespace(decision=decision, reasons=[], audit=audit)

    return _fn, calls


class _Quote:
    def __init__(self, p): self.price = p


class _FakeKisBroker:
    def __init__(self, prices):
        self.is_paper = True
        self._prices = prices
        self.calls = 0

    async def get_price(self, symbol):
        p = self._prices[min(self.calls, len(self._prices) - 1)]
        self.calls += 1
        return _Quote(p)


class _Act:
    def __init__(self, v): self.value = v


class _FakeDecision:
    def __init__(self, action, exit_plan=True):
        self.final_action = _Act(action)
        self._ep = exit_plan

    def to_kis_paper_decision(self, *, quantity, price):
        if self.final_action.value == "HOLD":
            return None
        return KisPaperAutoDecision(
            symbol="005930", side=self.final_action.value, quantity=quantity,
            price=price, selected_strategies=["MOMENTUM"], confidence=0.8,
            quality_score=80, entry_reason="t", has_exit_plan=self._ep,
            exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0} if self._ep else {},
        )


# ─────────── 1) _ForceDryRunSettings 프록시 ───────────


def test_force_dry_run_settings_proxy():
    base = _settings(kis_paper_auto_order_dry_run=False)
    proxy = lr._ForceDryRunSettings(base)
    assert proxy.kis_paper_auto_order_dry_run is True          # 강제 True
    assert proxy.enable_kis_paper_auto_trading is True         # 위임
    assert proxy.enable_live_trading is False                  # 위임 (안전 flag 그대로)
    assert proxy.kis_paper_auto_order_window_start == "09:05"  # 위임


# ─────────── 2) force_dry_run: BUY 라도 route_order 미호출 ───────────


def test_force_dry_run_buy_does_not_call_route_order(db):
    """REAL executor + 프록시(force dry_run) → DRY_RUN_OK, route_order 0회."""
    route, calls = _spy_route()
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=_buy_decision(),
        settings=lr._ForceDryRunSettings(_settings(kis_paper_auto_order_dry_run=False)),
        broker=MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route, now=OPEN_TIME,
    ))
    assert res.reason_code == "KIS_PAPER_DRY_RUN_OK"
    assert res.submitted is False
    assert res.broker_order_sent is False
    assert res.is_live_authorization is False
    assert calls["n"] == 0          # 주문 라우터까지 도달 0건


def test_without_force_dry_run_buy_does_route(db):
    """대조군: 프록시 없이 dry_run=False → route_order 1회 (force_dry_run 효과 입증)."""
    route, calls = _spy_route(RiskDecision.APPROVED)
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=_buy_decision(),
        settings=_settings(kis_paper_auto_order_dry_run=False),
        broker=MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route, now=OPEN_TIME,
    ))
    assert calls["n"] == 1
    assert res.reason_code == "KIS_PAPER_SUBMITTED"


# ─────────── 3) runner: price_source=kis + dry_run 태깅 ───────────


def test_runner_force_dry_run_tags_price_source_and_no_submit(db, monkeypatch):
    monkeypatch.setattr(lr, "run_agent_council",
                        lambda mi, **kw: _FakeDecision("BUY", exit_plan=True))
    seen = {"dry_run_in_settings": None}

    async def _fake_exec(db_, *, decision, settings, route_order_fn, **kw):
        seen["dry_run_in_settings"] = settings.kis_paper_auto_order_dry_run
        return types.SimpleNamespace(reason_code="KIS_PAPER_DRY_RUN_OK", submitted=False,
                                     fill_status=None, reason_message="dry")

    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisBroker([70000]), risk=object(),
        settings=_settings(kis_paper_auto_order_dry_run=False),
        credentials_present=True, force_dry_run=True,
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["price_source"] == "kis"
    assert out["last_action"] == "BUY"
    assert out["dry_run"] is True
    assert out["order_submitted"] is False
    assert out["broker_order_sent"] is False
    assert out["is_live_authorization"] is False
    # 프록시가 executor 에 dry_run=True 를 전달했는지 (force 효과).
    assert seen["dry_run_in_settings"] is True


def test_runner_hold_records_price_source_no_order(db, monkeypatch):
    monkeypatch.setattr(lr, "run_agent_council",
                        lambda mi, **kw: _FakeDecision("HOLD"))
    called = {"n": 0}

    async def _fake_exec(*a, **k):
        called["n"] += 1
        return types.SimpleNamespace(reason_code="x", submitted=False,
                                     fill_status=None, reason_message="")

    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisBroker([70000]), risk=object(),
        settings=_settings(), credentials_present=True, force_dry_run=True,
    )
    out = asyncio.run(runner(None, None, 0))
    assert called["n"] == 0
    assert out["price_source"] == "kis"
    assert out["ai_hold_signals"] == 1
    assert out["order_submitted"] is False


def test_runner_real_council_over_rising_kis_prices(db, monkeypatch):
    """진짜 run_agent_council 로 실 KIS(흉내) 시세 시리즈 평가 — 무중단."""
    async def _fake_exec(db_, *, decision, **kw):
        return types.SimpleNamespace(reason_code="KIS_PAPER_DRY_RUN_OK", submitted=False,
                                     fill_status=None, reason_message="dry")

    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    broker = _FakeKisBroker([70000, 70300, 70700, 71200, 71800, 72500])
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=broker, risk=object(), settings=_settings(),
        credentials_present=True, force_dry_run=True,
    )
    outs = [asyncio.run(runner(None, None, i)) for i in range(6)]
    assert sum(o["ai_decisions"] for o in outs) == 6
    assert sum(o["errors"] for o in outs) == 0
    assert all(o.get("price_source") == "kis" for o in outs)
    assert all(o.get("is_live_authorization") is False for o in outs)


# ─────────── 4) 매 tick 판단이 RuntimeEvent 로 기록 (HOLD 포함) ───────────


def _capture_log_event(monkeypatch):
    events = []
    import app.system.event_log as ev
    monkeypatch.setattr(ev, "log_event",
                        lambda **kw: events.append(kw))
    return events


def test_runner_emits_decision_event_for_hold(db, monkeypatch):
    monkeypatch.setattr(lr, "run_agent_council", lambda mi, **kw: _FakeDecision("HOLD"))
    events = _capture_log_event(monkeypatch)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisBroker([70000]), risk=object(),
        settings=_settings(), credentials_present=True, force_dry_run=True,
    )
    asyncio.run(runner(None, None, 0))
    assert len(events) == 1
    e = events[0]
    assert e["code"] == "KIS_PAPER_DECISION_HOLD"
    assert e["details"]["price_source"] == "kis"
    assert e["details"]["final_action"] == "HOLD"
    assert e["details"]["dry_run"] is True
    assert e["details"]["order_submitted"] is False
    assert e["details"]["broker_order_sent"] is False
    assert e["details"]["is_live_authorization"] is False


def test_runner_emits_decision_event_for_buy(db, monkeypatch):
    monkeypatch.setattr(lr, "run_agent_council", lambda mi, **kw: _FakeDecision("BUY"))

    async def _fake_exec(*a, **k):
        return types.SimpleNamespace(reason_code="KIS_PAPER_DRY_RUN_OK", submitted=False,
                                     fill_status=None, reason_message="dry")
    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    events = _capture_log_event(monkeypatch)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisBroker([70000]), risk=object(),
        settings=_settings(), credentials_present=True, force_dry_run=True,
    )
    asyncio.run(runner(None, None, 0))
    codes = [e["code"] for e in events]
    assert "KIS_PAPER_DECISION_BUY" in codes


def test_emit_decision_event_uses_valid_category_and_records():
    """REAL log_event 사용 — category 가 유효(EventCategory)해서 실제로 기록되는지.

    (monkeypatch 없이 — 'KIS_PAPER' 같은 invalid category 회귀를 잡는다.)
    """
    from app.system.event_log import (
        get_runtime_event_log,
        reset_runtime_event_log_for_tests,
    )
    reset_runtime_event_log_for_tests()
    lr._emit_decision_event(symbol="005930", price=70000, action="HOLD",
                            dry_run=True, force_dry_run=True)
    events = get_runtime_event_log().recent(limit=10)
    reset_runtime_event_log_for_tests()
    codes = [e.code for e in events]
    assert "KIS_PAPER_DECISION_HOLD" in codes, f"event not recorded: {codes}"
    rec = [e for e in events if e.code == "KIS_PAPER_DECISION_HOLD"][0]
    assert rec.details["price_source"] == "kis"
    assert rec.details["order_submitted"] is False


# ─────────── 5) 정적 가드 ───────────


def test_live_runner_no_direct_place_order_still_holds():
    import pathlib
    src = pathlib.Path(lr.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        for banned in (".place_order(", ".cancel_order(",
                       "enable_live_trading =", "settings.enable_live_trading ="):
            assert banned not in code, f"banned: {line!r}"
