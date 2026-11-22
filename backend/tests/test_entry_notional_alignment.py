"""Regression: entry sizing and hard KIS PAPER order gate stay aligned."""

from app.kis_paper.driver_bridge import _bounded_entry_notional, _entry_quantity


def test_runtime_budget_is_capped_to_hard_order_limit():
    assert _bounded_entry_notional(1_600_000, 1_000_000) == 1_000_000


def test_lower_runtime_budget_is_preserved():
    assert _bounded_entry_notional(800_000, 1_000_000) == 800_000


def test_zero_hard_limit_means_unbounded_by_order_gate():
    assert _bounded_entry_notional(1_600_000, 0) == 1_600_000


def test_entry_quantity_floors_to_whole_shares_within_limit():
    assert _entry_quantity(310_000, 1_000_000) == 3
    assert 310_000 * _entry_quantity(310_000, 1_000_000) <= 1_000_000


def test_entry_quantity_blocks_when_one_share_exceeds_limit():
    assert _entry_quantity(1_200_000, 1_000_000) == 0
