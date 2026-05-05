"""Unit tests for the strategy registry introspection layer.

Covers describe_strategy / describe_all_strategies in isolation from HTTP.
The route-layer test (test_live_engine_routes.py) covers serialization through
the FastAPI response model.
"""

import pytest

from app.strategies.concrete import (
    STRATEGY_REGISTRY,
    describe_all_strategies,
    describe_strategy,
)


def test_describe_all_returns_one_entry_per_registered_strategy():
    described = describe_all_strategies()
    assert len(described) == len(STRATEGY_REGISTRY)
    assert {d["name"] for d in described} == set(STRATEGY_REGISTRY)


def test_describe_strategy_extracts_param_defaults_and_types():
    d = describe_strategy("sma_crossover")
    assert d["name"]       == "sma_crossover"
    assert d["class_name"] == "SmaCrossoverStrategy"
    by_param = {p["name"]: p for p in d["params"]}
    assert by_param["short"] == {"name": "short", "type": "int", "default": 5,  "required": False}
    assert by_param["long"]  == {"name": "long",  "type": "int", "default": 20, "required": False}


def test_describe_strategy_skips_self_param():
    d = describe_strategy("sma_crossover")
    assert all(p["name"] != "self" for p in d["params"])


def test_describe_strategy_includes_docstring():
    d = describe_strategy("sma_crossover")
    assert d["description"]
    assert "\n" not in d["description"][:5]  # leading whitespace stripped


def test_describe_strategy_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        describe_strategy("does_not_exist")


def test_required_flag_when_no_default():
    """Synthetic strategy with a required positional arg shows required=True."""
    from app.backtest.types import Bar, Signal
    from app.strategies.base import Strategy

    class _NeedsArg(Strategy):
        def __init__(self, threshold: int):
            self.threshold = threshold

        def on_bar(self, bars: list[Bar]) -> Signal:
            return Signal.HOLD

    STRATEGY_REGISTRY["_synthetic"] = _NeedsArg
    try:
        d = describe_strategy("_synthetic")
        assert d["params"] == [
            {"name": "threshold", "type": "int", "default": None, "required": True},
        ]
    finally:
        STRATEGY_REGISTRY.pop("_synthetic")
