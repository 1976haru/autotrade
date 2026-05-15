"""Stress test runner — 시나리오별 전략 robustness 평가.

본 모듈은 백테스트를 *baseline + 변형* 두 번 실행해 metric degradation 을
측정. broker / 외부 API / DB write 0건.

stress_score (0~100): degradation 이 작을수록 높은 점수.
- 100: baseline 과 동일 (영향 없음)
- 0:   완전 붕괴 (거래 0건 또는 expectancy 0 이하)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.backtest.types import Bar
from app.optimization.optimizer import OptimizationResult, evaluate_backtest
from app.stress_test.scenarios import (
    ScenarioParams,
    StressScenario,
    apply_high_correlation,
    transform,
)


@dataclass(frozen=True)
class StressResult:
    """단일 (strategy_id, scenario) 평가 결과.

    *advisory* — 주문 신호 / 자동 적용 트리거 아님.
    """
    strategy_id:        str
    scenario:           str
    params:             dict[str, Any]
    baseline_expectancy: float
    stressed_expectancy: float
    baseline_trade_count: int
    stressed_trade_count: int
    stress_score:       float           # 0-100
    degradation_label:  str             # "healthy"/"degraded"/"broken"
    notes:              tuple[str, ...] = field(default_factory=tuple)
    is_order_signal:    bool = False
    auto_apply_allowed: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("StressResult.is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("StressResult.auto_apply_allowed must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id":          self.strategy_id,
            "scenario":             self.scenario,
            "params":               dict(self.params),
            "baseline_expectancy":  self.baseline_expectancy,
            "stressed_expectancy":  self.stressed_expectancy,
            "baseline_trade_count": self.baseline_trade_count,
            "stressed_trade_count": self.stressed_trade_count,
            "stress_score":         self.stress_score,
            "degradation_label":    self.degradation_label,
            "notes":                list(self.notes),
            "is_order_signal":      self.is_order_signal,
            "auto_apply_allowed":   self.auto_apply_allowed,
        }


def _stress_score(baseline_exp: float, stressed_exp: float, stressed_trades: int) -> float:
    """0-100 스코어. baseline 과 비교한 degradation 정도.

    rules:
    - stressed trade_count == 0 → 0 (완전 붕괴)
    - stressed_exp <= 0 and baseline_exp > 0 → 0 (역전)
    - baseline_exp <= 0 → stressed_exp 도 음수면 50 (둘 다 손실 — 회복 가치 X),
                          stressed_exp > 0 면 baseline 보다 *나아진* 경우 90
    - 정상 비교 → 100 × min(1, stressed_exp / baseline_exp)
    """
    if stressed_trades == 0:
        return 0.0
    if baseline_exp > 0 and stressed_exp <= 0:
        return 0.0
    if baseline_exp <= 0:
        if stressed_exp > 0:
            return 90.0
        return 50.0
    ratio = stressed_exp / baseline_exp
    return float(max(0.0, min(100.0, 100.0 * ratio)))


def _degradation_label(score: float) -> str:
    if score >= 70.0:
        return "healthy"
    if score >= 30.0:
        return "degraded"
    return "broken"


def run_scenario(
    strategy_id: str,
    params: dict[str, Any],
    bars: list[Bar],
    scenario: StressScenario,
    *,
    scenario_params: ScenarioParams | None = None,
    initial_cash: int = 10_000_000,
    quantity: int = 1,
) -> StressResult:
    """baseline + 변형 두 백테스트 비교."""
    baseline = evaluate_backtest(
        strategy_id, params, bars,
        initial_cash=initial_cash, quantity=quantity,
    )

    notes: list[str] = []
    if scenario == StressScenario.HIGH_CORRELATION:
        # 본 시나리오는 *2개 symbol 동시 진입* 시뮬 — symbol_b 가 거의 동일
        # 거동을 가짐. 백테스트 엔진은 단일 시퀀스만 처리하므로 본 runner 는
        # *advisory* — 상관도 측정 가능성을 carry 하며, 실제 동시-진입 가드는
        # CorrelationGuard (#78) / PortfolioCorrelationGuard (#95) 가 담당.
        notes.append(
            "HIGH_CORRELATION advisory: 실제 멀티-symbol 가드는 #78/#95 가 담당. "
            "본 시나리오는 동일 시퀀스로 추정 단일 백테스트."
        )
        stressed_bars = list(bars)
    else:
        stressed_bars = transform(bars, scenario, scenario_params)

    stressed = evaluate_backtest(
        strategy_id, params, stressed_bars,
        initial_cash=initial_cash, quantity=quantity,
    )

    score = _stress_score(baseline.expectancy, stressed.expectancy, stressed.trade_count)
    if stressed.trade_count == 0:
        notes.append("stressed trade_count=0 — 신호 0건")
    return StressResult(
        strategy_id=strategy_id,
        scenario=scenario.value,
        params=dict(params),
        baseline_expectancy=baseline.expectancy,
        stressed_expectancy=stressed.expectancy,
        baseline_trade_count=baseline.trade_count,
        stressed_trade_count=stressed.trade_count,
        stress_score=score,
        degradation_label=_degradation_label(score),
        notes=tuple(notes),
    )


def run_all_scenarios(
    strategy_id: str,
    params: dict[str, Any],
    bars: list[Bar],
    *,
    scenario_params: ScenarioParams | None = None,
) -> list[StressResult]:
    """5개 시나리오 모두 실행."""
    return [
        run_scenario(strategy_id, params, bars, s, scenario_params=scenario_params)
        for s in StressScenario
    ]


def run_correlation_pair(
    strategy_id: str,
    params: dict[str, Any],
    bars_a: list[Bar],
    *,
    symbol_b: str = "B-CORR",
    factor: float = 1.0,
    initial_cash: int = 10_000_000,
    quantity: int = 1,
) -> tuple[OptimizationResult, OptimizationResult, float]:
    """2개 symbol (A + 거의 동일 B) 백테스트 실행 → 동시 진입 추정 신호 carry.

    반환: (result_a, result_b, simultaneous_entry_estimate).
    simultaneous_entry_estimate 는 *상관도가 1.0 이면 거의 1.0* — 동시 진입
    가능성 라벨로만 carry. 실제 진입 차단은 RiskManager / PortfolioCorrelation
    Guard 가 담당.
    """
    bars_b = apply_high_correlation(bars_a, symbol_b, factor=factor)
    result_a = evaluate_backtest(
        strategy_id, params, bars_a,
        initial_cash=initial_cash, quantity=quantity,
    )
    result_b = evaluate_backtest(
        strategy_id, params, bars_b,
        initial_cash=initial_cash, quantity=quantity,
    )
    # 동시 진입 추정 — 두 백테스트 모두 trade > 0 이고 같은 방향 손익이면 1.0.
    if result_a.trade_count == 0 or result_b.trade_count == 0:
        est = 0.0
    else:
        # 같은 방향 (둘 다 양수 또는 둘 다 음수 expectancy) 이면 동시 진입 가능성 높음.
        same_dir = (result_a.expectancy > 0) == (result_b.expectancy > 0)
        est = 1.0 if same_dir else 0.5
    return result_a, result_b, est
