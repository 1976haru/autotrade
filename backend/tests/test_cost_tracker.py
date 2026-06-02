"""STEP 4 (2026-06-03): 거래 비용 추적기 테스트. 순수 계산, 실주문 0건."""

import pytest

from app.reporting.cost_tracker import (
    ROUND_TRIP_COST_BPS,
    augment_trades_with_costs,
    compute_trade_cost,
    daily_cost_summary,
    estimate_cost_for_frequency,
)
from app.reporting.trade_lifecycle import TradeRow


def _closed(symbol="A", buy=10_000, sell=10_500, qty=10, gross=None, strategy="VWAP"):
    if gross is None:
        gross = (sell - buy) * qty
    return TradeRow(
        symbol=symbol, strategy=strategy, status="CLOSED", quantity=qty,
        buy_time="2026-06-02T00:10:00", buy_price=buy,
        sell_time="2026-06-02T00:40:00", sell_price=sell, holding_minutes=30.0,
        gross_pnl_krw=gross, gross_pnl_pct=round((sell / buy - 1) * 100, 4),
        price_basis="FILL", buy_reason="VWAP",
    )


def test_round_trip_bps_is_33():
    assert ROUND_TRIP_COST_BPS == pytest.approx(33.0)


def test_compute_trade_cost_components():
    # buy 1,000,000 / sell 1,000,000 (qty implied by price*qty)
    c = compute_trade_cost(buy_price=100_000, sell_price=100_000, quantity=10)
    # notional each = 1,000,000
    assert c.buy_commission == round(1_000_000 * 1.5 / 1e4)      # 150
    assert c.sell_commission == round(1_000_000 * 1.5 / 1e4)     # 150
    assert c.sell_tax == round(1_000_000 * 20.0 / 1e4)           # 2000
    assert c.slippage == round(1_000_000 * 5 / 1e4) * 2          # 500*2=1000
    # total = 150+150+2000+1000 = 3300  (= 1,000,000 * 33bps)
    assert c.total_cost_krw == 3300


def test_open_trade_has_only_buy_side_cost():
    c = compute_trade_cost(buy_price=100_000, sell_price=None, quantity=10)
    assert c.sell_tax == 0 and c.sell_commission == 0
    assert c.buy_commission == 150
    assert c.slippage == 500   # buy side only


def test_augment_adds_net_pnl_after_cost():
    rows = [_closed(buy=100_000, sell=101_000, qty=10)]  # gross = 10,000
    aug = augment_trades_with_costs(rows)
    d = aug[0]
    # cost: buy_notional 1,000,000 ; sell_notional 1,010,000
    cost = d["total_cost_krw"]
    assert d["net_pnl_krw"] == 10_000 - cost
    assert cost > 0


def test_daily_cost_summary_ratio_and_net():
    rows = [
        _closed(symbol="A", buy=100_000, sell=101_000, qty=10),  # gross +10,000
        _closed(symbol="B", buy=100_000, sell=100_500, qty=10),  # gross +5,000
    ]
    s = daily_cost_summary(rows)
    assert s["closed_trades"] == 2
    assert s["gross_pnl_krw"] == 15_000
    assert s["total_cost_krw"] > 0
    assert s["net_pnl_krw"] == s["gross_pnl_krw"] - s["total_cost_krw"]
    # cost/profit ratio present when gross > 0
    assert s["cost_to_gross_profit_pct"] == pytest.approx(
        s["total_cost_krw"] / s["gross_pnl_krw"] * 100, abs=0.01)


def test_cost_can_exceed_thin_profit_costwall():
    """얇은 수익(작은 변동)일수록 비용이 수익을 넘을 수 있음 — 비용벽."""
    rows = [_closed(buy=100_000, sell=100_100, qty=10)]  # gross +1,000 (0.1%)
    s = daily_cost_summary(rows)
    assert s["net_pnl_krw"] < 0           # cost (≈3,300) > gross (1,000)
    assert s["cost_to_gross_profit_pct"] > 100


def test_summary_ratio_none_when_no_gross_profit():
    rows = [_closed(buy=100_000, sell=99_000, qty=10)]  # gross negative
    s = daily_cost_summary(rows)
    assert s["cost_to_gross_profit_pct"] is None


def test_estimate_cost_for_frequency():
    e = estimate_cost_for_frequency(avg_trade_notional_krw=5_000_000,
                                    round_trips_per_day=8, trading_days=20)
    # one round trip ≈ 5,000,000 * 33bps = 16,500
    assert e["round_trip_cost_krw"] == 16_500
    assert e["estimated_cost_per_day_krw"] == 16_500 * 8
    assert e["estimated_cost_total_krw"] == 16_500 * 8 * 20


# ---------- API ----------

def test_api_cost_estimate_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    res = client.get("/api/reporting/cost-estimate"
                     "?avg_trade_notional_krw=5000000&round_trips_per_day=8&trading_days=20")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["estimated_cost_total_krw"] == 16_500 * 8 * 20
    assert body["is_order_signal"] is False


def test_api_cost_summary_endpoint():
    from datetime import datetime, timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.db.models import OrderAuditLog
    from app.db.session import get_db
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    t = datetime(2026, 6, 2, 0, 10)
    with S() as db:
        for side, px, dt in (("BUY", 100_000, t), ("SELL", 101_000, t + timedelta(minutes=20))):
            db.add(OrderAuditLog(
                created_at=dt, mode="PAPER", requested_by_ai=False, symbol="005930",
                side=side, quantity=10, order_type="MARKET", latest_price=px,
                decision="APPROVED", reasons=["VWAP"], executed=True, filled_quantity=10,
                avg_fill_price=px, message="", strategy="VWAP", broker_order_id="X",
            ))
        db.commit()

    def _override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        res = client.get("/api/reporting/cost-summary?date=2026-06-02")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]["closed_trades"] == 1
        assert body["summary"]["gross_pnl_krw"] == 10_000
        assert body["summary"]["net_pnl_krw"] == 10_000 - body["summary"]["total_cost_krw"]
        assert body["is_order_signal"] is False
    finally:
        app.dependency_overrides.clear()
