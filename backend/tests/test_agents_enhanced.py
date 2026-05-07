"""Enhanced council members unit tests (224, MUST).

5개 신규 agent 모두 deterministic — AI Key 없이도 동일 출력 보장.
"""

from __future__ import annotations

from app.ai.agents.base import AgentDecision
from app.ai.agents.enhanced import (
    AgentCritic,
    OperatorBriefingAgent,
    ReadinessAgent,
    ScenarioStressAgent,
    StopLossGuardianAgent,
)


# ---------- AgentCritic ----------

def test_critic_flags_overconfidence_without_evidence() -> None:
    critic = AgentCritic()
    members = [
        AgentDecision(agent_name="X", decision="BUY", confidence=95,
                      reasons=["x"], meta={}),
    ]
    out = critic.decide(member_decisions=members)
    assert out.decision == "WARN"
    assert any("overconfident" in r for r in out.reasons)


def test_critic_flags_contradiction_buy_vs_sell() -> None:
    critic = AgentCritic()
    members = [
        AgentDecision(agent_name="A", decision="BUY", confidence=70, reasons=["r1", "r2"]),
        AgentDecision(agent_name="B", decision="SELL", confidence=70, reasons=["r1", "r2"]),
    ]
    out = critic.decide(member_decisions=members)
    assert out.decision == "WARN"
    assert any("contradiction" in r for r in out.reasons)


def test_critic_passes_clean_council() -> None:
    critic = AgentCritic()
    members = [
        AgentDecision(agent_name="A", decision="HOLD", confidence=60,
                      reasons=["r1", "r2"], meta={"k": "v"}),
    ]
    out = critic.decide(member_decisions=members)
    assert out.decision == "INFO"


def test_critic_handles_empty_input() -> None:
    out = AgentCritic().decide(member_decisions=None)
    assert out.decision == "INFO"
    assert out.confidence == 0


# ---------- ScenarioStressAgent ----------

def test_scenario_survives_small_position_neutral_news() -> None:
    a = ScenarioStressAgent()
    out = a.decide(notional=100_000, sentiment=50)
    assert out.meta["overall_verdict"] == "SURVIVES"
    assert out.meta["scenarios"]["crash"] == "PASS"


def test_scenario_fails_on_huge_notional() -> None:
    out = ScenarioStressAgent().decide(notional=3_000_000)
    assert out.meta["overall_verdict"] == "FAILS"
    assert out.meta["scenarios"]["slippage"] == "FAIL"


def test_scenario_fragile_when_one_warn() -> None:
    # high sentiment alone triggers news_flip:WARN. notional=400000 → others PASS.
    out = ScenarioStressAgent().decide(notional=400_000, sentiment=80)
    assert out.meta["overall_verdict"] == "SURVIVES"  # 1 WARN OK


def test_scenario_returns_score_in_bounds() -> None:
    out = ScenarioStressAgent().decide(notional=100_000)
    assert 0 <= out.meta["overall_score"] <= 100


# ---------- OperatorBriefingAgent ----------

def test_briefing_3_lines_with_chief_decision() -> None:
    chief = AgentDecision(agent_name="Chief", decision="BUY", confidence=70,
                          reasons=["chief:entry_buy"])
    out = OperatorBriefingAgent().decide(
        chief_decision=chief, readiness_label="READY", regime="trending_up",
    )
    assert len(out.reasons) == 3
    assert "BUY" in out.reasons[0]
    assert "trending_up" in out.reasons[1]
    assert "READY" in out.reasons[2]


def test_briefing_safe_default_when_no_decision() -> None:
    out = OperatorBriefingAgent().decide(chief_decision=None)
    assert len(out.reasons) == 3
    assert out.reasons[0] == "Agent 결정 없음"


# ---------- ReadinessAgent ----------

def test_readiness_blocked_on_emergency_stop() -> None:
    out = ReadinessAgent().decide(emergency_stop=True)
    assert out.meta["readiness_label"] == "BLOCKED"
    assert out.decision == "REJECT"


def test_readiness_blocked_on_risk_officer_reject() -> None:
    out = ReadinessAgent().decide(risk_officer_decision="REJECT")
    assert out.meta["readiness_label"] == "BLOCKED"


def test_readiness_blocked_on_scenario_fails() -> None:
    out = ReadinessAgent().decide(scenario_verdict="FAILS")
    assert out.meta["readiness_label"] == "BLOCKED"


def test_readiness_caution_on_high_vol() -> None:
    out = ReadinessAgent().decide(market_volatility_pct=6.0)
    assert out.meta["readiness_label"] == "CAUTION"


def test_readiness_caution_on_low_agreement() -> None:
    out = ReadinessAgent().decide(agent_agreement_score=40)
    assert out.meta["readiness_label"] == "CAUTION"


def test_readiness_ready_when_all_clear() -> None:
    out = ReadinessAgent().decide(
        emergency_stop=False, market_volatility_pct=1.0,
        agent_agreement_score=90, chief_confidence=70,
    )
    assert out.meta["readiness_label"] == "READY"
    assert out.decision == "INFO"


# ---------- StopLossGuardianAgent ----------

def test_guardian_liquidates_on_risk_spike() -> None:
    out = StopLossGuardianAgent().decide(risk_spike=True, unrealized_pct=0.0)
    assert out.decision == "SELL"
    assert out.meta["action"] == "LIQUIDATE"


def test_guardian_stop_loss_below_neg_1_5_pct() -> None:
    out = StopLossGuardianAgent().decide(unrealized_pct=-1.6)
    assert out.decision == "SELL"
    assert out.meta["action"] == "STOP_LOSS"


def test_guardian_trailing_after_peak_retracement() -> None:
    out = StopLossGuardianAgent().decide(unrealized_pct=2.0, peak_pct=3.5)
    assert out.decision == "SELL"
    assert out.meta["action"] == "TRAILING"


def test_guardian_time_exit_after_6h() -> None:
    out = StopLossGuardianAgent().decide(unrealized_pct=0.5, holding_minutes=400)
    assert out.decision == "SELL"
    assert out.meta["action"] == "TIME_EXIT"


def test_guardian_holds_when_safe() -> None:
    out = StopLossGuardianAgent().decide(
        unrealized_pct=0.5, peak_pct=0.5, holding_minutes=30,
    )
    assert out.decision == "HOLD"
    assert out.meta["action"] == "HOLD"


def test_guardian_priority_risk_spike_over_loss() -> None:
    """risk_spike이 다른 조건보다 우선되는지 확인."""
    out = StopLossGuardianAgent().decide(
        risk_spike=True, unrealized_pct=-3.0, holding_minutes=400,
    )
    assert out.meta["action"] == "LIQUIDATE"
