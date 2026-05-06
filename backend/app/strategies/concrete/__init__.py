import inspect
from typing import Any

from app.strategies.base import Strategy
from app.strategies.concrete.orb_vwap import OrbVwapStrategy
from app.strategies.concrete.rsi_reversion import RsiReversionStrategy
from app.strategies.concrete.sma_crossover import SmaCrossoverStrategy


# 131: 등록되는 전략은 contract metadata(entry/exit/invalidation/required_regime/
# risk_profile)를 base.py Strategy 클래스 attrs로 가진다. orb_vwap/rsi_reversion
# 은 미완성 stub — TODO 표시 + on_bar는 항상 HOLD라 실거래에는 영향 없음.
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "sma_crossover": SmaCrossoverStrategy,
    "orb_vwap":      OrbVwapStrategy,
    "rsi_reversion": RsiReversionStrategy,
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
        # 131: contract metadata. 클래스가 명시 안 하면 base.py의 default(""/
        # "any"/{})가 그대로 노출돼 운영자가 미완성 신호를 인지.
        "entry":           cls.entry,
        "exit":            cls.exit,
        "invalidation":    cls.invalidation,
        "required_regime": cls.required_regime,
        "risk_profile":    dict(cls.risk_profile),
    }


def describe_all_strategies() -> list[dict]:
    return [describe_strategy(name) for name in STRATEGY_REGISTRY]


__all__ = [
    "STRATEGY_REGISTRY",
    "build_strategy",
    "describe_strategy",
    "describe_all_strategies",
    "OrbVwapStrategy",
    "RsiReversionStrategy",
    "SmaCrossoverStrategy",
]
