from types import SimpleNamespace

from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.kis_paper.order_limits import (
    PAPER_ORDER_LIMITS_POLICY_VERSION,
    resolve_kis_paper_order_limits,
    safe_market_buy_quantity,
)
from app.risk.position_limits import (
    PositionLimitInput, PositionLimitPolicy, PositionLimitRule,
)


def _settings(**overrides):
    values = dict(
        kis_paper_auto_max_order_notional=2_000_000,
        kis_paper_auto_max_orders_per_day=10,
        kis_paper_max_new_positions_per_tick=1,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _runtime(monkeypatch, *, per_symbol=1_000_000, positions=5, daily=10_000_000):
    from app.core import runtime_config

    monkeypatch.setattr(runtime_config, "effective_per_stock_budget", lambda: per_symbol)
    monkeypatch.setattr(runtime_config, "effective_max_concurrent_positions", lambda: positions)
    monkeypatch.setattr(runtime_config, "effective_daily_buy_limit", lambda: daily)


def test_default_1m_and_selected_2m_are_single_source_of_truth(monkeypatch):
    _runtime(monkeypatch)
    default = resolve_kis_paper_order_limits(_settings())
    assert default.policy_version == PAPER_ORDER_LIMITS_POLICY_VERSION
    assert default.per_symbol_krw == default.max_order_krw == 1_000_000

    _runtime(monkeypatch, per_symbol=2_000_000)
    expanded = resolve_kis_paper_order_limits(_settings())
    assert expanded.per_symbol_krw == expanded.max_order_krw == 2_000_000
    assert expanded.total_budget_krw == 10_000_000


def test_paper_limits_clamp_oversized_overrides_and_generic_hard_caps(monkeypatch):
    _runtime(monkeypatch, per_symbol=10_000_000, positions=15, daily=300_000_000)
    limits = resolve_kis_paper_order_limits(_settings(kis_paper_auto_max_order_notional=10_000_000))
    assert limits.per_symbol_krw == limits.max_order_krw == 2_000_000
    assert limits.max_positions == 5
    assert limits.daily_buy_krw == 10_000_000
    assert limits.daily_buy_count == 10 and limits.buys_per_tick == 1


def test_user_lower_cap_wins(monkeypatch):
    _runtime(monkeypatch, per_symbol=2_000_000)
    limits = resolve_kis_paper_order_limits(_settings(kis_paper_auto_max_order_notional=1_000_000))
    assert limits.per_symbol_krw == limits.max_order_krw == 1_000_000


def test_market_buy_quantity_uses_conservative_kis_calculation_price_and_reserve():
    shared = dict(
        quote_price_krw=203_000,
        calculation_price_krw=283_000,
        no_credit_buy_quantity=100,
        no_credit_buy_amount_krw=499_929_000,
        orderable_cash_krw=497_500_000,
        cost_reserve_bps=100,
    )
    assert safe_market_buy_quantity(target_budget_krw=1_000_000, **shared) == 3
    assert safe_market_buy_quantity(target_budget_krw=2_000_000, **shared) == 6
    assert 4 * 283_000 * 1.01 > 1_000_000
    assert 7 * 283_000 * 1.01 > 2_000_000


def test_market_buy_quantity_handles_calc_price_above_below_and_kis_caps():
    base = dict(
        target_budget_krw=1_000_000, quote_price_krw=203_000,
        no_credit_buy_quantity=100, no_credit_buy_amount_krw=1_000_000,
        orderable_cash_krw=1_000_000, cost_reserve_bps=100,
    )
    assert safe_market_buy_quantity(calculation_price_krw=203_000, **base) == 4
    assert safe_market_buy_quantity(calculation_price_krw=283_000, **base) == 3
    assert safe_market_buy_quantity(calculation_price_krw=180_000, **base) == 4
    assert safe_market_buy_quantity(
        calculation_price_krw=203_000, **{**base, "no_credit_buy_quantity": 2},
    ) == 2
    assert safe_market_buy_quantity(
        calculation_price_krw=203_000, **{**base, "remaining_total_budget_krw": 300_000},
    ) == 1
    assert safe_market_buy_quantity(calculation_price_krw=0, **base) == 0


def test_five_positions_allowed_and_sixth_new_symbol_blocked():
    rule = PositionLimitRule(PositionLimitPolicy(
        max_order_notional=2_000_000,
        max_positions=5,
        max_symbol_exposure=2_000_000,
        max_total_exposure=10_000_000,
    ))
    holdings = [Position(symbol=f"00000{i}", quantity=1,
                         avg_price=1_000_000, market_price=1_000_000)
                for i in range(1, 6)]
    candidate = PositionLimitInput(
        order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
        balance=Balance(cash=10_000_000, equity=10_000_000, buying_power=10_000_000),
        positions=holdings,
        latest_price=1_000_000,
    )
    result = rule.check(candidate)
    assert "max positions reached" in result.reasons

    same_symbol = PositionLimitInput(
        order=OrderRequest(symbol="000001", side=OrderSide.BUY, quantity=1),
        balance=candidate.balance,
        positions=holdings,
        latest_price=1_000_000,
    )
    assert "max positions reached" not in rule.check(same_symbol).reasons
