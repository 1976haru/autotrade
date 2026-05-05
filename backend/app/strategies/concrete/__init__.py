import inspect
from typing import Any

from app.strategies.base import Strategy
from app.strategies.concrete.sma_crossover import SmaCrossoverStrategy


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


# Introspection — used by GET /api/strategies/registry so the frontend can
# render config forms without hardcoding param names. Keep the schema flat
# (name/type/default/required) — no full JSON-Schema, no nested types yet.

_PRIMITIVE_TYPE_NAMES: dict[type, str] = {
    int:   "int",
    float: "float",
    str:   "string",
    bool:  "bool",
}


def _param_type_name(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "any"
    return _PRIMITIVE_TYPE_NAMES.get(annotation, str(annotation))


def describe_strategy(name: str) -> dict:
    cls = STRATEGY_REGISTRY[name]
    sig = inspect.signature(cls.__init__)
    params: list[dict] = []
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        has_default = param.default is not inspect.Parameter.empty
        params.append({
            "name":     param_name,
            "type":     _param_type_name(param.annotation),
            "default":  param.default if has_default else None,
            "required": not has_default,
        })
    return {
        "name":        name,
        "class_name":  cls.__name__,
        "description": (cls.__doc__ or "").strip(),
        "params":      params,
    }


def describe_all_strategies() -> list[dict]:
    return [describe_strategy(name) for name in STRATEGY_REGISTRY]


__all__ = [
    "STRATEGY_REGISTRY",
    "build_strategy",
    "describe_strategy",
    "describe_all_strategies",
    "SmaCrossoverStrategy",
]
