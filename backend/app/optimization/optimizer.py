"""Strategy Optimizer — 그리드 서치 + 다중 지표 평가 + Walk-forward.

본 모듈은 `BacktestEngine` (#23) + `metrics` (#24) + `walk_forward_runner` (#25)
를 read-only 로 재사용하는 *연구용 파이프라인*. 실 주문 / broker / 한투 API 호출
0건.

CLAUDE.md 절대 원칙 (정적 grep 가드):
- broker / OrderExecutor / route_order / paper_trader / `app.ai.assist` /
  `app.ai.client` / `anthropic` / `openai` / `httpx` / `requests` /
  외부 거래소 client import 0건.
- DB write 0건 — `BacktestRun` 등 기록은 호출자 책임.
- `OrderRequest` import / 생성 / annotation 0건.
- `OptimizationResult.is_order_signal=False` / `auto_apply_allowed=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.backtest.engine import BacktestEngine
from app.backtest.metrics import (
    expectancy,
    max_consecutive_losses,
    max_drawdown,
    profit_factor,
    total_pnl,
    win_count,
    win_rate,
)
from app.backtest.types import Bar, BacktestConfig, BacktestResult
from app.optimization.param_space import ParamGrid, get_param_grid, supported_strategy_ids
from app.strategies.concrete import STRATEGY_REGISTRY


# ----------------------------------------------------------------------
# DTO
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationResult:
    """단일 (strategy_id, params) 의 백테스트 평가 결과.

    *advisory* — 운영자 / Agent 가 paper 후보 선정 / 리포트 작성에만 사용.
    주문 신호 / 자동 적용 트리거 *아님*.
    """
    strategy_id:           str
    params:                dict[str, Any]
    trade_count:           int
    win_count:             int
    win_rate:              float
    expectancy:            float
    profit_factor:         float | None
    total_pnl:             int
    max_drawdown:          int
    max_consecutive_losses: int
    loss_concentration:    float
    # invariants — 결과 객체가 *주문 신호 아님*.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_investment_advice:  bool = False

    def __post_init__(self) -> None:
        # invariant 강제 — 외부에서 True 로 생성 시도 즉시 차단.
        if self.is_order_signal is not False:
            raise ValueError("OptimizationResult.is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("OptimizationResult.auto_apply_allowed must be False")
        if self.is_investment_advice is not False:
            raise ValueError("OptimizationResult.is_investment_advice must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id":            self.strategy_id,
            "params":                 dict(self.params),
            "trade_count":            self.trade_count,
            "win_count":              self.win_count,
            "win_rate":               self.win_rate,
            "expectancy":             self.expectancy,
            "profit_factor":          self.profit_factor,
            "total_pnl":              self.total_pnl,
            "max_drawdown":           self.max_drawdown,
            "max_consecutive_losses": self.max_consecutive_losses,
            "loss_concentration":     self.loss_concentration,
            "is_order_signal":        self.is_order_signal,
            "auto_apply_allowed":     self.auto_apply_allowed,
            "is_investment_advice":   self.is_investment_advice,
        }


# ----------------------------------------------------------------------
# 평가
# ----------------------------------------------------------------------


def _loss_concentration(trades: list[Any]) -> float:
    """손실 집중도 (0-1).

    상위 N개 (전체의 20%) 손실이 총 손실에서 차지하는 비중. 0.8 이상이면
    "소수 거래에 손실이 몰림" → 단일 거래 의존 위험.

    정의: top_20pct_loss_sum / total_loss_sum. 거래 0건 / 손실 0건이면 0.
    """
    losses = [t for t in trades if (getattr(t, "pnl", 0) or 0) < 0]
    if not losses:
        return 0.0
    total_loss = sum(abs(getattr(t, "pnl", 0) or 0) for t in losses)
    if total_loss == 0:
        return 0.0
    losses_sorted = sorted(
        losses, key=lambda t: abs(getattr(t, "pnl", 0) or 0), reverse=True
    )
    top_k = max(1, len(losses_sorted) // 5)  # 상위 20%
    top_loss = sum(abs(getattr(t, "pnl", 0) or 0) for t in losses_sorted[:top_k])
    return float(top_loss / total_loss)


def evaluate_backtest(
    strategy_id: str,
    params: dict[str, Any],
    bars: list[Bar],
    *,
    config: BacktestConfig | None = None,
    initial_cash: int = 10_000_000,
    quantity: int = 1,
) -> OptimizationResult:
    """단일 (strategy_id, params) 백테스트 실행 + 지표 계산.

    Args:
        strategy_id: STRATEGY_REGISTRY 의 키. 없으면 KeyError.
        params: 전략 __init__ 에 전달할 kwargs.
        bars: 시계열 bar 목록.
        config: BacktestConfig — None 이면 legacy(same_close) 사용.

    Returns:
        OptimizationResult — 다중 지표를 capture. broker 호출 0건.
    """
    if strategy_id not in STRATEGY_REGISTRY:
        raise KeyError(f"unknown strategy_id: {strategy_id!r}")

    strategy_cls = STRATEGY_REGISTRY[strategy_id]
    strategy = strategy_cls(**params)

    engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
    result: BacktestResult = engine.run(bars, strategy, config=config)
    trades = list(result.trades)

    pf = profit_factor(trades)
    return OptimizationResult(
        strategy_id=strategy_id,
        params=dict(params),
        trade_count=len(trades),
        win_count=win_count(trades),
        win_rate=win_rate(trades),
        expectancy=expectancy(trades),
        profit_factor=pf,
        total_pnl=total_pnl(trades),
        max_drawdown=max_drawdown(trades),
        max_consecutive_losses=max_consecutive_losses(trades),
        loss_concentration=_loss_concentration(trades),
    )


# ----------------------------------------------------------------------
# 그리드 서치
# ----------------------------------------------------------------------


def grid_search(
    strategy_id: str,
    bars: list[Bar],
    *,
    grid: ParamGrid | None = None,
    config: BacktestConfig | None = None,
    initial_cash: int = 10_000_000,
    quantity: int = 1,
) -> list[OptimizationResult]:
    """단일 전략의 모든 param 조합에 대해 백테스트 실행.

    조합 0건이면 빈 리스트.
    """
    g = grid if grid is not None else get_param_grid(strategy_id)
    results: list[OptimizationResult] = []
    for combo in g.combinations():
        results.append(
            evaluate_backtest(
                strategy_id,
                combo,
                bars,
                config=config,
                initial_cash=initial_cash,
                quantity=quantity,
            )
        )
    return results


def grid_search_all(
    bars_by_strategy: dict[str, list[Bar]],
    *,
    config: BacktestConfig | None = None,
    initial_cash: int = 10_000_000,
    quantity: int = 1,
) -> dict[str, list[OptimizationResult]]:
    """6개 strategy 모두 grid_search 실행.

    Args:
        bars_by_strategy: {strategy_id: bars}. 누락된 전략은 skip.
    """
    out: dict[str, list[OptimizationResult]] = {}
    for sid in supported_strategy_ids():
        if sid not in bars_by_strategy:
            continue
        out[sid] = grid_search(
            sid, bars_by_strategy[sid],
            config=config,
            initial_cash=initial_cash,
            quantity=quantity,
        )
    return out
