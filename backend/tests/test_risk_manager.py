from app.brokers.base import Balance, OrderRequest, OrderSide
from app.core.modes import OperationMode
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def test_rejects_order_over_notional_limit():
    risk = RiskManager(RiskPolicy(max_order_notional=100_000))
    result = risk.evaluate_order(
        order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=10),
        mode=OperationMode.SIMULATION,
        balance=Balance(cash=10_000_000, equity=10_000_000, buying_power=10_000_000),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "order notional exceeds max_order_notional" in result.reasons


def test_shadow_mode_rejects_live_order_attempt():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
        mode=OperationMode.LIVE_SHADOW,
        balance=Balance(cash=10_000_000, equity=10_000_000, buying_power=10_000_000),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("LIVE_SHADOW" in reason for reason in result.reasons)


def test_manual_mode_requires_approval():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=Balance(cash=10_000_000, equity=10_000_000, buying_power=10_000_000),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL
