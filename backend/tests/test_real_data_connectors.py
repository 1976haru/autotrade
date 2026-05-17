"""3-04 walk-forward + 3-05 stress test connector 구조 테스트.

본 PR 시점에는 *준비 stub* — 자동 실행은 후속 PR. 본 테스트는:
- dataclass 구조 / verdict enum 값
- OVERFIT_RISK 판정 helper
- 6 시나리오 catalog
- 정적 import 가드 (broker / OrderExecutor / route_order 0건)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.backtest.real_data.walk_forward_connector import (
    WalkForwardSplit,
    WalkForwardVerdict,
    assess_walk_forward_overfit,
)
from app.backtest.real_data.stress_test_connector import (
    STRESS_SCENARIO_CATALOG,
    StressScenario,
    StressVerdict,
    list_stress_scenarios,
)


class TestWalkForwardConnector:
    def test_healthy_when_both_positive(self):
        split = WalkForwardSplit(
            train_metrics={"expectancy": 500.0},
            validation_metrics={"expectancy": 400.0},
            fold_count=5,
            train_bars=200,
            validation_bars=80,
        )
        r = assess_walk_forward_overfit(split)
        assert r.verdict == WalkForwardVerdict.HEALTHY

    def test_overfit_risk_when_validation_much_lower(self):
        split = WalkForwardSplit(
            train_metrics={"expectancy": 1000.0},
            validation_metrics={"expectancy": 100.0},
            fold_count=5,
            train_bars=200,
            validation_bars=80,
        )
        r = assess_walk_forward_overfit(split, overfit_ratio=0.5)
        assert r.verdict == WalkForwardVerdict.OVERFIT_RISK

    def test_overfit_risk_when_validation_negative(self):
        split = WalkForwardSplit(
            train_metrics={"expectancy": 500.0},
            validation_metrics={"expectancy": -100.0},
            fold_count=5,
            train_bars=200,
            validation_bars=80,
        )
        r = assess_walk_forward_overfit(split)
        assert r.verdict == WalkForwardVerdict.OVERFIT_RISK

    def test_underfit_when_both_negative(self):
        split = WalkForwardSplit(
            train_metrics={"expectancy": -100.0},
            validation_metrics={"expectancy": -50.0},
            fold_count=5,
            train_bars=200,
            validation_bars=80,
        )
        r = assess_walk_forward_overfit(split)
        assert r.verdict == WalkForwardVerdict.UNDERFIT

    def test_insufficient_when_fold_count_too_low(self):
        split = WalkForwardSplit(
            train_metrics={"expectancy": 500.0},
            validation_metrics={"expectancy": 400.0},
            fold_count=1,
            train_bars=200,
            validation_bars=80,
        )
        r = assess_walk_forward_overfit(split, min_fold_count=3)
        assert r.verdict == WalkForwardVerdict.INSUFFICIENT

    def test_verdict_enum_has_no_order_labels(self):
        for v in WalkForwardVerdict:
            assert v.value not in {"BUY", "SELL", "HOLD"}


class TestStressTestConnector:
    def test_catalog_has_six_scenarios(self):
        names = {s.name for s in STRESS_SCENARIO_CATALOG}
        expected = {
            StressScenario.CRASH,
            StressScenario.SURGE,
            StressScenario.SIDEWAYS,
            StressScenario.SLIPPAGE,
            StressScenario.DATA_GAP,
            StressScenario.EXECUTION_REJECT,
        }
        assert names == expected

    def test_list_returns_dicts(self):
        items = list_stress_scenarios()
        assert len(items) == 6
        for item in items:
            assert "name" in item
            assert "description" in item
            assert "notes" in item

    def test_verdict_enum_has_no_order_labels(self):
        for v in StressVerdict:
            assert v.value not in {"BUY", "SELL", "HOLD"}


class TestConnectorNoForbiddenImports:
    def test_walk_forward_no_broker_imports(self):
        import app.backtest.real_data.walk_forward_connector as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order",
            r"route_order\s*\(",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden in walk_forward_connector: {pat}"

    def test_stress_test_no_broker_imports(self):
        import app.backtest.real_data.stress_test_connector as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order",
            r"route_order\s*\(",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden in stress_test_connector: {pat}"
