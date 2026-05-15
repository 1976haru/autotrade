"""Strategy parameter grids — 그리드 서치 후보 공간.

코드의 6개 strategy_id 만 대응 (CLAUDE.md #87 invariant: 가짜 전략명 0건).
각 전략당 *1-2 축 + 3-4 값* 로 제한 — 과한 grid 는 CI 시간 / 과최적화 위험을
키운다.

본 모듈은 *advisory* — 운영자가 별도 PR 에서 새 param 후보를 추가할 때 본
파일만 수정. broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ParamGrid:
    """단일 전략의 param 그리드.

    `axes` 는 {param_name: [candidate_values]}. cartesian product 로 후보
    조합 생성. 모든 값은 strategy `__init__` 시그니처의 default 와 호환되어야
    한다 — base default 를 일부 override 하는 형태.
    """
    strategy_id: str
    axes:        dict[str, list[Any]] = field(default_factory=dict)
    base_defaults: dict[str, Any]      = field(default_factory=dict)

    def combinations(self) -> list[dict[str, Any]]:
        """cartesian product 로 모든 param 조합 생성."""
        keys = list(self.axes.keys())
        if not keys:
            return [dict(self.base_defaults)]
        out: list[dict[str, Any]] = []
        # 순회로 cartesian product (itertools 의존 회피 — pure stdlib).
        def _walk(idx: int, current: dict[str, Any]) -> None:
            if idx == len(keys):
                merged = dict(self.base_defaults)
                merged.update(current)
                out.append(merged)
                return
            k = keys[idx]
            for v in self.axes[k]:
                current[k] = v
                _walk(idx + 1, current)
        _walk(0, {})
        return out


# --------------------------------------------------------------------
# 코드의 6개 strategy_id 와 1:1 매핑.
# --------------------------------------------------------------------

_GRIDS: dict[str, ParamGrid] = {
    "sma_crossover": ParamGrid(
        strategy_id="sma_crossover",
        axes={
            "short": [3, 5, 10],
            "long":  [15, 20, 30],
        },
    ),
    "rsi_reversion": ParamGrid(
        strategy_id="rsi_reversion",
        axes={
            "period":   [7, 14, 21],
            "oversold": [25, 30, 35],
        },
    ),
    "vwap_strategy": ParamGrid(
        strategy_id="vwap_strategy",
        axes={
            "rolling_vwap_window": [15, 20, 25],
        },
    ),
    "orb_vwap": ParamGrid(
        strategy_id="orb_vwap",
        axes={
            "orb_bars": [4, 6, 8],
        },
    ),
    "volume_breakout": ParamGrid(
        strategy_id="volume_breakout",
        axes={
            "volume_multiplier":      [1.5, 2.0, 2.5],
            "breakout_lookback_bars": [10, 20],
        },
    ),
    "pullback_rebreak": ParamGrid(
        strategy_id="pullback_rebreak",
        axes={
            # pullback_rebreak 는 30+ 파라미터 — *가장 영향 큰 1축* 만 노출.
            # 다른 축은 default 유지.
            "min_bars_required": [25, 30, 40],
        },
    ),
}


def supported_strategy_ids() -> tuple[str, ...]:
    """grid 가 정의된 strategy_id 목록."""
    return tuple(_GRIDS.keys())


def get_param_grid(strategy_id: str) -> ParamGrid:
    """strategy_id 의 ParamGrid 반환. 미정의 시 ValueError."""
    if strategy_id not in _GRIDS:
        raise ValueError(
            f"unknown strategy_id: {strategy_id!r} "
            f"(supported: {', '.join(supported_strategy_ids())})"
        )
    return _GRIDS[strategy_id]


def all_combinations() -> Iterable[tuple[str, dict[str, Any]]]:
    """모든 strategy 의 모든 param 조합을 (strategy_id, params) 로 yield."""
    for sid, grid in _GRIDS.items():
        for combo in grid.combinations():
            yield sid, combo
