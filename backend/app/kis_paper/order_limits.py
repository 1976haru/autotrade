"""Single source of truth for KIS PAPER automatic-entry limits."""

from __future__ import annotations

from dataclasses import dataclass

PAPER_ORDER_LIMITS_POLICY_VERSION = "KIS_PAPER_V2.2"
PAPER_TOTAL_BUDGET_KRW = 10_000_000
PAPER_MAX_ORDER_KRW = 2_000_000
PAPER_MAX_POSITIONS = 5
PAPER_MAX_BUYS_PER_DAY = 10
PAPER_MAX_DAILY_BUY_KRW = 10_000_000
PAPER_MAX_BUYS_PER_TICK = 1
PAPER_COST_RESERVE_BPS = 100


def safe_market_buy_quantity(
    *, target_budget_krw: int, quote_price_krw: int,
    calculation_price_krw: int, no_credit_buy_quantity: int,
    no_credit_buy_amount_krw: int, orderable_cash_krw: int,
    cost_reserve_bps: int = PAPER_COST_RESERVE_BPS,
    remaining_total_budget_krw: int | None = None,
) -> int:
    """Conservatively size a market BUY using KIS's cash-only capacity fields.

    The KIS calculation price is not required to equal the quote. Use the
    higher unit price for budget reservation, and cap by KIS's no-credit qty.
    Missing/invalid values fail closed as zero quantity.
    """
    values = (
        target_budget_krw, quote_price_krw, calculation_price_krw,
        no_credit_buy_quantity, no_credit_buy_amount_krw, orderable_cash_krw,
        cost_reserve_bps,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return 0
    if min(quote_price_krw, calculation_price_krw) <= 0:
        return 0
    if min(target_budget_krw, no_credit_buy_quantity, no_credit_buy_amount_krw,
           orderable_cash_krw, cost_reserve_bps) < 0:
        return 0
    budget = min(target_budget_krw, no_credit_buy_amount_krw, orderable_cash_krw)
    if remaining_total_budget_krw is not None:
        if isinstance(remaining_total_budget_krw, bool) or not isinstance(remaining_total_budget_krw, int):
            return 0
        if remaining_total_budget_krw < 0:
            return 0
        budget = min(budget, remaining_total_budget_krw)
    conservative_unit_price = max(quote_price_krw, calculation_price_krw)
    denominator = conservative_unit_price * (10_000 + cost_reserve_bps)
    budget_quantity = (budget * 10_000) // denominator
    return min(no_credit_buy_quantity, budget_quantity)


@dataclass(frozen=True)
class KisPaperOrderLimits:
    policy_version: str
    total_budget_krw: int
    per_symbol_krw: int
    max_order_krw: int
    max_positions: int
    daily_buy_count: int
    daily_buy_krw: int
    buys_per_tick: int
    cost_reserve_bps: int


def resolve_kis_paper_order_limits(settings) -> KisPaperOrderLimits:
    """Resolve overrides while retaining immutable PAPER hard ceilings.

    Generic RISK_* settings are deliberately not read or changed here; those
    continue to govern the non-PAPER risk path.
    """
    from app.core.runtime_config import (
        effective_daily_buy_limit,
        effective_max_concurrent_positions,
        effective_per_stock_budget,
    )

    configured_order_cap = int(
        getattr(settings, "kis_paper_auto_max_order_notional", PAPER_MAX_ORDER_KRW)
        or PAPER_MAX_ORDER_KRW
    )
    per_symbol = min(
        max(0, int(effective_per_stock_budget())),
        PAPER_MAX_ORDER_KRW,
        max(0, configured_order_cap),
    )
    positions = min(
        max(1, int(effective_max_concurrent_positions())), PAPER_MAX_POSITIONS,
    )
    daily = min(
        max(0, int(effective_daily_buy_limit())), PAPER_MAX_DAILY_BUY_KRW,
    )
    count = min(
        max(0, int(getattr(settings, "kis_paper_auto_max_orders_per_day", 10) or 0)),
        PAPER_MAX_BUYS_PER_DAY,
    )
    buys_per_tick = min(
        max(1, int(getattr(settings, "kis_paper_max_new_positions_per_tick", 1) or 1)),
        PAPER_MAX_BUYS_PER_TICK,
    )
    return KisPaperOrderLimits(
        policy_version=PAPER_ORDER_LIMITS_POLICY_VERSION,
        total_budget_krw=PAPER_TOTAL_BUDGET_KRW,
        per_symbol_krw=per_symbol,
        max_order_krw=min(PAPER_MAX_ORDER_KRW, max(0, configured_order_cap), per_symbol),
        max_positions=positions,
        daily_buy_count=count,
        daily_buy_krw=daily,
        buys_per_tick=buys_per_tick,
        cost_reserve_bps=PAPER_COST_RESERVE_BPS,
    )
