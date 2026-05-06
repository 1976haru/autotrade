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


# 131: contract metadata — entry/exit/invalidation/required_regime/risk_profile
# 가 describe 응답에 항상 포함되며, 미작성 strategy는 base.py의 default를 그대로
# surface해 운영자에게 "미완성" 신호로 인지.
def test_describe_strategy_includes_contract_metadata():
    d = describe_strategy("sma_crossover")
    # 실제 작성된 SmaCrossoverStrategy는 모든 필드를 채움.
    assert d["entry"]
    assert d["exit"]
    assert d["invalidation"]
    assert d["required_regime"] == "trending"
    assert d["risk_profile"]["position_size_pct"] == 5
    assert d["risk_profile"]["stop_loss_pct"]     == 2
    assert d["risk_profile"]["max_concurrent"]    == 1


def test_concrete_strategies_have_complete_metadata():
    """142 이후로 orb_vwap / rsi_reversion은 실제 신호 로직을 가진다 — 다만
    contract metadata는 모두 명시되어 있어야 한다 (base.py default를 그대로
    surface하지 않음)."""
    from app.backtest.types import Bar, Signal
    from app.strategies.concrete import build_strategy

    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    for name in ("orb_vwap", "rsi_reversion"):
        d = describe_strategy(name)
        assert d["entry"], f"{name} must have entry metadata"
        assert d["exit"],  f"{name} must have exit metadata"
        assert d["invalidation"], f"{name} must have invalidation metadata"
        assert d["required_regime"] != "any", f"{name} must declare a regime hint"
        assert d["risk_profile"], f"{name} must declare a risk_profile"

        strat = build_strategy(name, None)
        # 평탄한 데이터는 어떤 trigger도 만들지 않아야 한다 — 양 전략 모두
        # cross-back 또는 cross-up 이벤트로 발화하므로 close가 일정하면 HOLD.
        bars = [Bar(symbol="X",
                    timestamp=base + timedelta(minutes=i),
                    open=100, high=101, low=99, close=100, volume=10)
                for i in range(50)]
        # Strategy is stateful — feed bars one-by-one and verify never BUY/SELL.
        for i in range(1, len(bars) + 1):
            sig = strat.on_bar(bars[:i])
            assert sig == Signal.HOLD, (
                f"{name} must not emit signals on flat data, got {sig} at bar {i}"
            )


def test_unannotated_strategy_falls_back_to_base_metadata_defaults():
    """Strategy 클래스가 metadata를 명시 안 하면 base.py의 default(""/any/{}) 가
    그대로 응답에 노출 — 'unfilled contract' 신호."""
    from app.backtest.types import Bar, Signal
    from app.strategies.base import Strategy

    class _Bare(Strategy):
        def __init__(self):
            pass

        def on_bar(self, bars: list[Bar]) -> Signal:
            return Signal.HOLD

    STRATEGY_REGISTRY["_bare"] = _Bare
    try:
        d = describe_strategy("_bare")
        assert d["entry"] == ""
        assert d["exit"] == ""
        assert d["invalidation"] == ""
        assert d["required_regime"] == "any"
        assert d["risk_profile"] == {}
    finally:
        STRATEGY_REGISTRY.pop("_bare")
