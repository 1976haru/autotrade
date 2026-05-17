"""3-03 — parameter grid catalog 테스트.

invariant:
- 6 전략 모두 grid 정의됨.
- 각 grid entry 의 키가 strategy `__init__` 시그니처에 실재.
- grid 총 조합 수 < 50 (overfit / 자원 폭주 차단).
"""

from __future__ import annotations


from app.backtest.real_data.grid_search import (
    PARAMETER_GRIDS,
    iter_param_grid,
    total_combinations,
    validate_grid_keys,
)
from app.strategies.concrete import build_strategy


class TestGridCatalog:
    def test_six_strategies_have_grids(self):
        expected = {
            "sma_crossover", "rsi_reversion", "vwap_strategy",
            "orb_vwap", "volume_breakout", "pullback_rebreak",
        }
        assert set(PARAMETER_GRIDS.keys()) == expected

    def test_grid_size_within_budget(self):
        # 총 조합 수 50 미만으로 lock — search space 폭주 차단.
        assert total_combinations() < 50

    def test_each_grid_has_at_least_one_entry(self):
        for name, grid in PARAMETER_GRIDS.items():
            assert len(grid) >= 1, f"strategy {name} has empty grid"

    def test_each_grid_entry_keys_match_strategy_init(self):
        issues = validate_grid_keys()
        assert issues == {}, f"grid keys not matching __init__ params: {issues}"

    def test_grid_entries_can_build_strategy(self):
        """모든 grid entry 로 build_strategy 가 성공해야 한다."""
        for name, grid in PARAMETER_GRIDS.items():
            for params in grid:
                # build_strategy 가 raise 하지 않아야 함.
                strategy = build_strategy(name, params=dict(params))
                assert strategy is not None


class TestIterParamGrid:
    def test_iter_returns_grid_entries(self):
        grid = list(iter_param_grid("sma_crossover"))
        assert len(grid) >= 1
        for entry in grid:
            assert isinstance(entry, dict)
            assert "short" in entry and "long" in entry

    def test_iter_unknown_strategy_returns_empty(self):
        grid = list(iter_param_grid("nonexistent_strategy"))
        assert grid == []


class TestSpecificGrids:
    def test_sma_crossover_grid_satisfies_constraint(self):
        # SmaCrossoverStrategy: short < long 강제.
        for params in PARAMETER_GRIDS["sma_crossover"]:
            assert params["short"] < params["long"]

    def test_rsi_reversion_grid_satisfies_constraint(self):
        # 0 < oversold < overbought < 100.
        for params in PARAMETER_GRIDS["rsi_reversion"]:
            assert 0 < params["oversold"] < params["overbought"] < 100

    def test_orb_vwap_grid_has_positive_orb_bars(self):
        for params in PARAMETER_GRIDS["orb_vwap"]:
            assert params["orb_bars"] >= 1
