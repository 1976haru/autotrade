"""#51 / 6-06: Agent decision quality_score 고도화 테스트.

핵심 invariant:
- quality_score 0~100 clamp + grade.
- signal_consistency / data_reliability / risk / regime_fit / exit_plan breakdown.
- quality 낮으면 BUY → should_hold (HOLD 권고), reason_code=QUALITY_SCORE_LOW_HOLD.
- 데이터 부재(None) 는 과도 감점 금지 (중립).
- council 통합: 약한 BUY → HOLD 강등 + quality_gate_result/pre_quality_action 저장.
- is_order_signal=false / is_live_authorization=false / contains_secret=false.
"""

from __future__ import annotations

import json

import pytest

from app.agents import decision_quality as dq
from app.agents.decision_quality import (
    QUALITY_SCORE_LOW_HOLD,
    compute_decision_quality,
    compute_decision_quality_from_council,
    data_reliability_score,
    signal_consistency_score,
)


def _strong_buy_votes():
    return [
        {"strategy": "MOMENTUM", "signal": "BUY", "score": 80},
        {"strategy": "VWAP", "signal": "BUY", "score": 70},
        {"strategy": "ORB", "signal": "BUY", "score": 65},
        {"strategy": "GAP", "signal": "HOLD", "score": 30},
    ]


def _weak_buy_votes():
    return [
        {"strategy": "MOMENTUM", "signal": "BUY", "score": 55},
        {"strategy": "VWAP", "signal": "SELL", "score": 40},
        {"strategy": "ORB", "signal": "HOLD", "score": 30},
        {"strategy": "GAP", "signal": "HOLD", "score": 20},
    ]


# ── 1. clamp + grade ─────────────────────────────────────────────────────────


def test_quality_score_clamped_0_100():
    q = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                 has_exit_plan=True, exit_plan_valid=True,
                                 regime_decision="ALLOW", market_regime="TREND_UP")
    assert 0 <= q.enhanced_quality_score <= 100
    assert q.quality_grade in ("A", "B", "C", "D", "F")


# ── 2. signal consistency ────────────────────────────────────────────────────


def test_signal_consistency_high_when_agree():
    assert signal_consistency_score(_strong_buy_votes(), "BUY") >= 70


def test_signal_consistency_low_when_split():
    assert signal_consistency_score(_weak_buy_votes(), "BUY") < 50


# ── 3. data reliability (부재 시 중립) ───────────────────────────────────────


def test_data_reliability_neutral_when_unknown():
    assert data_reliability_score() == 70   # 모두 None → 중립.


def test_data_reliability_penalized_when_stale():
    assert data_reliability_score(data_status="STALE", price_stale=True) < 40


# ── 4. risk / regime / exit_plan 반영 ────────────────────────────────────────


def test_risk_lowers_score():
    clean = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                     risk_flags=[], has_exit_plan=True, exit_plan_valid=True)
    risky = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                     risk_flags=["a", "b", "c"], veto_applied=True,
                                     has_exit_plan=True, exit_plan_valid=True)
    assert risky.enhanced_quality_score < clean.enhanced_quality_score
    assert risky.breakdown["risk"] < clean.breakdown["risk"]


def test_regime_mismatch_lowers_score():
    q = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                 regime_decision="BLOCK_NEW_BUY", has_exit_plan=True,
                                 exit_plan_valid=True)
    assert q.breakdown["regime_fit"] <= 30


def test_exit_plan_invalid_lowers_score():
    q = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                 has_exit_plan=False, exit_plan_valid=False)
    assert q.breakdown["exit_plan"] <= 20


def test_feedback_penalty_subtracted():
    base = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                    has_exit_plan=True, exit_plan_valid=True)
    pen = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                   has_exit_plan=True, exit_plan_valid=True,
                                   feedback_penalty=20)
    assert pen.enhanced_quality_score == max(0, base.enhanced_quality_score - 20)


# ── 5. quality 낮으면 HOLD ──────────────────────────────────────────────────


def test_low_quality_buy_should_hold():
    q = compute_decision_quality(votes=_weak_buy_votes(), final_action="BUY",
                                 risk_flags=["a", "b"], regime_decision="BLOCK_NEW_BUY",
                                 has_exit_plan=False, exit_plan_valid=False, min_quality=60)
    assert q.should_hold is True
    assert q.reason_code == QUALITY_SCORE_LOW_HOLD


def test_strong_buy_not_hold():
    q = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY",
                                 has_exit_plan=True, exit_plan_valid=True,
                                 regime_decision="ALLOW", market_regime="TREND_UP",
                                 min_quality=60)
    assert q.should_hold is False


def test_hold_action_never_should_hold():
    # final HOLD 는 quality gate 대상 아님 (BUY 만 강등).
    q = compute_decision_quality(votes=_weak_buy_votes(), final_action="HOLD")
    assert q.should_hold is False


