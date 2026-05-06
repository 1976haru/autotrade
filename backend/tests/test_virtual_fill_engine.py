"""Virtual Fill Engine tests (149, MUST)."""

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
    STATUS_REJECTED,
    create_order,
    transition,
)


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _accepted(db, **overrides):
    """ACCEPTED 상태 주문 생성 헬퍼."""
    defaults = dict(symbol="005930", side="BUY", quantity=10, mode="SIMULATION")
    defaults.update(overrides)
    o = create_order(db, **defaults)
    transition(db, o, to_status=STATUS_ACCEPTED)
    return o


def _ctx(**overrides):
    base = dict(
        quote_price=1000,
        quote_timestamp=datetime.now(timezone.utc),
        bar_volume=1000,
        emergency_stop_enabled=False,
        stale_max_age_seconds=60,
        slippage_bps=0,  # 슬리피지는 별도 테스트에서만 활성
    )
    base.update(overrides)
    return FillContext(**base)


# ---------- MARKET buy/sell, full fill ----------

def test_market_buy_fully_filled_at_market_price_when_no_slippage():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=5, order_type="MARKET")
        outcome = simulate_fill(db, order, _ctx(quote_price=1000, bar_volume=100))
        assert outcome.final_status   == STATUS_FILLED
        assert outcome.filled_delta   == 5
        assert outcome.fill_price     == 1000
        assert order.filled_quantity  == 5
        assert order.avg_fill_price   == 1000


def test_market_buy_with_slippage_pays_more():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=1, order_type="MARKET", side="BUY")
        # 1000 * 50bps(0.5%) = 5 → fill_price 1005.
        outcome = simulate_fill(db, order, _ctx(quote_price=1000, slippage_bps=50))
        assert outcome.fill_price == 1005


def test_market_sell_with_slippage_receives_less():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=1, order_type="MARKET", side="SELL")
        outcome = simulate_fill(db, order, _ctx(quote_price=1000, slippage_bps=50))
        assert outcome.fill_price == 995


# ---------- partial fill ----------

def test_partial_fill_when_volume_less_than_remaining():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10)
        outcome = simulate_fill(db, order, _ctx(quote_price=1000, bar_volume=4))
        assert outcome.final_status == STATUS_PARTIALLY_FILLED
        assert outcome.filled_delta == 4
        assert order.filled_quantity == 4

        # 다음 봉에서 잔량 6 체결 시도 — 거래량 충분.
        outcome2 = simulate_fill(db, order, _ctx(quote_price=1010, bar_volume=10))
        assert outcome2.final_status == STATUS_FILLED
        assert outcome2.filled_delta == 6
        # weighted average: (1000*4 + 1010*6) / 10 = 1006
        assert order.avg_fill_price == 1006


def test_no_volume_keeps_order_unfilled():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10)
        outcome = simulate_fill(db, order, _ctx(bar_volume=0))
        assert outcome.final_status == STATUS_ACCEPTED
        assert outcome.filled_delta == 0
        assert outcome.structured_reason == "no_volume"


# ---------- LIMIT order ----------

def test_limit_buy_does_not_cross_at_higher_market():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10, order_type="LIMIT", limit_price=900)
        # 시장가 1000 > 한도 900 → 미체결.
        outcome = simulate_fill(db, order, _ctx(quote_price=1000, bar_volume=100))
        assert outcome.final_status == STATUS_ACCEPTED
        assert outcome.structured_reason == "limit_not_crossed"


def test_limit_buy_crosses_at_or_below_limit():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10, order_type="LIMIT", limit_price=1000)
        outcome = simulate_fill(db, order, _ctx(quote_price=900, bar_volume=100))
        assert outcome.final_status == STATUS_FILLED
        assert outcome.fill_price   == 900   # LIMIT은 slippage 미적용


def test_limit_sell_does_not_cross_at_lower_market():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10, side="SELL",
                          order_type="LIMIT", limit_price=1100)
        outcome = simulate_fill(db, order, _ctx(quote_price=1000))
        assert outcome.final_status == STATUS_ACCEPTED
        assert outcome.structured_reason == "limit_not_crossed"


def test_limit_sell_crosses_at_or_above_limit():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10, side="SELL",
                          order_type="LIMIT", limit_price=1000)
        outcome = simulate_fill(db, order, _ctx(quote_price=1100, bar_volume=100))
        assert outcome.final_status == STATUS_FILLED
        assert outcome.fill_price   == 1100


# ---------- safety guards ----------

def test_emergency_stop_rejects_fill():
    """060 invariant: emergency_stop이면 어떤 단계에서도 체결 차단."""
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10)
        outcome = simulate_fill(db, order, _ctx(emergency_stop_enabled=True))
        assert outcome.final_status == STATUS_REJECTED
        assert outcome.structured_reason == "emergency_stop"
        assert order.filled_quantity == 0


def test_stale_price_rejects_fill():
    """143 invariant: 시세가 stale이면 fill 거부."""
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10)
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        outcome = simulate_fill(db, order, _ctx(quote_timestamp=old, stale_max_age_seconds=60))
        assert outcome.final_status == STATUS_REJECTED
        assert outcome.structured_reason == "stale_price"


def test_naive_quote_timestamp_treated_as_utc():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10)
        old_naive = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(tzinfo=None)
        outcome = simulate_fill(db, order, _ctx(quote_timestamp=old_naive,
                                                stale_max_age_seconds=60))
        assert outcome.final_status == STATUS_REJECTED


def test_stale_check_disabled_when_threshold_zero():
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=10)
        very_old = datetime.now(timezone.utc) - timedelta(days=365)
        outcome = simulate_fill(db, order, _ctx(quote_timestamp=very_old,
                                                stale_max_age_seconds=0))
        assert outcome.final_status == STATUS_FILLED


# ---------- safety: not-accepted orders ----------

def test_simulate_fill_no_op_on_terminal_order():
    """이미 FILLED된 주문에 다시 simulate_fill 호출 — NO-OP."""
    Session = _session()
    with Session() as db:
        order = _accepted(db, quantity=5)
        simulate_fill(db, order, _ctx())  # 첫 체결로 FILLED
        outcome = simulate_fill(db, order, _ctx())  # 두 번째 호출
        assert outcome.filled_delta == 0
        assert outcome.structured_reason == "not_accepted"


def test_simulate_fill_no_op_on_new_order():
    """ACCEPTED를 거치지 않은 NEW 상태 주문 — NO-OP (caller 책임)."""
    Session = _session()
    with Session() as db:
        order = create_order(db, symbol="005930", side="BUY", quantity=5,
                             mode="SIMULATION")
        outcome = simulate_fill(db, order, _ctx())
        assert outcome.filled_delta == 0
        assert outcome.structured_reason == "not_accepted"
        assert order.status == "NEW"  # transition 안 일어남
