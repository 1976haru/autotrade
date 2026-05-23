"""2-12: AgentDecisionLog 에 vote 상세/판단 근거 meta 저장 테스트.

build_agent_decision_log_meta 표준 구조(votes/selected_strategies/risk_profile/
reason_code/confidence/quality_score/broker_order_type/final_action/risk_flags/
risk_veto_result/exit_plan_validation/sell_reason_code/order_created) + secret-safe
+ decision_episode 핵심값 일치 + 안전 invariant.
"""

from __future__ import annotations

import json

import pytest

from app.agents.agent_council import StrategyMarketInput, run_agent_council
from app.agents.agent_memory import SecretLeakError
from app.agents.decision_log_meta import build_agent_decision_log_meta


def _strong(**kw):
    base = dict(
        symbol="005930", current_price=1100, prev_close=1000, open_price=1010,
        vwap=1000, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 1030, 1060, 1090, 1100),
        current_volume=150.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _buy_decision():
    return run_agent_council(_strong(), risk_profile="AGGRESSIVE").to_kis_paper_decision(
        quantity=10, price=1100)


# ── meta 표준 구조 ──

def test_meta_has_all_standard_keys():
    m = build_agent_decision_log_meta(
        decision=_buy_decision(), reason_code="KIS_PAPER_SUBMITTED",
        broker_order_no="P1", submitted=True, order_created=True, episode_id="ep-1")
    for k in ("votes", "selected_strategies", "risk_profile", "reason_code",
              "confidence", "quality_score", "broker_order_type", "final_action",
              "risk_flags", "risk_veto_result", "exit_plan_validation",
              "sell_reason_code", "order_created", "broker_order_sent",
              "is_live_authorization", "contains_secret", "episode_id", "audit_id"):
        assert k in m, f"missing meta key: {k}"


def test_votes_stored_with_4_strategies():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X")
    strategies = {v.get("strategy") for v in m["votes"]}
    assert {"ORB", "MOMENTUM", "GAP", "VWAP"}.issubset(strategies)


def test_selected_strategies_risk_profile_confidence_quality():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X")
    assert m["selected_strategies"]
    assert m["risk_profile"] == "AGGRESSIVE"
    assert 0.0 <= m["confidence"] <= 1.0
    assert m["quality_score"] >= 0


def test_broker_order_type_and_final_action():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X")
    assert m["broker_order_type"] == "KIS_PAPER"
    assert m["final_action"] == "BUY"


def test_risk_flags_and_veto_and_exit_validation():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X")
    assert isinstance(m["risk_flags"], list)
    assert "veto_applied" in m["risk_veto_result"]
    assert m["exit_plan_validation"].get("valid") is True


def test_order_created_flag():
    m_yes = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X",
                                          submitted=True, order_created=True)
    m_no = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X",
                                         submitted=False, order_created=False)
    assert m_yes["order_created"] is True
    assert m_no["order_created"] is False


def test_sell_reason_code_stored():
    inp = _strong(current_price=900, prev_close=1000, open_price=1000, vwap=1000,
                  recent_closes=(1000, 980, 960, 940, 900), market_regime="TREND_DOWN")
    council = run_agent_council(inp, held_position=True, risk_profile="AGGRESSIVE")
    if council.final_action.value != "SELL":
        pytest.skip("council did not produce SELL")
    kd = council.to_kis_paper_decision(quantity=5, price=900)
    m = build_agent_decision_log_meta(decision=kd, reason_code="KIS_PAPER_SUBMITTED")
    assert m["final_action"] == "SELL"
    assert m["sell_reason_code"]


# ── HOLD 판단도 근거 저장 ──

def test_hold_meta_preserves_basis():
    from app.kis_paper.auto_executor import KisPaperAutoDecision
    # driver_bridge 가 council carry 를 채운 HOLD decision 가정.
    hold = KisPaperAutoDecision(
        symbol="005930", side="HOLD", quantity=0, price=1000,
        selected_strategies=[], confidence=0.4, quality_score=30,
        risk_profile="CONSERVATIVE", risk_flags=["high_volatility"],
        votes=[{"strategy": s, "signal": "HOLD", "score": 30} for s in
               ("ORB", "MOMENTUM", "GAP", "VWAP")],
    )
    m = build_agent_decision_log_meta(decision=hold, reason_code="NO_STRATEGY_SIGNAL",
                                      order_created=False)
    assert m["final_action"] == "HOLD"
    assert len(m["votes"]) == 4
    assert m["risk_profile"] == "CONSERVATIVE"
    assert m["order_created"] is False


# ── 안전 invariant / secret ──

def test_invariants_forced():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X",
                                      broker_order_sent=True)
    # broker_order_sent 입력이 True 여도 is_live_authorization 은 강제 False.
    assert m["is_live_authorization"] is False
    assert m["contains_secret"] is False
    assert m["broker_order_type"] == "KIS_PAPER"


def test_secret_in_extra_raises():
    with pytest.raises(SecretLeakError):
        build_agent_decision_log_meta(
            decision=_buy_decision(), reason_code="X",
            extra={"note": "sk-ant-" + "a" * 40})


def test_no_secret_keys_in_output():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X")
    flat = json.dumps(m).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in flat


def test_json_serializable():
    m = build_agent_decision_log_meta(decision=_buy_decision(), reason_code="X")
    assert json.loads(json.dumps(m))  # round-trips.


def test_none_decision_safe():
    m = build_agent_decision_log_meta(decision=None, reason_code="X")
    assert m["final_action"] == "HOLD"
    assert m["votes"] == []
    assert m["broker_order_type"] == "KIS_PAPER"
    assert m["is_live_authorization"] is False


# ── decision_episode 핵심값 일치 ──

def test_meta_matches_council_episode_values():
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    council_dict = d.to_dict()                      # episode.council 에 저장되는 값.
    kd = d.to_kis_paper_decision(quantity=10, price=1100)
    m = build_agent_decision_log_meta(decision=kd, reason_code="X")
    # 핵심값이 episode(council) 와 일치.
    assert m["selected_strategies"] == council_dict["selected_strategies"]
    assert m["risk_profile"] == council_dict["risk_profile"]
    assert m["quality_score"] == council_dict["quality_score"]
    assert m["final_action"] == council_dict["final_action"]
    assert len(m["votes"]) == len(council_dict["votes"])
