from app.backtest.strategies.sma_crossover import SmaCrossoverStrategy
from app.backtest.strategy import Strategy


STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "sma_crossover": SmaCrossoverStrategy,
}


def build_strategy(name: str, params: dict | None) -> Strategy:
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown strategy: {name}")
    try:
        return cls(**(params or {}))
    except (TypeError, ValueError) as e:
        raise ValueError(f"invalid params for strategy '{name}': {e}") from e


__all__ = ["STRATEGY_REGISTRY", "build_strategy", "SmaCrossoverStrategy"]
