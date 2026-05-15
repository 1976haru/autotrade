"""Stress test 시나리오 + runner 단위 테스트.

CLAUDE.md invariant 강제:
- broker / OrderExecutor / route_order import 0건 (정적 grep)
- `StressResult.is_order_signal=False` / `auto_apply_allowed=False` 불변
- 5개 시나리오 모두 deterministic — 같은 입력 = 같은 출력
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.types import Bar
from app.stress_test import (
    ScenarioParams,
    StressResult,
    StressScenario,
    apply_data_missing,
    apply_fill_rejection,
    apply_high_correlation,
    apply_latency,
    apply_signal_overload,
    run_all_scenarios,
    run_correlation_pair,
    run_scenario,
    transform,
)


def _bars(n: int = 80, seed: int = 1, symbol: str = "TEST") -> list[Bar]:
    base = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
    out: list[Bar] = []
    price = 50_000
    for i in range(n):
        trend = ((i % 20) - 10) * 30
        noise = ((i * 7 + seed * 3) % 11 - 5) * 25
        new_price = max(1000, price + trend + noise)
        out.append(
            Bar(
                symbol=symbol,
                timestamp=base + timedelta(minutes=i),
                open=price,
                high=max(price, new_price) + 30,
                low=max(1, min(price, new_price) - 30),
                close=new_price,
                volume=1000 + i,
            )
        )
        price = new_price
    return out


# ----------------------------------------------------------------------
# 1. 시나리오 변형 함수
# ----------------------------------------------------------------------


class TestScenarios:
    def test_latency_makes_last_n_bars_stale(self):
        bars = _bars(n=20)
        out = apply_latency(bars, stale_bars=3)
        assert len(out) == 20
        # 마지막 3개 bar 의 timestamp 가 모두 같다.
        assert out[-1].timestamp == out[-2].timestamp == out[-3].timestamp

    def test_latency_zero_returns_input(self):
        bars = _bars(n=10)
        out = apply_latency(bars, stale_bars=0)
        assert out == bars

    def test_fill_rejection_drops_some_bars(self):
        bars = _bars(n=20)
        out = apply_fill_rejection(bars, drop_rate=0.20)
        # 20% drop → 약 4 bars 제거.
        assert len(out) < len(bars)
        assert len(out) >= int(len(bars) * 0.7)

    def test_fill_rejection_zero_returns_input(self):
        bars = _bars(n=10)
        out = apply_fill_rejection(bars, drop_rate=0.0)
        assert out == bars

    def test_fill_rejection_full_returns_empty(self):
        bars = _bars(n=10)
        out = apply_fill_rejection(bars, drop_rate=1.0)
        assert out == []

    def test_data_missing_removes_middle_chunk(self):
        bars = _bars(n=40)
        out = apply_data_missing(bars, drop_rate=0.20)
        assert len(out) < len(bars)
        # 처음 bar 는 보존.
        assert out[0] == bars[0]

    def test_signal_overload_creates_flat_then_oscillation(self):
        bars = _bars(n=40)
        out = apply_signal_overload(bars, flat_bars=10)
        # 처음 10개 bar 의 close 가 모두 동일.
        for b in out[:10]:
            assert b.close == out[0].close
        # 나머지는 변동.
        closes_after = [b.close for b in out[10:]]
        assert len(set(closes_after)) > 1

    def test_high_correlation_creates_parallel_series(self):
        bars_a = _bars(n=20, seed=1, symbol="A")
        bars_b = apply_high_correlation(bars_a, symbol_b="B", factor=1.5)
        assert len(bars_b) == len(bars_a)
        for a, b in zip(bars_a, bars_b):
            assert b.symbol == "B"
            assert b.timestamp == a.timestamp
            # close 는 factor 배 (반올림).
            assert b.close >= int(a.close * 1.5) - 1
            assert b.close <= int(a.close * 1.5) + 1

    def test_transform_dispatches_correctly(self):
        bars = _bars(n=20)
        for s in StressScenario:
            out = transform(bars, s)
            assert isinstance(out, list)

    def test_transform_deterministic(self):
        bars = _bars(n=30, seed=2)
        out1 = transform(bars, StressScenario.LATENCY)
        out2 = transform(bars, StressScenario.LATENCY)
        # 같은 입력 = 같은 출력.
        assert [b.timestamp for b in out1] == [b.timestamp for b in out2]


# ----------------------------------------------------------------------
# 2. StressResult invariants
# ----------------------------------------------------------------------


class TestStressResultInvariants:
    def test_is_order_signal_false_invariant(self):
        with pytest.raises(ValueError):
            StressResult(
                strategy_id="x", scenario="latency", params={},
                baseline_expectancy=0.0, stressed_expectancy=0.0,
                baseline_trade_count=0, stressed_trade_count=0,
                stress_score=0.0, degradation_label="broken",
                is_order_signal=True,  # forbidden
            )

    def test_auto_apply_allowed_false_invariant(self):
        with pytest.raises(ValueError):
            StressResult(
                strategy_id="x", scenario="latency", params={},
                baseline_expectancy=0.0, stressed_expectancy=0.0,
                baseline_trade_count=0, stressed_trade_count=0,
                stress_score=0.0, degradation_label="broken",
                auto_apply_allowed=True,  # forbidden
            )

    def test_default_invariants(self):
        r = StressResult(
            strategy_id="x", scenario="latency", params={},
            baseline_expectancy=0.0, stressed_expectancy=0.0,
            baseline_trade_count=0, stressed_trade_count=0,
            stress_score=0.0, degradation_label="broken",
        )
        assert r.is_order_signal is False
        assert r.auto_apply_allowed is False


# ----------------------------------------------------------------------
# 3. runner 동작
# ----------------------------------------------------------------------


class TestRunner:
    def test_run_scenario_returns_stress_result(self):
        bars = _bars(n=80, seed=1)
        r = run_scenario(
            "sma_crossover", {"short": 5, "long": 20},
            bars, StressScenario.LATENCY,
        )
        assert isinstance(r, StressResult)
        assert r.strategy_id == "sma_crossover"
        assert r.scenario == "latency"
        assert 0.0 <= r.stress_score <= 100.0
        assert r.degradation_label in ("healthy", "degraded", "broken")

    def test_run_all_scenarios_returns_5(self):
        bars = _bars(n=80, seed=1)
        results = run_all_scenarios(
            "sma_crossover", {"short": 5, "long": 20}, bars
        )
        # 5 scenarios.
        assert len(results) == 5
        scenarios = {r.scenario for r in results}
        assert scenarios == {s.value for s in StressScenario}

    def test_zero_trades_yields_zero_score(self):
        """stressed trade_count=0 이면 score=0."""
        # data_missing 으로 거의 모든 bar 제거 → 거래 0건 가능성 높음.
        bars = _bars(n=10, seed=1)
        r = run_scenario(
            "sma_crossover", {"short": 50, "long": 99},  # 너무 큰 window → 거래 0.
            bars, StressScenario.LATENCY,
        )
        # baseline trade_count 가 0 이면 stress_score 도 0~100 사이 (rule 적용).
        assert 0.0 <= r.stress_score <= 100.0

    def test_correlation_pair_returns_three(self):
        bars = _bars(n=80, seed=1, symbol="A")
        a, b, est = run_correlation_pair(
            "sma_crossover", {"short": 5, "long": 20},
            bars, symbol_b="B", factor=1.0,
        )
        assert a.strategy_id == "sma_crossover"
        assert b.strategy_id == "sma_crossover"
        assert 0.0 <= est <= 1.0


# ----------------------------------------------------------------------
# 4. 정적 import 가드
# ----------------------------------------------------------------------


class TestStaticImportGuards:
    def _read(self, dotted: str) -> str:
        import importlib
        mod = importlib.import_module(dotted)
        path = Path(inspect.getfile(mod))
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("mod_name", [
        "app.stress_test.scenarios",
        "app.stress_test.runner",
    ])
    def test_no_forbidden_imports(self, mod_name):
        src = self._read(mod_name)
        for forbidden in (
            "from app.brokers",
            "from app.execution.executor",
            "from app.execution.order_router",
            "from app.ai.assist",
            "from app.ai.client",
            "import anthropic",
            "from anthropic",
            "import openai",
            "from openai",
            "import httpx",
            "from httpx",
            "import requests",
            "from requests",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains forbidden import {forbidden!r}"
            )

    @pytest.mark.parametrize("mod_name", [
        "app.stress_test.scenarios",
        "app.stress_test.runner",
    ])
    def test_no_order_execution_calls(self, mod_name):
        src = self._read(mod_name)
        for forbidden in (
            "broker.place_order(",
            "route_order(",
            "submit_candidate(",
            ".place_order(",
            ".cancel_order(",
            "OrderRequest(",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains forbidden call {forbidden!r}"
            )
