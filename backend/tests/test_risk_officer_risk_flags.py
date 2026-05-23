"""2-08: RiskOfficerAgent risk_flag veto 테스트.

risk_profile 별 허용치(CONSERVATIVE 0 / BALANCED 1 / AGGRESSIVE 2) 초과 시 BUY/SELL
→ HOLD 강등 + dedupe + pre_veto_action + risk_veto_result + council/episode 연결.
"""

from __future__ import annotations

import pytest

from app.agents.risk_officer import (
    MAX_RISK_FLAGS_BY_PROFILE,
    REASON_RISK_OFFICER_VETO,
    RiskVetoResult,
    aggregate_risk_flags,
    evaluate_risk_officer_veto,
    max_risk_flags_for,
)


def _flags(n):
    return [f"flag_{i}" for i in range(n)]


# ── 1~6. risk_profile × flag 개수 매트릭스 ──

@pytest.mark.parametrize("n,profile,expected", [
    (0, "CONSERVATIVE", "BUY"),
    (1, "CONSERVATIVE", "HOLD"),
    (1, "BALANCED", "BUY"),
    (2, "BALANCED", "HOLD"),
    (2, "AGGRESSIVE", "BUY"),
    (3, "AGGRESSIVE", "HOLD"),
])
def test_profile_flag_matrix(n, profile, expected):
    r = evaluate_risk_officer_veto(action="BUY", risk_flags=_flags(n), risk_profile=profile)
    assert r.final_action == expected
    assert r.veto_applied is (expected == "HOLD")


def test_aggressive_excess_flags_force_hold():
    # AGGRESSIVE 도 무제한 진입 불가 — 3개면 HOLD.
    r = evaluate_risk_officer_veto(action="BUY", risk_flags=_flags(3), risk_profile="AGGRESSIVE")
    assert r.final_action == "HOLD"
    assert r.veto_applied is True
    assert r.reason_code == REASON_RISK_OFFICER_VETO


def test_sell_also_vetoed():
    r = evaluate_risk_officer_veto(action="SELL", risk_flags=_flags(2), risk_profile="BALANCED")
    assert r.final_action == "HOLD"
    assert r.veto_applied is True


def test_hold_stays_hold():
    r = evaluate_risk_officer_veto(action="HOLD", risk_flags=_flags(5), risk_profile="CONSERVATIVE")
    assert r.final_action == "HOLD"
    assert r.veto_applied is False   # 이미 HOLD — 강등 아님.


# ── dedupe / 집계 ──

def test_dedupe():
    r = evaluate_risk_officer_veto(action="BUY", risk_flags=["x", "x", "y", "y", "y"],
                                   risk_profile="CONSERVATIVE")
    assert r.risk_flag_count == 2
    assert r.risk_flags == ["x", "y"]


def test_aggregate_risk_flags_multi_source():
    out = aggregate_risk_flags(["a", "b"], ["b", "c"], None, ["", "d"])
    assert out == ["a", "b", "c", "d"]


def test_max_risk_flags_lookup():
    assert max_risk_flags_for("CONSERVATIVE") == 0
    assert max_risk_flags_for("BALANCED") == 1
    assert max_risk_flags_for("AGGRESSIVE") == 2
    assert max_risk_flags_for("UNKNOWN") == 1   # 보수 default.


# ── pre_veto_action / result 필드 ──

def test_pre_veto_action_recorded():
    r = evaluate_risk_officer_veto(action="BUY", risk_flags=_flags(3), risk_profile="AGGRESSIVE")
    assert r.pre_veto_action == "BUY"
    assert r.final_action == "HOLD"


def test_result_dict_shape():
    d = evaluate_risk_officer_veto(action="BUY", risk_flags=_flags(2),
                                   risk_profile="BALANCED").to_dict()
    for k in ("veto_applied", "pre_veto_action", "final_action", "risk_flags",
              "risk_flag_count", "max_risk_flags", "risk_profile", "reason_code"):
        assert k in d
    assert d["is_order_signal"] is False
    assert d["is_live_authorization"] is False


