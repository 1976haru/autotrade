"""Stress test pipeline — 5 시나리오로 전략 robustness 평가.

본 패키지는 *연구용* — broker / 외부 API import 0건. CLAUDE.md 절대 원칙
유지.
"""

from app.stress_test.runner import (
    StressResult,
    run_all_scenarios,
    run_correlation_pair,
    run_scenario,
)
from app.stress_test.scenarios import (
    ScenarioParams,
    StressScenario,
    apply_data_missing,
    apply_fill_rejection,
    apply_high_correlation,
    apply_latency,
    apply_signal_overload,
    transform,
)

__all__ = [
    "ScenarioParams",
    "StressResult",
    "StressScenario",
    "apply_data_missing",
    "apply_fill_rejection",
    "apply_high_correlation",
    "apply_latency",
    "apply_signal_overload",
    "run_all_scenarios",
    "run_correlation_pair",
    "run_scenario",
    "transform",
]
