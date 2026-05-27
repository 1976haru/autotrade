"""CHECKLIST-02 시작버튼 연결 — acceptance 테스트.

완료 기준:
  1. counters.ai_decisions > 0
  2. counters.orders_attempted > 0 (dry-run BUY 시도)
  3. exit_plan 없는 BUY 차단 (order_submitted=False, broker_order_sent=False)
  4. Mock 모드 1분 운용 equivalent — 오류 0, ai_decisions>0, RuntimeEvent>0

dry_run_preview(force_dry_run)에서 order_attempted 는 증가해도 orders_executed
(submitted) 은 *반드시 0*. 실 broker/route_order 주문 전송 0건.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.brokers.mock_broker import MockBrokerAdapter
from app.kis_paper import live_runner as lr
from app.kis_paper.auto_executor import KisPaperAutoDecision, execute_kis_paper_auto_order
from app.kis_paper.engine import (
    KisPaperRunState,
    TestMode,
    _reset_engine_for_tests,
    get_engine,
)
from app.kis_paper.readiness import evaluate_readiness
from app.system.event_log import get_runtime_event_log, reset_runtime_event_log_for_tests


_KST = timezone(timedelta(hours=9))


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_KST).astimezone(timezone.utc)


OPEN_TIME = _utc(2026, 5, 22, 10, 0)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset():
    _reset_engine_for_tests()
    reset_runtime_event_log_for_tests()
    yield
    _reset_engine_for_tests()
    reset_runtime_event_log_for_tests()


def _settings(**kw):
    base = dict(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=5_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        market_data_provider="mock", kis_paper_auto_symbols="005930",
        kis_app_key="PAPER_KEY", kis_app_secret="PAPER_SECRET", kis_account_no="12345678-01",
        enable_ai_execution=False, enable_futures_live_trading=False, default_mode="PAPER",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


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


def _runner(db, *, action, exit_plan=True, exec_reason="KIS_PAPER_DRY_RUN_OK",
            submitted=False, monkeypatch, force_dry_run=True):
    monkeypatch.setattr(lr, "run_agent_council",
                        lambda mi, **kw: _FakeDecision(action, exit_plan))

    async def _fake_exec(db_, *, decision, **kw):
        return types.SimpleNamespace(reason_code=exec_reason, submitted=submitted,
                                     fill_status="FILLED" if submitted else None,
                                     reason_message=exec_reason)
    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisBroker([70000, 70300, 70700]), risk=object(),
        settings=_settings(), credentials_present=True, force_dry_run=force_dry_run,
    )
    return runner


# ═══════════ 기준 1: ai_decisions > 0 (mock 모드) ═══════════


def test_criterion1_mock_mode_ai_decisions_positive():
    engine = get_engine()
    rd = evaluate_readiness(_settings())
    asyncio.run(engine.start(TestMode.MOCK, rd, max_ticks_override=10))
    assert engine.counters.ai_decisions > 0
    assert engine.counters.errors == 0
    assert engine.state == KisPaperRunState.COMPLETED


# ═══════════ 기준 2: orders_attempted > 0, submitted = 0 (dry-run BUY) ═══════════


def test_criterion2_dry_run_buy_attempted_not_submitted(db, monkeypatch):
    runner = _runner(db, action="BUY", monkeypatch=monkeypatch,
                     exec_reason="KIS_PAPER_DRY_RUN_OK", submitted=False)
    engine = get_engine()
    rd = evaluate_readiness(_settings())
    asyncio.run(engine.start(TestMode.QUICK, rd, tick_runner=runner, max_ticks_override=2))
    c = engine.counters
    assert c.orders_attempted > 0          # 주문 *시도* 발생
    assert c.orders_executed == 0          # submitted(=executed) 0 — dry-run
    assert c.errors == 0
    # 주문 시도 이벤트가 order_submitted=False 로 기록됐는지.
    evs = [e for e in get_runtime_event_log().recent(limit=50)
           if e.details.get("order_attempted")]
    assert evs, "order_attempted event missing"
    assert all(e.details["order_submitted"] is False for e in evs)
    assert all(e.details["broker_order_sent"] is False for e in evs)
    assert all(e.details["is_live_authorization"] is False for e in evs)


# ═══════════ 기준 3: exit_plan 없는 BUY 차단 ═══════════


def test_criterion3_exit_plan_less_buy_blocked_real_executor(db):
    """REAL executor (now=OPEN_TIME) — BUY + exit_plan 없음 → 차단, submit 0."""
    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=False, exit_plan={},
    )
    called = {"n": 0}

    async def _route(**kw):
        called["n"] += 1
        return types.SimpleNamespace(decision=None, reasons=[], audit=None)

    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(), broker=MockBrokerAdapter(),
        risk=object(), broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=_route, now=OPEN_TIME,
    ))
    assert res.reason_code == "MISSING_EXIT_PLAN"
    assert res.submitted is False
    assert res.broker_order_sent is False
    assert res.is_live_authorization is False
    assert called["n"] == 0                # route_order 미호출


def test_criterion3_block_event_recorded(db, monkeypatch):
    """runner 가 exit_plan 차단을 BUY_BLOCKED_NO_EXIT_PLAN 이벤트로 기록."""
    runner = _runner(db, action="BUY", exit_plan=False, monkeypatch=monkeypatch,
                     exec_reason="MISSING_EXIT_PLAN", submitted=False)
    asyncio.run(runner(None, None, 0))
    evs = [e for e in get_runtime_event_log().recent(limit=50)
           if e.code == "BUY_BLOCKED_NO_EXIT_PLAN"]
    assert evs, "BUY_BLOCKED_NO_EXIT_PLAN event missing"
    e = evs[0]
    assert e.details["original_action"] == "BUY"
    assert e.details["action"] == "BLOCK"
    assert e.details["order_attempted"] is True
    assert e.details["order_submitted"] is False
    assert e.details["broker_order_sent"] is False
    assert e.details["is_live_authorization"] is False


# ═══════════ 기준 4: Mock 1분 운용 equivalent — 오류 0 + RuntimeEvent > 0 ═══════════


def test_criterion4_mock_one_minute_equivalent_no_errors(db, monkeypatch):
    """압축된 1분 운용 equivalent — live_runner(HOLD) 20 tick, 오류 0, 이벤트>0."""
    runner = _runner(db, action="HOLD", monkeypatch=monkeypatch)
    engine = get_engine()
    rd = evaluate_readiness(_settings())
    # 1분 운용 equivalent: 30초 간격 가정 시 ~20 tick (시간 압축, max_ticks_override).
    asyncio.run(engine.start(TestMode.MOCK, rd, tick_runner=runner, max_ticks_override=20))
    c = engine.counters
    assert c.ticks > 0
    assert c.ai_decisions > 0
    assert c.errors == 0
    assert engine.state == KisPaperRunState.COMPLETED
    # RuntimeEvent (KIS_PAPER_DECISION_*) 기록 > 0.
    decision_events = [e for e in get_runtime_event_log().recent(limit=100)
                       if str(e.code).startswith("KIS_PAPER_DECISION_")]
    assert len(decision_events) > 0
    assert all(e.details.get("price_source") == "kis" for e in decision_events)
