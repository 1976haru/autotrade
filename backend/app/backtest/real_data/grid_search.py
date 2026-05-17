"""3-03 — 6 전략 제한된 parameter grid catalog.

본 모듈은 3-02 백테스트 러너 위에 *제한된* parameter grid 를 정의한다.
각 전략에 대해 가장 영향력 있는 1~2개 파라미터에 대해 4~6개 조합으로
*폭주 차단* 한다 — search space 가 커지면 운영 자원 / 인지 부하 / overfit
위험이 함께 커지므로 의도적으로 보수적.

설계 원칙:
- 전략당 max 6 조합 (combinatorial 폭주 차단).
- 각 strategy 의 `__init__` 시그니처와 일치하는 키만 사용 — 잘못된 키는
  `build_strategy(name, params)` 시점에 즉시 ValueError.
- 임계값 / 룩백 / 손익절 같은 *경험적으로 영향 큰 파라미터* 만 다양화.
  기본값에서 *너무 멀어지는* 값은 제외 (overfit 회피).

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- 본 catalog 는 *정의* 일 뿐 — 자동 적용 / 주문 트리거 0건.
- 본 grid 가 *유일한 정답* 아님 — 후속 PR 에서 운영자 검토 후 확장 / 축소.
"""

from __future__ import annotations

from typing import Iterable

from app.strategies.concrete import STRATEGY_REGISTRY


# 6개 전략의 제한된 grid — 각 entry 는 `build_strategy(name, params)` 의
# ``params`` 로 직접 전달 가능한 dict.
#
# 총 combination 수 (의도적 보수):
#   sma_crossover    : 4
#   rsi_reversion    : 4
#   vwap_strategy    : 6
#   orb_vwap         : 3
#   volume_breakout  : 6
#   pullback_rebreak : 6
# ─────────────────────────────────────────
# 총합            : 29
PARAMETER_GRIDS: dict[str, list[dict[str, object]]] = {
    "sma_crossover": [
        {"short":  5, "long": 20},
        {"short":  5, "long": 30},
        {"short": 10, "long": 30},
        {"short": 10, "long": 40},
    ],
    "rsi_reversion": [
        {"period": 10, "oversold": 25, "overbought": 70},
        {"period": 10, "oversold": 30, "overbought": 70},
        {"period": 14, "oversold": 25, "overbought": 70},
        {"period": 14, "oversold": 30, "overbought": 70},
    ],
    "vwap_strategy": [
        {"stop_loss_pct": 1.0, "take_profit_pct": 2.0},
        {"stop_loss_pct": 1.0, "take_profit_pct": 3.0},
        {"stop_loss_pct": 1.5, "take_profit_pct": 2.5},
        {"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
        {"stop_loss_pct": 2.0, "take_profit_pct": 3.0},
        {"stop_loss_pct": 2.0, "take_profit_pct": 4.0},
    ],
    "orb_vwap": [
        {"orb_bars": 3},
        {"orb_bars": 6},
        {"orb_bars": 9},
    ],
    "volume_breakout": [
        {"volume_multiplier": 1.5, "stop_loss_pct": 1.5},
        {"volume_multiplier": 1.5, "stop_loss_pct": 2.0},
        {"volume_multiplier": 2.0, "stop_loss_pct": 1.5},
        {"volume_multiplier": 2.0, "stop_loss_pct": 2.0},
        {"volume_multiplier": 2.5, "stop_loss_pct": 2.0},
        {"volume_multiplier": 2.5, "stop_loss_pct": 2.5},
    ],
    "pullback_rebreak": [
        {"min_impulse_pct": 1.0, "pullback_max_pct": 3.0},
        {"min_impulse_pct": 1.0, "pullback_max_pct": 4.0},
        {"min_impulse_pct": 1.5, "pullback_max_pct": 3.0},
        {"min_impulse_pct": 1.5, "pullback_max_pct": 4.0},
        {"min_impulse_pct": 2.0, "pullback_max_pct": 3.0},
        {"min_impulse_pct": 2.0, "pullback_max_pct": 4.0},
    ],
}


def iter_param_grid(strategy_name: str) -> Iterable[dict[str, object]]:
    """전략의 grid 를 iterate. 등록되지 않은 전략은 빈 iter."""
    return iter(PARAMETER_GRIDS.get(strategy_name, []))


def total_combinations() -> int:
    """모든 전략의 grid 조합 수 합."""
    return sum(len(g) for g in PARAMETER_GRIDS.values())


def validate_grid_keys() -> dict[str, list[str]]:
    """각 grid entry 의 키가 strategy `__init__` 시그니처에 존재하는지 검증.

    Returns:
        dict — `{strategy_name: [unknown_keys]}` — 빈 list 면 모두 OK.
    """
    import inspect

    issues: dict[str, list[str]] = {}
    for name, grid in PARAMETER_GRIDS.items():
        cls = STRATEGY_REGISTRY.get(name)
        if cls is None:
            issues[name] = ["strategy_not_registered"]
            continue
        sig_params = set(inspect.signature(cls.__init__).parameters.keys())
        # self 는 제외.
        sig_params.discard("self")
        bad: list[str] = []
        for entry in grid:
            for k in entry.keys():
                if k not in sig_params and k not in bad:
                    bad.append(k)
        if bad:
            issues[name] = bad
    return issues
