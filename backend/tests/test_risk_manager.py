from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def _balance(cash: int = 10_000_000) -> Balance:
    return Balance(cash=cash, equity=cash, buying_power=cash)


def _buy(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty)


def test_simulation_small_order_is_approved():
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.APPROVED


def test_rejects_order_over_notional_limit():
    risk = RiskManager(RiskPolicy(max_order_notional=100_000))
    result = risk.evaluate_order(
        order=_buy(10),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "order notional exceeds max_order_notional" in result.reasons


def test_emergency_stop_rejects_otherwise_valid_order():
    risk = RiskManager(RiskPolicy())
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "emergency stop is enabled" in result.reasons


def test_daily_loss_limit_rejects_new_orders():
    risk = RiskManager(RiskPolicy(max_daily_loss=200_000))
    risk.daily_realized_pnl = -250_000
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "daily loss limit reached" in result.reasons


def test_insufficient_cash_is_rejected():
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(cash=10_000),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "insufficient cash" in result.reasons


def test_max_positions_blocks_new_symbol():
    risk = RiskManager(RiskPolicy(max_positions=2))
    held = [
        Position(symbol="000660", quantity=1, avg_price=185_000, market_price=185_000),
        Position(symbol="035420", quantity=1, avg_price=205_000, market_price=205_000),
    ]
    result = risk.evaluate_order(
        order=_buy(1, symbol="005930"),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=held,
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "max positions reached" in result.reasons


def test_symbol_exposure_limit():
    risk = RiskManager(RiskPolicy(max_symbol_exposure=200_000, max_order_notional=10_000_000))
    held = [Position(symbol="005930", quantity=2, avg_price=75_000, market_price=75_000)]
    result = risk.evaluate_order(
        order=_buy(2, symbol="005930"),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=held,
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "symbol exposure limit exceeded" in result.reasons


def test_shadow_mode_rejects_live_order_attempt():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_SHADOW,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("LIVE_SHADOW" in reason for reason in result.reasons)


def test_manual_mode_requires_approval():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL


def test_ai_assist_mode_requires_approval():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_ASSIST,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL


def test_live_ai_execution_requires_global_flag():
    risk = RiskManager(RiskPolicy(enable_live_trading=False, enable_ai_execution=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("live trading" in reason for reason in result.reasons)


def test_live_ai_execution_blocked_when_ai_flag_off():
    risk = RiskManager(RiskPolicy(enable_live_trading=True, enable_ai_execution=False))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("AI execution" in reason for reason in result.reasons)


def test_live_ai_execution_approved_when_both_flags_on():
    risk = RiskManager(RiskPolicy(enable_live_trading=True, enable_ai_execution=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED
    assert result.allowed is True