def test_invariant_guard():
    with pytest.raises(ValueError):
        RiskVetoResult(veto_applied=False, pre_veto_action="BUY", final_action="BUY",
                       risk_flags=[], risk_flag_count=0, max_risk_flags=1,
                       risk_profile="BALANCED", is_order_signal=True)
    with pytest.raises(ValueError):
        RiskVetoResult(veto_applied=False, pre_veto_action="BUY", final_action="BUY",
                       risk_flags=[], risk_flag_count=0, max_risk_flags=1,
                       risk_profile="BALANCED", is_live_authorization=True)


def test_explicit_max_overrides_profile():
    # max_risk_flags 명시 시 profile 무시.
    r = evaluate_risk_officer_veto(action="BUY", risk_flags=_flags(3),
                                   risk_profile="CONSERVATIVE", max_risk_flags=5)
    assert r.final_action == "BUY"
    assert r.veto_applied is False


def test_deterministic():
    kw = dict(action="BUY", risk_flags=_flags(2), risk_profile="BALANCED")
    assert evaluate_risk_officer_veto(**kw).to_dict() == evaluate_risk_officer_veto(**kw).to_dict()


# ── Agent Council 통합 ──

def _strong_buy_input(**kw):
    from app.agents.agent_council import StrategyMarketInput
    base = dict(
        symbol="005930", current_price=1100, prev_close=1000, open_price=1010,
        vwap=1000, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 1030, 1060, 1090, 1100),
        current_volume=120.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def test_council_thresholds_match_risk_officer():
    # agent_council 의 _PROFILE_THRESHOLDS.max_risk_flags 와 RiskOfficer 기준 일치.
    from app.agents.agent_council import _PROFILE_THRESHOLDS
    from app.agents.risk_profile import RiskProfile
    assert _PROFILE_THRESHOLDS[RiskProfile.CONSERVATIVE]["max_risk_flags"] == \
        MAX_RISK_FLAGS_BY_PROFILE["CONSERVATIVE"]
    assert _PROFILE_THRESHOLDS[RiskProfile.BALANCED]["max_risk_flags"] == \
        MAX_RISK_FLAGS_BY_PROFILE["BALANCED"]
    assert _PROFILE_THRESHOLDS[RiskProfile.AGGRESSIVE]["max_risk_flags"] == \
        MAX_RISK_FLAGS_BY_PROFILE["AGGRESSIVE"]


def test_council_aggressive_2flags_buy_balanced_hold():
    from app.agents.agent_council import CouncilAction, run_agent_council
    # low_volume + high_volatility = 2 flags.
    inp = _strong_buy_input(current_volume=5.0, avg_volume=100.0,
                            market_regime="HIGH_VOLATILITY")
    aggr = run_agent_council(inp, risk_profile="AGGRESSIVE")
    bal = run_agent_council(inp, risk_profile="BALANCED")
    cons = run_agent_council(inp, risk_profile="CONSERVATIVE")
    # AGGRESSIVE max 2 — 2 flags 는 경계(초과 아님) → veto 미적용.
    assert aggr.risk_veto_result["risk_flag_count"] == 2
    assert aggr.risk_veto_result["veto_applied"] is False
    # BALANCED max 1, CONSERVATIVE max 0 — 2 flags → veto → HOLD.
    assert bal.final_action == CouncilAction.HOLD
    assert bal.risk_veto_result["veto_applied"] is True
    assert cons.final_action == CouncilAction.HOLD


def test_council_carries_risk_veto_result_in_to_dict():
    from app.agents.agent_council import run_agent_council
    d = run_agent_council(_strong_buy_input()).to_dict()
    assert "risk_veto_result" in d
    assert d["risk_veto_result"]["max_risk_flags"] is not None
    assert d["threshold_snapshot"]["max_risk_flags"] is not None


def test_council_veto_downgrades_buy_to_hold_with_reason():
    from app.agents.agent_council import CouncilAction, run_agent_council
    inp = _strong_buy_input(current_volume=5.0, avg_volume=100.0,
                            market_regime="HIGH_VOLATILITY")
    bal = run_agent_council(inp, risk_profile="BALANCED")
    assert bal.final_action == CouncilAction.HOLD
    assert bal.risk_veto_result["pre_veto_action"] == "BUY"
    assert "RiskOfficer veto" in bal.reason
    assert bal.is_live_authorization is False


def test_council_no_veto_when_no_flags():
    from app.agents.agent_council import run_agent_council
    d = run_agent_council(_strong_buy_input(), risk_profile="AGGRESSIVE")
    assert d.risk_veto_result["veto_applied"] is False
