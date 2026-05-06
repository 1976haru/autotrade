import inspect
from typing import Any

from app.strategies.base import Strategy
from app.strategies.concrete.orb_vwap import OrbVwapStrategy
from app.strategies.concrete.rsi_reversion import RsiReversionStrategy
from app.strategies.concrete.sma_crossover import SmaCrossoverStrategy


# 131: 등록되는 전략은 contract metadata(entry/exit/invalidation/required_regime/
# risk_profile)를 base.py Strategy 클래스 attrs로 가진다. 142에서 orb_vwap/
# rsi_reversion의 실제 신호 로직이 구현됐다 (이전엔 HOLD-only stub).
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "sma_crossover": SmaCrossoverStrategy,
    "orb_vwap":      OrbVwapStrategy,
    "rsi_reversion": RsiReversionStrategy,
}


class StrategyContractError(ValueError):
    """170: Strategy class가 base.py default contract metadata를 그대로
    노출 — entry/exit/invalidation 빈 문자열 또는 required_regime='any'.
    운영자가 미완성 strategy를 LIVE에 배포하는 것을 사전 차단한다."""


def validate_strategy_contract(cls: type[Strategy]) -> list[str]:
    """class-level contract metadata가 모두 채워졌는지 검사. 빈 list면 통과,
    아니면 violation reason 리스트.

    검사 항목 (모두 base.py default를 거부):
    - entry: 비어 있으면 거부 (string truthiness)
    - exit:  동일
    - invalidation: 동일
    - required_regime: "any" 또는 빈 값 거부 (구체값 강제)
    - risk_profile: 빈 dict 거부 (최소 position_size_pct 권장이지만 필드 존재만 확인)
    """
    violations: list[str] = []
    if not (cls.entry and str(cls.entry).strip()):
        violations.append("entry is empty (base.py default)")
    if not (cls.exit and str(cls.exit).strip()):
        violations.append("exit is empty (base.py default)")
    if not (cls.invalidation and str(cls.invalidation).strip()):
        violations.append("invalidation is empty (base.py default)")
    regime = (cls.required_regime or "").strip()
    if not regime or regime == "any":
        violations.append(f"required_regime is '{regime or '<empty>'}' (base.py default — must be specific)")
    if not cls.risk_profile:
        violations.append("risk_profile is empty (base.py default)")
    return violations


def build_strategy(
    name: str,
    params: dict | None,
    *,
    enforce_contract: bool = True,
) -> Strategy:
    """170: enforce_contract=True (기본)이면 contract metadata 미완성 strategy
    등록 거부. 백테스트 / 검증 흐름에서 의도적으로 우회가 필요하면 False 명시."""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown strategy: {name}")
    if enforce_contract:
        violations = validate_strategy_contract(cls)
        if violations:
            raise StrategyContractError(
                f"strategy '{name}' contract incomplete: {'; '.join(violations)}"
            )
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
    "StrategyContractError",
    "build_strategy",
    "describe_strategy",
    "describe_all_strategies",
    "validate_strategy_contract",
    "OrbVwapStrategy",
    "RsiReversionStrategy",
    "SmaCrossoverStrategy",
]
