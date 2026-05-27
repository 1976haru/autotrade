"""KIS-PAPER-CONDITIONAL-ORDER — 조건 충족 시 KIS 모의주문 전송 / 미충족 시 HOLD·BLOCK.

핵심 안전 규칙:
  - 실제 KIS 모의주문은 dry_run_preview=False AND paper_order_confirm=True 일 때만.
  - 그 외(둘 중 하나라도 미충족) → force_dry_run=True → 주문 전송 0건.
  - 조건 충족 + market open + 게이트 통과 → SUBMITTED (KisBrokerAdapter is_paper=True).
  - market closed / KIS rejection → 주문 미전송 + 사유 기록.

모든 경로는 execute_kis_paper_auto_order → route_order → RiskManager →
PermissionGate → OrderExecutor → KisBrokerAdapter(is_paper=True). broker.place_order
직접 호출 0건. 실전 주문 0건.
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
from app.risk.risk_manager import RiskDecision
from app.system.event_log import get_runtime_event_log, reset_runtime_event_log_for_tests


_KST = timezone(timedelta(hours=9))


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_KST).astimezone(timezone.utc)


OPEN_TIME = _utc(2026, 5, 22, 10, 0)     # 장중
CLOSED_TIME = _utc(2026, 5, 22, 16, 0)   # 장 마감 후


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_log():
    reset_runtime_event_log_for_tests()
    yield
    reset_runtime_event_log_for_tests()


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


class _Quote:
    def __init__(self, p): self.price = p


class _FakeKisBroker:
    def __init__(self, price=70000):
        self.is_paper = True
        self._p = price

    async def get_price(self, symbol):
        return _Quote(self._p)


class _Act:
    def __init__(self, v): self.value = v


class _FakeDecision:
    def __init__(self, action="BUY", exit_plan=True):
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


def _build_runner(db, *, force_dry_run, monkeypatch, exec_reason, submitted, action="BUY"):
    monkeypatch.setattr(lr, "run_agent_council", lambda mi, **kw: _FakeDecision(action))

    async def _fake_exec(db_, *, decision, **kw):
        return types.SimpleNamespace(reason_code=exec_reason, submitted=submitted,
                                     fill_status="FILLED" if submitted else None,
                                     reason_message=exec_reason)
    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisBroker(), risk=object(), settings=_settings(),
        credentials_present=True, force_dry_run=force_dry_run,
    )
    return runner


# ═══════════ force_dry_run 진리표 (route 게이팅 로직) ═══════════


def test_force_dry_run_truth_table():
    """실제 주문은 dry_run_preview=False AND paper_order_confirm=True 일 때만."""
    def force_dry_run(dry_run_preview, paper_order_confirm):
        return bool(dry_run_preview) or not bool(paper_order_confirm)

    assert force_dry_run(False, True) is False     # 유일하게 실주문 허용
    assert force_dry_run(False, False) is True      # 미동의 → dry-run
    assert force_dry_run(True, True) is True         # preview 우선 → dry-run
    assert force_dry_run(True, False) is True


# ═══════════ 조건 충족 → KIS 모의주문 전송 (SUBMITTED) ═══════════


def test_conditional_order_submits_when_confirmed(db, monkeypatch):
    """force_dry_run=False + BUY + 게이트 통과 → SUBMITTED, order_submitted=True."""
    runner = _build_runner(db, force_dry_run=False, monkeypatch=monkeypatch,
                           exec_reason=lr.KIS_PAPER_SUBMITTED, submitted=True)
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_attempted"] == 1
    assert out["orders_executed"] == 1
    assert out["fills_observed"] == 1
    evs = [e for e in get_runtime_event_log().recent(limit=50)
           if e.details.get("order_attempted")]
    assert evs and evs[0].details["order_submitted"] is True
    assert evs[0].details["broker_order_sent"] is True
    assert evs[0].details["is_live_authorization"] is False


def test_no_order_when_not_confirmed(db, monkeypatch):
    """force_dry_run=True (미동의) → DRY_RUN_OK, 주문 전송 0건."""
    runner = _build_runner(db, force_dry_run=True, monkeypatch=monkeypatch,
                           exec_reason="KIS_PAPER_DRY_RUN_OK", submitted=False)
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_attempted"] == 1
    assert out["orders_executed"] == 0
    evs = [e for e in get_runtime_event_log().recent(limit=50)
           if e.details.get("order_attempted")]
    assert evs and evs[0].details["order_submitted"] is False
    assert evs[0].details["broker_order_sent"] is False


# ═══════════ REAL executor: 조건 충족 시 route_order 경유 전송 ═══════════


def test_real_executor_submits_via_route_order_when_open(db):
    """market open + 게이트 통과 + dry_run=False → route_order 1회 → SUBMITTED."""
    calls = {"n": 0}

    async def _route(*, order, **kw):
        calls["n"] += 1
        audit = types.SimpleNamespace(id=1, broker_order_id="PAPER-1",
                                      broker_status="FILLED", filled_quantity=1, executed=True)
        return types.SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)

    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0})
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(), broker=MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=_route, now=OPEN_TIME))
    assert calls["n"] == 1
    assert res.reason_code == "KIS_PAPER_SUBMITTED"
    assert res.submitted is True
    assert res.is_live_authorization is False


# ═══════════ 조건 미충족 → 주문 차단 ═══════════


def test_market_closed_blocks_order(db):
    """장 마감(now=CLOSED) → 주문 미전송, route_order 0회."""
    calls = {"n": 0}

    async def _route(**kw):
        calls["n"] += 1
        return types.SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=None)

    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0})
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(), broker=MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=_route, now=CLOSED_TIME))
    assert res.submitted is False
    assert res.broker_order_sent is False
    assert calls["n"] == 0                      # 주문 라우터 미도달


def test_kis_rejection_records_not_submitted(db, monkeypatch):
    """KIS 모의 거절 → orders_rejected, order_submitted=False."""
    runner = _build_runner(db, force_dry_run=False, monkeypatch=monkeypatch,
                           exec_reason=lr.KIS_PAPER_REJECTED, submitted=False)
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_rejected"] == 1
    assert out["orders_executed"] == 0
    evs = [e for e in get_runtime_event_log().recent(limit=50)
           if e.details.get("order_attempted")]
    assert evs and evs[0].details["order_submitted"] is False
    assert evs[0].details["action"] == "BLOCK"
