import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.strategies.base import Strategy
from app.strategies.concrete.sma_crossover import SmaCrossoverStrategy
from app.backtest.types import Bar, Signal
from app.brokers.base import OrderSide, OrderType
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from app.strategies.live_engine import LiveStrategyEngine, TickResult


def _make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def run(coro):
    return asyncio.run(coro)


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, close: int, symbol: str = "005930") -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=_BASE + timedelta(days=i),
        open=close, high=close, low=close, close=close, volume=1,
    )


class _FixedSignals(Strategy):
    def __init__(self, signals: list[Signal]):
        self._signals = list(signals)
        self._idx = 0

    def on_bar(self, bars):
        s = self._signals[self._idx] if self._idx < len(self._signals) else Signal.HOLD
        self._idx += 1
        return s


def test_quantity_must_be_positive():
    with pytest.raises(ValueError):
        LiveStrategyEngine(SmaCrossoverStrategy(), quantity=0)
    with pytest.raises(ValueError):
        LiveStrategyEngine(SmaCrossoverStrategy(), quantity=-1)


def test_start_and_stop_raise_not_implemented():
    eng = LiveStrategyEngine(SmaCrossoverStrategy())
    with pytest.raises(NotImplementedError, match="follow-up"):
        eng.start()
    with pytest.raises(NotImplementedError, match="follow-up"):
        eng.stop()


def test_hold_signal_yields_no_intended_order():
    eng = LiveStrategyEngine(_FixedSignals([Signal.HOLD]), quantity=1)
    result = eng.run_tick(_bar(0, 100))
    assert isinstance(result, TickResult)
    assert result.signal == Signal.HOLD
    assert result.intended_order is None
    assert eng.holding is False


def test_buy_signal_creates_market_buy_when_flat():
    eng = LiveStrategyEngine(_FixedSignals([Signal.BUY]), quantity=5)
    result = eng.run_tick(_bar(0, 100))
    assert result.signal == Signal.BUY
    order = result.intended_order
    assert order is not None
    assert order.side == OrderSide.BUY
    assert order.quantity == 5
    assert order.order_type == OrderType.MARKET
    assert eng.holding is True


def test_repeated_buy_does_not_stack_position():
    eng = LiveStrategyEngine(_FixedSignals([Signal.BUY, Signal.BUY, Signal.BUY]))
    first = eng.run_tick(_bar(0, 100))
    second = eng.run_tick(_bar(1, 110))
    third = eng.run_tick(_bar(2, 120))
    assert first.intended_order is not None
    assert second.intended_order is None
    assert third.intended_order is None


def test_sell_without_position_is_ignored():
    eng = LiveStrategyEngine(_FixedSignals([Signal.SELL, Signal.SELL]))
    a = eng.run_tick(_bar(0, 100))
    b = eng.run_tick(_bar(1, 95))
    assert a.intended_order is None
    assert b.intended_order is None
    assert eng.holding is False


def test_buy_then_sell_round_trip_emits_two_orders():
    eng = LiveStrategyEngine(_FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL]))
    r0 = eng.run_tick(_bar(0, 100))
    r1 = eng.run_tick(_bar(1, 110))
    r2 = eng.run_tick(_bar(2, 120))
    assert r0.intended_order is not None and r0.intended_order.side == OrderSide.BUY
    assert r1.intended_order is None
    assert r2.intended_order is not None and r2.intended_order.side == OrderSide.SELL
    assert eng.holding is False


def test_bars_seen_counter_increments():
    eng = LiveStrategyEngine(_FixedSignals([Signal.HOLD] * 4))
    assert eng.bars_seen == 0
    for i in range(4):
        eng.run_tick(_bar(i, 100))
    assert eng.bars_seen == 4


def test_works_with_real_sma_strategy_after_warmup():
    eng = LiveStrategyEngine(SmaCrossoverStrategy(short=2, long=4), quantity=10)
    closes = [100, 99, 98, 97, 100, 105, 110]
    results = [eng.run_tick(_bar(i, c)) for i, c in enumerate(closes)]
    # Warmup: HOLD until long window full
    for r in results[:3]:
        assert r.signal == Signal.HOLD
        assert r.intended_order is None
    # SMA crossover eventually fires BUY on rising prices
    has_buy = any(r.intended_order is not None and r.intended_order.side == OrderSide.BUY
                  for r in results)
    assert has_buy, "expected at least one BUY signal once SMA crosses"


# ---------- submit_tick: pipeline integration ----------

def test_submit_tick_without_dependencies_raises():
    eng = LiveStrategyEngine(_FixedSignals([Signal.BUY]), quantity=1)
    with pytest.raises(RuntimeError, match="broker, risk, db, and mode"):
        run(eng.submit_tick(_bar(0, 75_000)))


def test_submit_tick_passes_through_when_signal_is_hold():
    Session = _make_session()
    with Session() as db:
        eng = LiveStrategyEngine(
            _FixedSignals([Signal.HOLD]),
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, mode=OperationMode.SIMULATION,
        )
        result = run(eng.submit_tick(_bar(0, 75_000)))
        assert result.signal == Signal.HOLD
        assert result.intended_order is None
        assert result.routing is None
        assert eng.holding is False


def test_submit_tick_simulation_mode_executes_order():
    Session = _make_session()
    with Session() as db:
        eng = LiveStrategyEngine(
            _FixedSignals([Signal.BUY]), quantity=1,
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, mode=OperationMode.SIMULATION,
        )
        result = run(eng.submit_tick(_bar(0, 75_000)))
        assert result.intended_order is not None
        assert result.routing is not None
        assert result.routing.decision == RiskDecision.APPROVED
        assert result.routing.result.status.value == "FILLED"
        assert eng.holding is True


def test_submit_tick_shadow_mode_rejection_rolls_back_position_state():
    Session = _make_session()
    with Session() as db:
        eng = LiveStrategyEngine(
            _FixedSignals([Signal.BUY]),
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, mode=OperationMode.LIVE_SHADOW,
        )
        result = run(eng.submit_tick(_bar(0, 75_000)))
        assert result.routing.decision == RiskDecision.REJECTED
        # Position state was rolled back so the next BUY signal can fire again
        assert eng.holding is False


def test_submit_tick_manual_approval_mode_enqueues():
    Session = _make_session()
    with Session() as db:
        eng = LiveStrategyEngine(
            _FixedSignals([Signal.BUY]),
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, mode=OperationMode.LIVE_MANUAL_APPROVAL,
        )
        result = run(eng.submit_tick(_bar(0, 75_000)))
        assert result.routing.decision == RiskDecision.NEEDS_APPROVAL
        assert result.routing.approval is not None
        assert result.routing.approval.status == "PENDING"


def test_submit_tick_default_requested_by_ai_is_false():
    Session = _make_session()
    with Session() as db:
        eng = LiveStrategyEngine(
            _FixedSignals([Signal.BUY]),
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, mode=OperationMode.SIMULATION,
        )
        result = run(eng.submit_tick(_bar(0, 75_000)))
        assert result.routing.audit.requested_by_ai is False


def test_submit_tick_explicit_requested_by_ai_propagates():
    Session = _make_session()
    with Session() as db:
        eng = LiveStrategyEngine(
            _FixedSignals([Signal.BUY]),
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, mode=OperationMode.SIMULATION,
        )
        result = run(eng.submit_tick(_bar(0, 75_000), requested_by_ai=True))
        assert result.routing.audit.requested_by_ai is True
