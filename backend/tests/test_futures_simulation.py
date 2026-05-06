"""Futures simulation engine + MockFuturesBroker tests (151, MUST)."""

import asyncio

import pytest

from app.futures.mock import MockFuturesBroker
from app.futures.risk import (
    FuturesRiskDecision,
    FuturesRiskManager,
    FuturesRiskPolicy,
)
from app.futures.simulation import (
    FuturesSimulationParams,
    apply_slippage,
    compute_fee,
    compute_initial_margin,
    compute_liquidation_price,
    realized_pnl_on_close,
    should_force_liquidate,
)
from app.futures.types import (
    FuturesOrderRequest,
    FuturesOrderStatus,
    FuturesPosition,
    FuturesPositionSide,
    FuturesSide,
)


# ---------- pure simulation math ----------

def test_compute_initial_margin_basic():
    assert compute_initial_margin(notional=1_000_000, leverage=5.0) == 200_000


def test_compute_initial_margin_round_up_for_safety():
    """1/3 같은 비정수 결과는 ceil — 0.5원 부족도 미허용."""
    assert compute_initial_margin(notional=1_000_001, leverage=3.0) == 333_334


def test_compute_initial_margin_invalid_inputs():
    with pytest.raises(ValueError):
        compute_initial_margin(notional=1_000, leverage=0)
    with pytest.raises(ValueError):
        compute_initial_margin(notional=0, leverage=5)


def test_compute_liquidation_price_long():
    """leverage=5, mm=10 → loss buffer = 0.20 - 0.10 = 0.10. 1000 * 0.9 = 900."""
    p = compute_liquidation_price(
        side=FuturesPositionSide.LONG, entry_price=1000, leverage=5.0,
        maintenance_margin_pct=10.0,
    )
    assert p == 900


def test_compute_liquidation_price_short():
    p = compute_liquidation_price(
        side=FuturesPositionSide.SHORT, entry_price=1000, leverage=5.0,
        maintenance_margin_pct=10.0,
    )
    assert p == 1100


def test_should_force_liquidate_long():
    pos = FuturesPosition(
        contract="X", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=1000, market_price=900, margin_used=200,
        liquidation_price=900,
    )
    assert should_force_liquidate(pos, 900) is True
    assert should_force_liquidate(pos, 901) is False


def test_should_force_liquidate_short():
    pos = FuturesPosition(
        contract="X", side=FuturesPositionSide.SHORT, quantity=1,
        entry_price=1000, market_price=1100, margin_used=200,
        liquidation_price=1100,
    )
    assert should_force_liquidate(pos, 1100) is True
    assert should_force_liquidate(pos, 1099) is False


def test_should_force_liquidate_none_returns_false():
    pos = FuturesPosition(
        contract="X", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=1000, market_price=500, margin_used=200,
        liquidation_price=None,
    )
    assert should_force_liquidate(pos, 500) is False


def test_apply_slippage_long_pays_more_short_receives_less():
    assert apply_slippage(price=1000, side="LONG", slippage_bps=50) == 1005
    assert apply_slippage(price=1000, side="SHORT", slippage_bps=50) == 995


def test_apply_slippage_zero_or_negative_returns_unchanged():
    assert apply_slippage(price=1000, side="LONG", slippage_bps=0) == 1000


def test_compute_fee_basic():
    assert compute_fee(notional=1_000_000, fee_bps=2) == 200


def test_realized_pnl_long_short():
    pnl_long  = realized_pnl_on_close(side=FuturesPositionSide.LONG,
                                       quantity=2, entry_price=1000, exit_price=1100)
    pnl_short = realized_pnl_on_close(side=FuturesPositionSide.SHORT,
                                       quantity=2, entry_price=1000, exit_price=900)
    assert pnl_long  == 200
    assert pnl_short == 200


# ---------- MockFuturesBroker lifecycle ----------

def _broker(**kwargs):
    return MockFuturesBroker(initial_cash=20_000_000, **kwargs)


def _order(side="BUY", qty=1, contract="KOSPI200_2503", order_type="MARKET",
           limit_price=None):
    return FuturesOrderRequest(
        contract=contract, side=FuturesSide(side), quantity=qty,
        order_type=order_type, limit_price=limit_price,
    )


def test_open_long_position_market_fill():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    res = asyncio.run(broker.place_order(_order("BUY", 1)))
    assert res.status          == FuturesOrderStatus.FILLED
    assert res.message         == "virtual_open"
    assert res.filled_quantity == 1
    pos = broker.positions["KOSPI200_2503"]
    assert pos.side             == FuturesPositionSide.LONG
    assert pos.quantity         == 1
    # entry_price = 1000 + slippage (5bps default) = 1000.5 → 1001 (max(1, ...)).
    assert pos.entry_price      == 1001
    # margin = (1001 * 1) / 5 → ceil 201
    assert pos.margin_used      == 201
    # liquidation_price 산출됨.
    assert pos.liquidation_price is not None


def test_open_short_position():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    res = asyncio.run(broker.place_order(_order("SELL", 1)))
    assert res.status == FuturesOrderStatus.FILLED
    pos = broker.positions["KOSPI200_2503"]
    assert pos.side == FuturesPositionSide.SHORT
    # SHORT는 slippage 아래로 → 999.
    assert pos.entry_price == 999


def test_insufficient_cash_rejects():
    broker = MockFuturesBroker(initial_cash=100)
    broker.set_mark_price("KOSPI200_2503", 1000)
    res = asyncio.run(broker.place_order(_order("BUY", 1)))
    assert res.status == FuturesOrderStatus.REJECTED
    assert res.message == "insufficient_cash"


