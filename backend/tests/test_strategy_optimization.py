"""Strategy Optimization & Paper Readiness 파이프라인 단위 테스트.

CLAUDE.md invariant 강제:
- broker / OrderExecutor / route_order / 외부 API import 0건 (정적 grep)
- `OptimizationResult.is_order_signal=False` / `auto_apply_allowed=False` 불변
- `PaperCandidate` 동일 invariant
- `PaperReadinessRecommendation` 동일 invariant
- AgentBase 호환 + AgentOutput.is_order_intent=False 강제
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.paper_readiness_agent import (
    PaperReadinessAgent,
    PaperReadinessRecommendation,
    evaluate_paper_readiness,
)
from app.backtest.types import Bar
from app.optimization import (
    OptimizationResult,
    ParamGrid,
    PaperCandidate,
    PaperCandidateCriteria,
    all_combinations,
    evaluate_backtest,
    get_param_grid,
    grid_search,
    grid_search_all,
    pick_paper_candidates,
    rank_results,
    supported_strategy_ids,
)


# ----------------------------------------------------------------------
# 합성 bars
# ----------------------------------------------------------------------


def _synthetic_bars(symbol: str = "TEST", n: int = 80, seed: int = 1) -> list[Bar]:
    base = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    price = 50_000
    for i in range(n):
        trend = ((i % 20) - 10) * 30
        noise = ((i * 7 + seed * 3) % 11 - 5) * 25
        new_price = max(1000, price + trend + noise)
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=base + timedelta(minutes=i),
                open=price,
                high=max(price, new_price) + 30,
                low=max(1, min(price, new_price) - 30),
                close=new_price,
                volume=1000 + i * 5,
            )
        )
        price = new_price
    return bars


# ----------------------------------------------------------------------
# 1. param_space invariant
# ----------------------------------------------------------------------


class TestParamSpace:
    def test_supported_strategy_ids_returns_6(self):
        sids = supported_strategy_ids()
        assert len(sids) == 6
        assert "sma_crossover" in sids
        assert "rsi_reversion" in sids
        assert "vwap_strategy" in sids
        assert "orb_vwap" in sids
        assert "volume_breakout" in sids
        assert "pullback_rebreak" in sids

    def test_no_fake_strategy_names(self):
        """code 의 6개 strategy_id 외 *어떤 가짜 전략명* 도 등장 0건."""
        from app.strategies.concrete import STRATEGY_REGISTRY

        for sid in supported_strategy_ids():
            assert sid in STRATEGY_REGISTRY, (
                f"unknown strategy_id {sid!r} not in STRATEGY_REGISTRY"
            )

    def test_param_grid_combinations(self):
        g = get_param_grid("sma_crossover")
        combos = g.combinations()
        # short(3) × long(3) = 9 조합
        assert len(combos) == 9
        for c in combos:
            assert "short" in c
            assert "long" in c

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError):
            get_param_grid("not_a_real_strategy")

    def test_all_combinations_yields_tuples(self):
        items = list(all_combinations())
        assert len(items) > 0
        for sid, params in items:
            assert sid in supported_strategy_ids()
            assert isinstance(params, dict)


# ----------------------------------------------------------------------
# 2. OptimizationResult invariants
# ----------------------------------------------------------------------


class TestOptimizationResultInvariants:
    def test_is_order_signal_false_invariant(self):
        with pytest.raises(ValueError):
            OptimizationResult(
                strategy_id="sma_crossover",
                params={"short": 5, "long": 20},
                trade_count=0,
                win_count=0,
                win_rate=0.0,
                expectancy=0.0,
                profit_factor=None,
                total_pnl=0,
                max_drawdown=0,
                max_consecutive_losses=0,
                loss_concentration=0.0,
                is_order_signal=True,   # forbidden
            )

    def test_auto_apply_allowed_false_invariant(self):
        with pytest.raises(ValueError):
            OptimizationResult(
                strategy_id="sma_crossover",
                params={"short": 5, "long": 20},
                trade_count=0,
                win_count=0,
                win_rate=0.0,
                expectancy=0.0,
                profit_factor=None,
                total_pnl=0,
                max_drawdown=0,
                max_consecutive_losses=0,
                loss_concentration=0.0,
                auto_apply_allowed=True,  # forbidden
            )

    def test_default_invariants(self):
        r = OptimizationResult(
            strategy_id="sma_crossover",
            params={"short": 5, "long": 20},
            trade_count=0,
            win_count=0,
            win_rate=0.0,
            expectancy=0.0,
            profit_factor=None,
            total_pnl=0,
            max_drawdown=0,
            max_consecutive_losses=0,
            loss_concentration=0.0,
        )
        assert r.is_order_signal is False
        assert r.auto_apply_allowed is False
        assert r.is_investment_advice is False


# ----------------------------------------------------------------------
# 3. evaluate_backtest + grid_search
# ----------------------------------------------------------------------


class TestBacktestEvaluation:
    def test_evaluate_backtest_returns_result(self):
        bars = _synthetic_bars(n=80, seed=1)
        r = evaluate_backtest(
            "sma_crossover", {"short": 5, "long": 20}, bars
        )
        assert isinstance(r, OptimizationResult)
        assert r.strategy_id == "sma_crossover"
        assert r.params == {"short": 5, "long": 20}
        assert r.trade_count >= 0
        assert 0.0 <= r.win_rate <= 1.0

    def test_grid_search_runs_all_combos(self):
        bars = _synthetic_bars(n=80, seed=2)
        results = grid_search("sma_crossover", bars)
        # 9 조합
        assert len(results) == 9
        for r in results:
            assert r.strategy_id == "sma_crossover"

    def test_grid_search_all_covers_all_strategies(self):
        bars_by = {
            sid: _synthetic_bars(symbol=f"S{i:02d}", n=80, seed=i + 1)
            for i, sid in enumerate(supported_strategy_ids())
        }
        results = grid_search_all(bars_by)
        assert set(results.keys()) == set(supported_strategy_ids())

    def test_evaluate_backtest_unknown_strategy_raises(self):
        with pytest.raises(KeyError):
            evaluate_backtest("not_a_strategy", {}, [])


# ----------------------------------------------------------------------
# 4. paper_picker
# ----------------------------------------------------------------------


def _mk_result(
    sid: str,
    expectancy: float,
    trade_count: int = 10,
    win_rate: float = 0.55,
    profit_factor: float | None = 1.5,
    max_consec_losses: int = 2,
    loss_concentration: float = 0.4,
    max_dd: int = -1000,
    params: dict | None = None,
) -> OptimizationResult:
    return OptimizationResult(
        strategy_id=sid,
        params=params or {"x": 1},
        trade_count=trade_count,
        win_count=int(trade_count * win_rate),
        win_rate=win_rate,
        expectancy=expectancy,
        profit_factor=profit_factor,
        total_pnl=int(expectancy * trade_count),
        max_drawdown=max_dd,
        max_consecutive_losses=max_consec_losses,
        loss_concentration=loss_concentration,
    )


class TestPaperPicker:
    def test_passing_candidate(self):
        results = {"sma_crossover": [_mk_result("sma_crossover", 100.0)]}
        candidates = pick_paper_candidates(results)
        assert len(candidates) == 1
        assert candidates[0].passed is True
        assert candidates[0].is_order_signal is False
        assert candidates[0].auto_apply_allowed is False

    def test_failing_candidate_low_expectancy(self):
        results = {
            "sma_crossover": [_mk_result("sma_crossover", -50.0)],
        }
        candidates = pick_paper_candidates(results)
        assert candidates[0].passed is False
        assert any("expectancy" in r for r in candidates[0].fail_reasons)

    def test_failing_candidate_low_trade_count(self):
        results = {
            "sma_crossover": [_mk_result("sma_crossover", 100.0, trade_count=2)],
        }
        candidates = pick_paper_candidates(results)
        assert candidates[0].passed is False
        assert any("trade_count" in r for r in candidates[0].fail_reasons)

    def test_failing_candidate_high_loss_concentration(self):
        results = {
            "sma_crossover": [
                _mk_result("sma_crossover", 100.0, loss_concentration=0.95),
            ],
        }
        candidates = pick_paper_candidates(results)
        assert candidates[0].passed is False
        assert any("loss_concentration" in r for r in candidates[0].fail_reasons)

    def test_overfit_suspected_blocks_pass(self):
        """winner 가 2.5배 우월하고 others 가 부진 → overfit 의심 → 미통과."""
        results = {
            "sma_crossover": [
                _mk_result("sma_crossover", 500.0, profit_factor=5.0, params={"x": 1}),
                _mk_result("sma_crossover", 30.0, profit_factor=1.1, params={"x": 2}),
                _mk_result("sma_crossover", 25.0, profit_factor=1.0, params={"x": 3}),
            ],
        }
        candidates = pick_paper_candidates(results)
        assert len(candidates) == 1
        # winner 는 metrics 자체는 통과지만 overfit 의심으로 passed=False.
        assert candidates[0].overfit_suspected is True
        assert candidates[0].passed is False

    def test_paper_candidate_invariants(self):
        with pytest.raises(ValueError):
            PaperCandidate(
                strategy_id="x", params={}, passed=False,
                is_order_signal=True,  # forbidden
            )
        with pytest.raises(ValueError):
            PaperCandidate(
                strategy_id="x", params={}, passed=False,
                auto_apply_allowed=True,  # forbidden
            )

    def test_rank_results_orders_by_expectancy(self):
        results = {
            "a": [_mk_result("a", 100.0), _mk_result("a", 200.0)],
            "b": [_mk_result("b", 50.0)],
        }
        ranked = rank_results(results)
        assert len(ranked) == 3
        assert ranked[0]["expectancy"] == 200.0
        assert ranked[-1]["expectancy"] == 50.0


# ----------------------------------------------------------------------
# 5. PaperReadinessAgent
# ----------------------------------------------------------------------


class TestPaperReadinessAgent:
    def _mk_passing_candidate(self, sid: str) -> PaperCandidate:
        return PaperCandidate(
            strategy_id=sid,
            params={"x": 1},
            passed=True,
            pass_reasons=("trade_count OK",),
            fail_reasons=(),
            metrics={
                "trade_count":            10,
                "win_rate":               0.6,
                "expectancy":             100.0,
                "profit_factor":          1.5,
                "total_pnl":              1000,
                "max_drawdown":           -200,
                "max_consecutive_losses": 2,
                "loss_concentration":     0.3,
            },
        )

    def test_recommendation_invariants(self):
        with pytest.raises(ValueError):
            PaperReadinessRecommendation(
                strategy_id="x", decision="REVIEW", score=50.0,
                is_order_signal=True,  # forbidden
            )

    def test_recommend_paper_when_score_high(self):
        c = self._mk_passing_candidate("sma_crossover")
        recs = evaluate_paper_readiness(
            [c], {"sma_crossover": [90.0, 85.0, 88.0, 92.0, 87.0]}
        )
        assert len(recs) == 1
        assert recs[0].decision == "RECOMMEND_PAPER"
        assert recs[0].score >= 60.0

    def test_exclude_when_candidate_failed(self):
        c = PaperCandidate(
            strategy_id="x", params={}, passed=False,
            fail_reasons=("expectancy=-10 <= min=0",),
            metrics={"win_rate": 0.0},
        )
        recs = evaluate_paper_readiness([c], {})
        assert recs[0].decision == "EXCLUDE"

    def test_review_when_overfit_suspected(self):
        c = PaperCandidate(
            strategy_id="x", params={}, passed=True,
            overfit_suspected=True,
            metrics={"win_rate": 0.6, "expectancy": 100.0},
        )
        recs = evaluate_paper_readiness(
            [c], {"x": [80.0, 80.0, 80.0]}
        )
        assert recs[0].overfit_warning is True

    def test_agent_run_returns_agent_output(self):
        agent = PaperReadinessAgent()
        ctx = AgentContext(
            extra={
                "paper_candidates":          [self._mk_passing_candidate("sma_crossover")],
                "stress_scores_by_strategy": {"sma_crossover": [90.0]},
            }
        )
        out = agent.run(ctx)
        assert out.is_order_intent is False
        assert out.can_execute_order is False
        assert out.role == AgentRole.STRATEGY_RESEARCHER
        assert out.decision in (AgentDecision.RECOMMEND, AgentDecision.REPORT)
        assert out.metadata["is_order_signal"] is False
        assert out.metadata["advisory_only"] is True

    def test_agent_metadata_forbidden_keywords(self):
        agent = PaperReadinessAgent()
        forbidden = agent.metadata.forbidden
        joined = " ".join(forbidden).lower()
        # 정책 라벨 키워드 — broker / order executor / route order 금지 명시.
        assert "broker" in joined
        assert "orderexecutor" in joined or "executor" in joined
        assert "route_order" in joined or "route order" in joined
        # paper_trader 직접 활성화 금지 라벨도 포함.
        assert "paper_trader" in joined or "paper trader" in joined


# ----------------------------------------------------------------------
# 6. 정적 import 가드
# ----------------------------------------------------------------------


class TestStaticImportGuards:
    """optimizer / paper_picker / paper_readiness_agent 가 broker / executor /
    외부 API client / OrderRequest 를 import 하지 않음을 정적 grep 으로 검증.
    """

    def _read(self, dotted: str) -> str:
        import importlib
        mod = importlib.import_module(dotted)
        path = Path(inspect.getfile(mod))
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("mod_name", [
        "app.optimization.optimizer",
        "app.optimization.paper_picker",
        "app.optimization.param_space",
        "app.agents.paper_readiness_agent",
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
            "from app.permission.gate",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains forbidden import {forbidden!r}"
            )

    @pytest.mark.parametrize("mod_name", [
        "app.optimization.optimizer",
        "app.optimization.paper_picker",
        "app.agents.paper_readiness_agent",
    ])
    def test_no_order_execution_calls(self, mod_name):
        src = self._read(mod_name)
        # 본 patterns 는 *코드 호출 / 타입 사용* 만 검사 — docstring/주석 내
        # 언급은 허용 (기존 agent_memory 등에서 사용하는 패턴).
        for forbidden in (
            "broker.place_order(",
            "route_order(",
            "submit_candidate(",
            ".place_order(",
            ".cancel_order(",
            "OrderRequest(",
            ": OrderRequest",
            "-> OrderRequest",
        ):
            # 단, forbidden 라벨이 *forbidden 리스트 자체* 에 등장하는 경우
            # (PaperReadinessAgent.metadata.forbidden) 는 정책 선언이므로 허용.
            # 단순 substring 매치는 false-positive 가 많아 import / call /
            # annotation 의 *문법적 컨텍스트* 만 확인하는 패턴 사용.
            assert forbidden not in src, (
                f"{mod_name} contains forbidden call/annotation {forbidden!r}"
            )
