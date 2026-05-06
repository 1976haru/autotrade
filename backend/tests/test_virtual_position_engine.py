"""Virtual Position Engine tests (150, MUST)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.virtual.fill_engine import FillContext, simulate_fill
from app.virtual.order_ledger import (
    STATUS_ACCEPTED,
    STATUS_FILLED,
    STATUS_PARTIALLY_FILLED,
    create_order,
    transition,
)
from app.virtual.position_engine import (
    PositionSummary,
    compute_open_positions,
    evaluate_close,
)


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _filled(db, side, qty, price, *, symbol="005930", strategy=None):
    o = create_order(db, symbol=symbol, side=side, quantity=qty,
                     strategy=strategy, mode="SIMULATION")
    transition(db, o, to_status=STATUS_ACCEPTED)
    transition(db, o, to_status=STATUS_FILLED, filled_delta=qty, avg_fill_price=price)
    return o


# ---------- compute_open_positions ----------

def test_no_orders_yields_empty_positions():
    Session = _session()
    with Session() as db:
        assert compute_open_positions(db) == []


def test_single_buy_creates_open_position():
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 5, 1000, strategy="sma")
        db.commit()
        positions = compute_open_positions(db, last_prices={"005930": 1100})
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol         == "005930"
    assert p.strategy       == "sma"
    assert p.quantity       == 5
    assert p.avg_price      == 1000
    assert p.last_price     == 1100
    assert p.unrealized_pnl == 500
    assert abs(p.unrealized_pct - 0.10) < 0.001


def test_buy_then_full_sell_zeros_position_keeps_realized():
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 5, 1000, strategy="sma")
        _filled(db, "SELL", 5, 1100, strategy="sma")
        db.commit()
        positions = compute_open_positions(db, last_prices={"005930": 1100})
    # 잔여 포지션 없음 — 응답 list에 안 나타남.
    assert positions == []


def test_partial_close_keeps_remainder_with_realized_carried():
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 10, 1000, strategy="sma")
        _filled(db, "SELL", 4, 1100, strategy="sma")
        db.commit()
        positions = compute_open_positions(db, last_prices={"005930": 1050})
    assert len(positions) == 1
    p = positions[0]
    assert p.quantity == 6
    assert p.avg_price == 1000
    # 실현 PnL: (1100 - 1000) * 4 = 400
    assert p.realized_pnl == 400
    # 미실현 PnL: (1050 - 1000) * 6 = 300
    assert p.unrealized_pnl == 300


def test_multiple_buys_weighted_average_price():
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 2, 1000, strategy="sma")
        _filled(db, "BUY", 3, 1200, strategy="sma")
        db.commit()
        positions = compute_open_positions(db, last_prices={"005930": 1300})
    p = positions[0]
    assert p.quantity == 5
    # weighted avg = (2*1000 + 3*1200) / 5 = 1120
    assert p.avg_price == 1120


def test_separates_by_symbol():
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 1, 1000, symbol="A", strategy="s")
        _filled(db, "BUY", 1, 2000, symbol="B", strategy="s")
        db.commit()
        positions = compute_open_positions(
            db, last_prices={"A": 1000, "B": 2000})
    assert {p.symbol for p in positions} == {"A", "B"}


def test_separates_by_strategy():
    """같은 symbol이라도 전략이 다르면 별개 포지션."""
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 1, 1000, strategy="strategy_a")
        _filled(db, "BUY", 1, 1200, strategy="strategy_b")
        db.commit()
        positions = compute_open_positions(db, last_prices={"005930": 1100})
    assert len(positions) == 2
    by_strat = {p.strategy: p for p in positions}
    assert by_strat["strategy_a"].avg_price == 1000
    assert by_strat["strategy_b"].avg_price == 1200


def test_skips_non_filled_orders():
    """ACCEPTED만 된 주문은 포지션에 포함 안 됨 — 체결 데이터 신뢰성."""
    Session = _session()
    with Session() as db:
        o = create_order(db, symbol="005930", side="BUY", quantity=5,
                         strategy="s", mode="SIMULATION")
        transition(db, o, to_status=STATUS_ACCEPTED)
        db.commit()
        assert compute_open_positions(db) == []


def test_partial_filled_status_counted():
    """PARTIALLY_FILLED도 체결 데이터가 신뢰 가능 — 포지션에 포함."""
    Session = _session()
    with Session() as db:
        o = create_order(db, symbol="005930", side="BUY", quantity=10,
                         strategy="s", mode="SIMULATION")
        transition(db, o, to_status=STATUS_ACCEPTED)
        transition(db, o, to_status=STATUS_PARTIALLY_FILLED,
                   filled_delta=4, avg_fill_price=1000)
        db.commit()
        positions = compute_open_positions(db, last_prices={"005930": 1000})
    assert len(positions) == 1
    assert positions[0].quantity == 4


def test_hold_seconds_from_first_buy():
    Session = _session()
    with Session() as db:
        _filled(db, "BUY", 1, 1000, strategy="s")
        db.commit()
        # 약 5분 후 평가.
        future = datetime.now(timezone.utc) + timedelta(seconds=300)
        positions = compute_open_positions(db, last_prices={"005930": 1000},
                                            now=future)
    assert positions[0].hold_seconds >= 300


# ---------- evaluate_close ----------

def _pos(unrealized_pct: float = 0.0, hold_seconds: float = 0.0) -> PositionSummary:
    return PositionSummary(
        symbol="005930", strategy="s", quantity=1, avg_price=1000, last_price=1000,
        unrealized_pnl=0, unrealized_pct=unrealized_pct,
        hold_seconds=hold_seconds, realized_pnl=0,
    )


def test_evaluate_close_no_thresholds_returns_no_action():
    ev = evaluate_close(_pos(unrealized_pct=-0.05))
    assert ev.should_close is False


def test_evaluate_close_stop_loss_triggers_at_threshold():
    ev = evaluate_close(_pos(unrealized_pct=-0.025), stop_loss_pct=2.0)
    assert ev.should_close is True
    assert ev.reason == "stop_loss"


def test_evaluate_close_stop_loss_below_threshold_does_not_trigger():
    ev = evaluate_close(_pos(unrealized_pct=-0.015), stop_loss_pct=2.0)
    assert ev.should_close is False


def test_evaluate_close_take_profit_triggers_at_threshold():
    ev = evaluate_close(_pos(unrealized_pct=+0.05), take_profit_pct=4.0)
    assert ev.should_close is True
    assert ev.reason == "take_profit"


def test_evaluate_close_time_exit_triggers_at_max_hold():
    ev = evaluate_close(_pos(hold_seconds=400), max_hold_seconds=300)
    assert ev.should_close is True
    assert ev.reason == "time_exit"


def test_evaluate_close_stop_loss_priority_over_others():
    """동시 트리거 시 stop_loss가 우선 — 손실 방어가 가장 중요한 신호."""
    ev = evaluate_close(_pos(unrealized_pct=-0.05, hold_seconds=400),
                         stop_loss_pct=2.0, take_profit_pct=10.0,
                         max_hold_seconds=300)
    assert ev.reason == "stop_loss"


def test_evaluate_close_zero_or_negative_threshold_skipped():
    ev = evaluate_close(_pos(unrealized_pct=-0.5),
                         stop_loss_pct=0, take_profit_pct=-1,
                         max_hold_seconds=0)
    assert ev.should_close is False