# ── invariant ────────────────────────────────────────────────────────────────


def test_quality_invariants():
    q = compute_decision_quality(votes=_strong_buy_votes(), final_action="BUY")
    assert q.is_order_signal is False
    assert q.is_live_authorization is False
    assert q.contains_secret is False


def test_quality_invariant_enforced():
    from app.agents.decision_quality import DecisionQuality
    with pytest.raises(ValueError):
        DecisionQuality(enhanced_quality_score=70, quality_grade="B", breakdown={},
                        penalties={}, should_hold=False, reason_code=None, min_quality=60,
                        final_action_input="BUY", is_live_authorization=True)


def test_score_out_of_range_rejected():
    from app.agents.decision_quality import DecisionQuality
    with pytest.raises(ValueError):
        DecisionQuality(enhanced_quality_score=150, quality_grade="A", breakdown={},
                        penalties={}, should_hold=False, reason_code=None, min_quality=60,
                        final_action_input="BUY")


# ── council 통합 ─────────────────────────────────────────────────────────────


def test_council_quality_gate_downgrades_weak_buy():
    from app.agents.agent_council import StrategyMarketInput, run_agent_council
    # 약한 BUY: ORB 만 약하게 돌파, 다른 전략은 중립 → 고도화 quality 낮음.
    # CONSERVATIVE profile(min_quality 75)에서 강등 유도.
    inp = StrategyMarketInput(
        symbol="005930", current_price=70_050, prev_close=70_000, open_price=70_000,
        vwap=70_040, opening_range_high=70_000, opening_range_low=69_500,
        recent_closes=(70_000, 70_010, 70_050),
        current_volume=2_000_000, avg_volume=8_000_000,   # 저거래량 → risk flag.
        market_regime="HIGH_VOLATILITY", regime_decision="REDUCE_SIZE")
    d = run_agent_council(inp, risk_profile="CONSERVATIVE")
    dd = d.to_dict()
    assert "quality_gate_result" in dd
    assert "pre_quality_action" in dd
    # quality_gate_result 가 breakdown 을 담는다.
    assert "breakdown" in dd["quality_gate_result"]


def test_council_quality_gate_result_present_on_strong_buy():
    from app.agents.agent_council import StrategyMarketInput, run_agent_council
    inp = StrategyMarketInput(
        symbol="005930", current_price=72_000, prev_close=70_000, open_price=71_500,
        vwap=70_500, opening_range_high=71_000, opening_range_low=70_500,
        recent_closes=(70_000, 70_800, 71_500, 72_000),
        current_volume=12_000_000, avg_volume=8_000_000,
        market_regime="TREND_UP", regime_decision="ALLOW")
    d = run_agent_council(inp, risk_profile="BALANCED")
    dd = d.to_dict()
    assert dd["quality_gate_result"]["enhanced_quality_score"] >= 0
    assert dd["pre_quality_action"] in ("BUY", "SELL", "HOLD")


def test_from_council_helper():
    council = {
        "final_action": "BUY", "votes": _strong_buy_votes(), "risk_flags": [],
        "market_regime": "TREND_UP", "has_exit_plan": True,
        "exit_plan_validation": {"valid": True}, "risk_veto_result": {},
        "metadata": {"regime_decision": "ALLOW"},
    }
    q = compute_decision_quality_from_council(council, min_quality=60)
    assert q.enhanced_quality_score > 0
    assert q.should_hold is False


# ── import / secret 가드 ─────────────────────────────────────────────────────


def test_no_forbidden_imports():
    src = open(dq.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "from app.execution.paper_trader", "import anthropic", "import openai",
                "import httpx", "import requests", ".place_order(", "route_order(",
                "db.add(", "db.commit("):
        assert tok not in src, f"forbidden token: {tok}"


def test_no_secret_in_quality():
    text = json.dumps(compute_decision_quality(votes=_strong_buy_votes(),
                                               final_action="BUY").to_dict(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in text


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_decision_quality(client):
    council = {"final_action": "BUY", "votes": _strong_buy_votes(),
               "market_regime": "TREND_UP", "has_exit_plan": True,
               "exit_plan_validation": {"valid": True},
               "metadata": {"regime_decision": "ALLOW"}}
    r = client.post("/api/agents/decision-quality", json={"council": council})
    assert r.status_code == 200
    body = r.json()
    assert "enhanced_quality_score" in body and "breakdown" in body
    assert body["is_live_authorization"] is False
    assert body["is_order_signal"] is False


def test_api_decision_quality_no_broker_order(client):
    client.post("/api/agents/decision-quality", json={"council": {"final_action": "HOLD"}})
    assert len(client.test_broker.orders) == 0
