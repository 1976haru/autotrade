"""KIS 모의 자동매매 *실제 tick* 흐름 테스트 (요청: test_kis_paper_real_tick.py).

검증 대상 5단계 흐름 (engine.py 가 아니라 *주입형* live_runner 가 수행 — engine 은
정적 가드상 broker/route_order 를 import 하지 않으므로 caller 가 runner 주입):

  1) 매 tick 현재가 조회        → broker.get_price (KIS 모의 시세, read-only)
  2) 4전략(ORB/MOMENTUM/GAP/VWAP) 신호 → evaluate_all_strategies (run_agent_council 내부)
  3) Agent Council 최종 BUY/SELL/HOLD   → run_agent_council
  4) BUY/SELL → route_order → KisBrokerAdapter.place_order(is_paper=True)
                              → execute_kis_paper_auto_order (route_order 위임)
  5) 체결 결과 → OrderAuditLog            → route_order 단일 진입점이 기록

불변(테스트로 lock):
  - RiskManager / PermissionGate 우회 0건 (route_order 경유).
  - exit_plan 없는 BUY 는 차단 (auto_permission MISSING_EXIT_PLAN).
  - 실거래 차단 유지 (KisBrokerAdapter live place_order → NotImplementedError,
    assert_paper_broker 백스톱, ENABLE_LIVE_TRADING mutate 0건).

broker / KIS 실 API / 실제 route_order 호출 0건 — fake broker(get_price) +
fake route_order_fn + in-memory SQLite + 일부 monkeypatch 로 흐름만 검증.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.brokers.kis import KisBrokerAdapter
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.base import Base
from app.execution.paper_trader import NotPaperBrokerError
from app.kis_paper import live_runner as lr
from app.kis_paper.auto_executor import KisPaperAutoDecision, execute_kis_paper_auto_order
from app.risk.risk_manager import RiskDecision


# ─────────── 공통 fixtures / helpers (pipeline 테스트와 동일 패턴) ───────────

_KST = timezone(timedelta(hours=9))


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_KST).astimezone(timezone.utc)


OPEN_TIME = _utc(2026, 5, 22, 10, 0)   # 금 10:00 KST (장중, 창 안)


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
    return SimpleNamespace(**base)


class _Quote:
    def __init__(self, price): self.price = price


class _FakeKisPaperBroker:
    """KIS 모의 어댑터 흉내 — get_price 만, place_order 없음."""

    def __init__(self, prices):
        self.is_paper = True
        self._prices = prices
        self.calls = 0

    async def get_price(self, symbol):
        p = self._prices[min(self.calls, len(self._prices) - 1)]
        self.calls += 1
        return _Quote(p)


class _Action:
    def __init__(self, value): self.value = value


class _FakeDecision:
    """run_agent_council 반환 흉내 — to_kis_paper_decision 으로 변환."""

    def __init__(self, action, has_exit_plan=True):
        self.final_action = _Action(action)
        self._has_exit = has_exit_plan

    def to_kis_paper_decision(self, *, quantity, price):
        if self.final_action.value == "HOLD":
            return None
        return KisPaperAutoDecision(
            symbol="005930", side=self.final_action.value,
            quantity=quantity, price=price, selected_strategies=["MOMENTUM"],
            confidence=0.8, quality_score=80, entry_reason="t",
            has_exit_plan=self._has_exit,
            exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0} if self._has_exit else {},
        )


def _capture_route(decision=RiskDecision.APPROVED, *, executed=True):
    calls = {"n": 0, "orders": []}

    async def _fn(*, order, **kw):
        calls["n"] += 1
        calls["orders"].append(order)
        audit = SimpleNamespace(
            id=1, broker_order_id="PAPER-0001" if executed else None,
            broker_status="FILLED" if executed else None,
            filled_quantity=order.quantity if executed else 0, executed=executed,
        )
        return SimpleNamespace(decision=decision, reasons=[], audit=audit)

    return _fn, calls


# ═══════════ 1) 실제 council 파이프라인이 매 tick 돈다 (steps 1-3) ═══════════


def test_real_tick_runs_full_pipeline_each_tick(db, monkeypatch):
    """fake KIS 시세 + *진짜* run_agent_council 로 매 tick 판단이 생성됨."""
    captured = {"decisions": 0}

    async def _fake_exec(db_, *, decision, **kw):
        captured["decisions"] += 1
        return SimpleNamespace(reason_code="KIS_PAPER_DRY_RUN_OK", submitted=False,
                               fill_status=None, reason_message="dry")

    # 실제 route/KIS 를 타지 않도록 executor 만 캡처. council 은 진짜 실행.
    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)

    broker = _FakeKisPaperBroker([70000, 70200, 70500, 70900, 71400, 72000])
    runner, cleanup = lr.build_kis_paper_tick_runner(
        db=db, broker=broker, risk=object(), settings=_settings(),
        credentials_present=True,
    )
    results = [asyncio.run(runner(None, None, i)) for i in range(6)]
    cleanup()

    assert sum(r["ai_decisions"] for r in results) == 6      # 매 tick 판단 1회
    assert sum(r["errors"] for r in results) == 0            # 예외 0
    assert broker.calls == 6                                 # 매 tick 시세 조회
    # BUY+SELL+HOLD = 6 (모든 tick 이 유효 action 으로 분류)
    assert sum(r["ai_buy_signals"] + r["ai_sell_signals"] + r["ai_hold_signals"]
               for r in results) == 6


# ═══════════ 2) BUY 결정 → executor(route_order) 로 위임 (step 4 wiring) ═══════


def test_buy_decision_routes_to_executor(db, monkeypatch):
    monkeypatch.setattr(lr, "run_agent_council",
                        lambda mi, **kw: _FakeDecision("BUY", has_exit_plan=True))
    seen = {"n": 0, "side": None}

    async def _fake_exec(db_, *, decision, route_order_fn, **kw):
        seen["n"] += 1
        seen["side"] = decision.side
        # route_order_fn 이 sanctioned route_order 인지 (우회 아님) 확인.
        assert route_order_fn is lr.route_order
        return SimpleNamespace(reason_code=lr.KIS_PAPER_SUBMITTED, submitted=True,
                               fill_status="FILLED", reason_message="ok")

    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    broker = _FakeKisPaperBroker([70000])
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=broker, risk=object(), settings=_settings(),
        credentials_present=True,
    )
    out = asyncio.run(runner(None, None, 0))
    assert seen["n"] == 1 and seen["side"] == "BUY"
    assert out["orders_attempted"] == 1 and out["orders_executed"] == 1
    assert out["fills_observed"] == 1


def test_hold_decision_sends_no_order(db, monkeypatch):
    monkeypatch.setattr(lr, "run_agent_council",
                        lambda mi, **kw: _FakeDecision("HOLD"))
    called = {"n": 0}

    async def _fake_exec(*a, **k):
        called["n"] += 1
        return SimpleNamespace(reason_code="x", submitted=False, fill_status=None,
                               reason_message="")

    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)
    runner, _c = lr.build_kis_paper_tick_runner(
        db=db, broker=_FakeKisPaperBroker([70000]), risk=object(),
        settings=_settings(), credentials_present=True,
    )
    out = asyncio.run(runner(None, None, 0))
    assert called["n"] == 0
    assert out["orders_attempted"] == 0 and out["ai_hold_signals"] == 1


# ═══════════ 3) exit_plan 없는 BUY 는 *실제* 권한 게이트가 차단 ═══════════


def test_exit_plan_less_buy_blocked_by_permission_gate(db):
    """REAL execute_kis_paper_auto_order — BUY + exit_plan 없음 → MISSING_EXIT_PLAN,
    route_order 호출 0건 (주문 전 차단)."""
    route, calls = _capture_route()
    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=False, exit_plan={},
    )
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(), broker=MockBrokerAdapter(),
        risk=object(), broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route, now=OPEN_TIME,
    ))
    assert res.reason_code == "MISSING_EXIT_PLAN"
    assert res.submitted is False
    assert res.broker_order_sent is False
    assert calls["n"] == 0           # route_order 까지 도달하지 않음


def test_buy_with_exit_plan_dry_run_passes_gate_without_order(db):
    """dry_run=True → 게이트 통과 검증만, route_order/KIS 호출 0건."""
    route, calls = _capture_route()
    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
    )
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(kis_paper_auto_order_dry_run=True),
        broker=MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route, now=OPEN_TIME,
    ))
    assert res.reason_code == "KIS_PAPER_DRY_RUN_OK"
    assert res.submitted is False
    assert calls["n"] == 0


# ═══════════ 4) BUY(+exit_plan) → route_order 경유 실제 위임 (steps 4-5) ═══════


def test_buy_with_exit_plan_executes_via_route_order(db):
    """enabled + not dry_run + 게이트 통과 → route_order 1회 호출 → SUBMITTED."""
    route, calls = _capture_route(RiskDecision.APPROVED, executed=True)
    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
    )
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(), broker=MockBrokerAdapter(),
        risk=object(), broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route, now=OPEN_TIME,
    ))
    assert calls["n"] == 1                              # route_order 단일 진입점 경유
    assert res.reason_code == "KIS_PAPER_SUBMITTED"
    assert res.submitted is True
    assert res.broker_order_type == "KIS_PAPER"
    assert res.is_live_authorization is False
    # 주문이 BUY MARKET 로 라우팅됐는지 (audit/route 입력 확인)
    o = calls["orders"][0]
    assert o.side == OrderSide.BUY and o.order_type == OrderType.MARKET


def test_rejected_route_maps_not_submitted(db):
    route, calls = _capture_route(RiskDecision.REJECTED, executed=False)
    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
    )
    res = asyncio.run(execute_kis_paper_auto_order(
        db, decision=dec, settings=_settings(), broker=MockBrokerAdapter(),
        risk=object(), broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route, now=OPEN_TIME,
    ))
    assert calls["n"] == 1
    assert res.submitted is False


# ═══════════ 5) 실거래 차단 (no bypass / paper-only) ═══════════


def test_live_broker_blocked_by_paper_backstop(db):
    """게이트 통과 후라도 broker 가 live 면 assert_paper_broker 가 차단."""
    route, calls = _capture_route()
    dec = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=1, price=70000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        entry_reason="t", has_exit_plan=True,
        exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
    )
    with pytest.raises(NotPaperBrokerError):
        asyncio.run(execute_kis_paper_auto_order(
            db, decision=dec, settings=_settings(),
            broker=KisBrokerAdapter(is_paper=False), risk=object(),
            broker_is_kis_paper=True, credentials_present=True,
            route_order_fn=route, now=OPEN_TIME,
        ))
    assert calls["n"] == 0           # route_order 까지 도달 0건


def test_kis_live_place_order_not_implemented():
    """KisBrokerAdapter(is_paper=False).place_order → NotImplementedError (실거래 차단)."""
    adapter = KisBrokerAdapter(is_paper=False)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET)
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.place_order(order))


# ═══════════ 6) ENABLE_LIVE_TRADING / engine 안전 불변 ═══════════


def test_live_runner_does_not_mutate_safety_flags():
    import pathlib
    src = pathlib.Path(lr.__file__).read_text(encoding="utf-8")
    for banned in (".place_order(", ".cancel_order(",
                   "enable_live_trading =", "enable_live_trading=True",
                   "settings.enable_live_trading"):
        # mutate/set 금지 (읽기/주석 제외 — 본 모듈은 안전 flag 를 *변경*하지 않음).
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            assert banned not in code, f"banned {banned!r}: {line!r}"


def test_engine_default_runner_stays_broker_free():
    """engine.py 가 broker/route_order 를 import 하지 않음 (counter-only 설계 유지)."""
    import pathlib
    eng = (pathlib.Path(lr.__file__).parent / "engine.py").read_text(encoding="utf-8")
    for banned in ("from app.execution.order_router import",
                   "from app.brokers.kis import", ".place_order(", "route_order("):
        assert banned not in eng, f"engine.py must stay broker-free: {banned!r}"
