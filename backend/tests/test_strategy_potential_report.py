"""STRATEGY-VALIDATION-01 — strategy_potential 모듈 + endpoint 테스트.

- 종합 판정 5단계 + data-sufficiency 게이팅 (sample fixture 만으로 STRONG 불가).
- sub-score 0~100 clamp + None(평가불가) 처리.
- 리포트 불변값 (do_not_auto_apply=True / is_live_authorization=False / is_order_signal=
  False / auto_apply_allowed=False / contains_secret=False).
- 모듈이 broker / OrderExecutor / route_order / KIS API 를 import 하지 않음.
- GET /api/system/strategy-potential read-only 응답 (실제 주문 0건).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.system.strategy_potential import (
    BLOCKED,
    CAUTIOUS,
    PAPER_EARLY_SIGNAL,
    PAPER_GATE_EVALUABLE,
    PAPER_NO_TRADES_YET,
    PAPER_SAMPLE_TOO_SMALL,
    RESEARCH_ONLY,
    STRONG,
    StrategyPotentialInputs,
    StrategyPotentialReport,
    classify_paper_sample,
    evaluate_strategy_potential,
    render_markdown,
    to_dict,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _good_backtest():
    return {
        "insufficient_data": False, "bar_count": 300,
        "council": {"performance": {
            "win_rate": 0.6, "profit_factor": 1.8, "expectancy": 5.0,
            "max_drawdown": -3, "max_consecutive_losses": 3,
            "by_market_regime": {}, "by_time_phase": {}}},
        "comparison": {"council_better_than_best_single": True},
    }


def _good_wf():
    return {
        "insufficient_data": False, "overall_stability_score": 0.8,
        "overall_overfit_suspected": False, "collapse_segments": [],
        "split_count": 3, "council_vs_best_single": {"council_better_fraction": 0.7},
    }


def _pass_stress():
    return {"counts": {"PASS": 12, "WARN": 0, "FAIL": 0}}


# --------------------------- verdict / gating ------------------------------

def test_empty_inputs_research_only():
    r = evaluate_strategy_potential(StrategyPotentialInputs())
    assert r.overall_verdict == RESEARCH_ONLY
    assert r.paper_sample_class == PAPER_NO_TRADES_YET
    assert r.sample_fixture_only is True


def test_sample_fixture_good_metrics_cannot_be_strong():
    """sample fixture(실데이터 아님) 만으로는 STRONG 불가 — data-sufficiency 게이팅."""
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=_good_backtest(), walk_forward=_good_wf(),
        stress=_pass_stress(), has_real_data=False))
    assert r.overall_verdict != STRONG
    assert r.overall_verdict == CAUTIOUS


def test_real_data_plus_paper_gate_allows_strong():
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=_good_backtest(), walk_forward=_good_wf(), stress=_pass_stress(),
        paper={"evaluated_trades": 120, "trading_days": 30, "win_rate": 0.58, "expectancy": 4.0},
        order_quality={"order_failure_rate": 0.01, "rejected_rate": 0.005},
        has_real_data=True))
    assert r.overall_verdict == STRONG


def test_stress_fail_two_is_blocked():
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=_good_backtest(), walk_forward=_good_wf(),
        stress={"counts": {"PASS": 10, "WARN": 0, "FAIL": 2}}, has_real_data=True))
    assert r.overall_verdict == BLOCKED


def test_stress_fail_one_not_ready():
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=_good_backtest(), walk_forward=_good_wf(),
        stress={"counts": {"PASS": 11, "WARN": 0, "FAIL": 1}}, has_real_data=True))
    assert r.overall_verdict == "NOT_READY"


def test_negative_expectancy_not_ready():
    bt = _good_backtest()
    bt["council"]["performance"]["expectancy"] = -2.0
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=bt, walk_forward=_good_wf(), stress=_pass_stress(), has_real_data=True))
    assert r.overall_verdict == "NOT_READY"


def test_overfit_suspected_research_only():
    wf = _good_wf()
    wf["overall_overfit_suspected"] = True
    wf["overall_stability_score"] = 0.3
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest={"insufficient_data": False, "bar_count": 50,
                  "council": {"performance": {"win_rate": 0.4, "profit_factor": 1.1,
                              "expectancy": 0.5, "max_consecutive_losses": 7,
                              "by_market_regime": {}, "by_time_phase": {}}},
                  "comparison": {"council_better_than_best_single": False}},
        walk_forward=wf, stress=_pass_stress(), has_real_data=True))
    assert r.overall_verdict in (RESEARCH_ONLY, "NOT_READY")


# --------------------------- paper sample classes --------------------------

@pytest.mark.parametrize("trades,days,expected", [
    (0, 0, PAPER_NO_TRADES_YET),
    (10, 5, PAPER_SAMPLE_TOO_SMALL),
    (50, 10, PAPER_EARLY_SIGNAL),
    (120, 30, PAPER_GATE_EVALUABLE),
    (120, 10, PAPER_EARLY_SIGNAL),   # days 부족
    (80, 40, PAPER_EARLY_SIGNAL),    # trades 부족
])
def test_classify_paper_sample(trades, days, expected):
    assert classify_paper_sample(
        {"evaluated_trades": trades, "trading_days": days}) == expected


# --------------------------- scores ----------------------------------------

def test_subscores_in_range_or_none():
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=_good_backtest(), walk_forward=_good_wf(), stress=_pass_stress()))
    for s in r.sub_scores:
        assert s.score is None or (0.0 <= s.score <= 100.0)
    assert r.overall_strategy_potential_score is None or (
        0.0 <= r.overall_strategy_potential_score <= 100.0)


def test_paper_execution_none_when_sample_too_small():
    r = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=_good_backtest(),
        paper={"evaluated_trades": 5, "trading_days": 2}))
    assert r.paper_execution_score is None


# --------------------------- invariants ------------------------------------

def test_report_invariants_default():
    r = evaluate_strategy_potential(StrategyPotentialInputs(backtest=_good_backtest()))
    assert r.do_not_auto_apply is True
    assert r.auto_apply_allowed is False
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.contains_secret is False
    assert r.method_fit.get("do_not_auto_apply") is True


@pytest.mark.parametrize("bad", [
    {"do_not_auto_apply": False},
    {"auto_apply_allowed": True},
    {"is_live_authorization": True},
    {"is_order_signal": True},
    {"contains_secret": True},
])
def test_report_guard_rejects_unsafe(bad):
    base = dict(
        generated_at="x", overall_verdict=RESEARCH_ONLY,
        overall_strategy_potential_score=10.0, backtest_score=None,
        walk_forward_score=None, stress_resilience_score=None,
        paper_execution_score=None, agent_value_score=None,
        risk_control_score=None, data_sufficiency_score=None,
        paper_sample_class=PAPER_NO_TRADES_YET, agent_value_verdict="x")
    base.update(bad)
    with pytest.raises(ValueError):
        StrategyPotentialReport(**base)


def test_invalid_verdict_rejected():
    with pytest.raises(ValueError):
        StrategyPotentialReport(
            generated_at="x", overall_verdict="MOON",
            overall_strategy_potential_score=10.0, backtest_score=None,
            walk_forward_score=None, stress_resilience_score=None,
            paper_execution_score=None, agent_value_score=None,
            risk_control_score=None, data_sufficiency_score=None,
            paper_sample_class=PAPER_NO_TRADES_YET, agent_value_verdict="x")


# --------------------------- markdown / serialize --------------------------

def test_markdown_has_disclaimers_and_no_profit_guarantee():
    md = render_markdown(evaluate_strategy_potential(
        StrategyPotentialInputs(backtest=_good_backtest())))
    assert "자동 적용 아님" in md
    assert "실전 승인 아님" in md
    assert "수익 보장 아님" in md


def test_to_dict_roundtrip_keys():
    d = to_dict(evaluate_strategy_potential(StrategyPotentialInputs(backtest=_good_backtest())))
    for k in ("overall_verdict", "overall_strategy_potential_score", "backtest_score",
              "agent_value_verdict", "do_not_auto_apply", "is_live_authorization",
              "sample_fixture_only", "sub_scores"):
        assert k in d


# --------------------------- read-only guard -------------------------------

def test_module_no_forbidden_imports_or_calls():
    src = (_REPO_ROOT / "backend" / "app" / "system" / "strategy_potential.py").read_text(
        encoding="utf-8")
    import re
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router", "paper_trader",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor(", "submit_candidate("):
        assert call not in src, f"forbidden call: {call}"


# --------------------------- endpoint smoke --------------------------------

def test_strategy_potential_endpoint(client, safe_default_flags):
    r = client.get("/api/system/strategy-potential")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["overall_verdict"] in (
        STRONG, CAUTIOUS, RESEARCH_ONLY, "NOT_READY", BLOCKED)
    # sample fixture only → STRONG 불가, 안전 불변값.
    assert data["sample_fixture_only"] is True
    assert data["overall_verdict"] != STRONG
    assert data["do_not_auto_apply"] is True
    assert data["is_live_authorization"] is False
    assert data["is_order_signal"] is False
    # secret-like 패턴 미노출.
    import re
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_strategy_potential_endpoint_is_get_only(client, safe_default_flags):
    assert client.post("/api/system/strategy-potential", json={}).status_code in (404, 405)