def test_close_position_realizes_pnl():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(_order("BUY", 1)))
    # 가격 상승 후 청산.
    broker.set_mark_price("KOSPI200_2503", 1100)
    res = asyncio.run(broker.place_order(_order("SELL", 1)))
    assert res.status         == FuturesOrderStatus.FILLED
    assert res.message        == "virtual_close"
    assert res.filled_quantity == 1
    assert "KOSPI200_2503" not in broker.positions
    assert broker.realized_pnl_today > 0


def test_partial_close_keeps_remainder():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(_order("BUY", 5)))
    res = asyncio.run(broker.place_order(_order("SELL", 2)))
    assert res.status         == FuturesOrderStatus.FILLED
    assert res.filled_quantity == 2
    pos = broker.positions["KOSPI200_2503"]
    assert pos.quantity == 3


def test_force_liquidate_long_when_mark_drops():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(_order("BUY", 1)))
    pos = broker.positions["KOSPI200_2503"]
    # liquidation_price 만큼 아래로 — 강제청산 트리거.
    broker.set_mark_price("KOSPI200_2503", pos.liquidation_price)
    result = broker.force_liquidate_if_needed("KOSPI200_2503")
    assert result is not None
    assert result.message == "virtual_force_liquidate"
    assert "KOSPI200_2503" not in broker.positions


def test_force_liquidate_no_op_when_mark_in_safe_zone():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(_order("BUY", 1)))
    # 살짝 떨어졌지만 liquidation 위. force_liquidate는 None.
    broker.set_mark_price("KOSPI200_2503", 990)
    assert broker.force_liquidate_if_needed("KOSPI200_2503") is None


def test_set_leverage_caps_at_max():
    broker = _broker()
    with pytest.raises(ValueError):
        broker.set_leverage(999)


def test_balance_reflects_margin_and_equity():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(_order("BUY", 1)))
    bal = asyncio.run(broker.get_balance())
    assert bal.margin_used > 0
    assert bal.margin_available == max(0, broker.cash - bal.margin_used)


def test_cancel_filled_order_returns_already_filled():
    broker = _broker()
    broker.set_mark_price("KOSPI200_2503", 1000)
    broker.set_leverage(5.0)
    res = asyncio.run(broker.place_order(_order("BUY", 1)))
    cancel = asyncio.run(broker.cancel_order(res.order_id))
    assert cancel.message == "already_filled"


def test_cancel_unknown_order_id_returns_rejected():
    broker = _broker()
    res = asyncio.run(broker.cancel_order("does_not_exist"))
    assert res.status == FuturesOrderStatus.REJECTED


# ---------- FuturesRiskManager: live still rejects, virtual evaluates ----------

def _live_order():
    return FuturesOrderRequest(
        contract="KOSPI200_2503",
        side=FuturesSide.BUY, quantity=1,
    )


def test_live_path_rejects_when_flag_disabled():
    risk = FuturesRiskManager(FuturesRiskPolicy(enable_futures_live_trading=False))
    res = risk.evaluate_order(
        order=_live_order(), positions=[], margin_used=0, margin_available=10_000_000
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("ENABLE_FUTURES_LIVE_TRADING" in r for r in res.reasons)


def test_live_path_still_rejects_when_flag_enabled_pre_pr():
    """151 PR은 live 평가 로직을 활성화하지 않는다 — flag만 켜도 REJECTED."""
    risk = FuturesRiskManager(FuturesRiskPolicy(enable_futures_live_trading=True))
    res = risk.evaluate_order(
        order=_live_order(), positions=[], margin_used=0, margin_available=10_000_000
    )
    assert res.decision == FuturesRiskDecision.REJECTED


def test_virtual_evaluate_approves_within_limits():
    risk = FuturesRiskManager(FuturesRiskPolicy(
        max_contracts=2, max_margin_used=1_000_000,
        max_leverage=10.0,
    ))
    res = risk.evaluate_virtual_order(
        order=_live_order(), positions=[],
        margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == FuturesRiskDecision.APPROVED


def test_virtual_evaluate_rejects_excess_leverage():
    risk = FuturesRiskManager(FuturesRiskPolicy(max_leverage=5.0))
    res = risk.evaluate_virtual_order(
        order=_live_order(), positions=[],
        margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=10.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("leverage" in r for r in res.reasons)


def test_virtual_evaluate_rejects_excess_contracts():
    risk = FuturesRiskManager(FuturesRiskPolicy(max_contracts=1))
    pos = FuturesPosition(
        contract="KOSPI200_2503", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=1_000_000, market_price=1_000_000, margin_used=200_000,
    )
    res = risk.evaluate_virtual_order(
        order=_live_order(), positions=[pos],
        margin_used=200_000, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED


def test_virtual_evaluate_rejects_insufficient_margin_available():
    risk = FuturesRiskManager(FuturesRiskPolicy())
    res = risk.evaluate_virtual_order(
        order=_live_order(), positions=[],
        margin_used=0, margin_available=100,  # 200,000 필요한데 100원만
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("margin_available" in r for r in res.reasons)


def test_virtual_evaluate_rejects_when_max_margin_used_exceeded():
    risk = FuturesRiskManager(FuturesRiskPolicy(max_margin_used=100_000))
    res = risk.evaluate_virtual_order(
        order=_live_order(), positions=[],
        margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("max_margin_used" in r for r in res.reasons)


def test_virtual_evaluate_rejects_when_daily_loss_breached():
    risk = FuturesRiskManager(
        FuturesRiskPolicy(max_daily_loss=10_000),
        daily_realized_pnl=-50_000,
    )
    res = risk.evaluate_virtual_order(
        order=_live_order(), positions=[],
        margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("daily futures loss" in r for r in res.reasons)
