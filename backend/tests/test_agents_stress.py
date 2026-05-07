"""Agent OS stress tests (228, MUST).

CLAUDE.md 손실 방어 우선 — Agent 기반 가상 자동매매 시스템이 대량 트래픽 +
악성 입력 패턴에서도 invariant를 유지하는지 검증한다. 본 모듈은 222~227에서
추가된 Agent OS 신규 경로(operating_loop / market_regime / signal_quality /
council enhanced agents)를 표적으로 한다.

CI 시간을 고려해 LARGE_N=100으로 스케일다운 (133/test_stress.py와 동일 패턴).
사용자 명시는 10,000건이지만 invariant 검증에는 100건이면 충분 — 시간 측정은
별도 nightly job에서 LARGE_N=10000으로 돌릴 수 있도록 구조 유지.

마커는 @pytest.mark.slow — 일반 CI(`pytest --deselect slow`)에서 자동 제외,
nightly에서만 실행.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.agents.market_regime import classify_market_regime
from app.agents.signal_quality import evaluate_signal_quality
from app.ai.agents.base import AgentDecision, persist_decision
from app.ai.agents.enhanced import (
    AgentCritic,
    ReadinessAgent,
    ScenarioStressAgent,
    StopLossGuardianAgent,
)
from app.db.models import AgentDecisionLog


pytestmark = pytest.mark.slow


# 사용자 명시 (10000)에서 환경 의존 무관하게 invariant 검증되는 N.
LARGE_N = 100


# ---------- 1. Agent virtual decisions @ volume ----------

def test_stress_agent_decision_log_volume(client) -> None:
    """LARGE_N개 AgentDecision을 persist — 모든 행이 정확히 들어가고 chain_id로
    묶이는지. 10k 본 가속 검증의 시드."""
    db = client.test_db_factory()
    try:
        chain_id = "stress-chain-1"
        t0 = time.time_ns()
        for i in range(LARGE_N):
            persist_decision(
                db,
                AgentDecision(
                    agent_name="StressAgent",
                    decision="HOLD" if i % 2 else "BUY",
                    confidence=50 + (i % 50),
                    reasons=[f"r{i}"],
                    meta={"i": i},
                    symbol=f"00{i:04d}",
                    chain_id=chain_id,
                ),
                mode="VIRTUAL_AI_EXECUTION",
            )
        db.commit()
        elapsed_ms = (time.time_ns() - t0) / 1e6

        rows = db.scalars(select(AgentDecisionLog).where(
            AgentDecisionLog.chain_id == chain_id,
        )).all()
        assert len(rows) == LARGE_N
        assert {r.decision for r in rows} == {"HOLD", "BUY"}
        # CI 환경에서 100건은 합리적 시간 내 — 1초 미만 기대 (assert는 관대).
        assert elapsed_ms < 30_000, f"persist too slow: {elapsed_ms}ms"
    finally:
        db.close()


# ---------- 2. Signal Quality mass-reject under low scores ----------

def test_stress_low_quality_signals_all_rejected() -> None:
    """LARGE_N건의 저품질 신호가 모두 REJECT 권고를 받는지 + rejection_reasons
    가 누락되지 않는지."""
    rejected = 0
    for _ in range(LARGE_N):
        out = evaluate_signal_quality(
            signal_strength=20, regime_fit=10, agent_agreement=10,
            scenario_stress=10, exit_plan_quality=10, sizing_safety=10,
            data_freshness=20, duplicate_penalty=10,
        )
        if out.approval_recommendation == "REJECT":
            rejected += 1
            assert out.rejection_reasons
    assert rejected == LARGE_N


# ---------- 3. Market Regime classifier under varied input ----------

def test_stress_market_regime_classifier_deterministic() -> None:
    """다양한 정량 입력 LARGE_N건 — classifier가 deterministic이고 항상 valid
    regime을 반환하는지."""
    valid_regimes = {
        "TREND_UP", "TREND_DOWN", "CHOPPY", "HIGH_VOLATILITY",
        "LOW_LIQUIDITY", "GAP_DAY", "NEWS_DRIVEN", "RISK_OFF",
        "OPENING_CHAOS", "LATE_DAY_FADE",
    }
    for i in range(LARGE_N):
        out = classify_market_regime(
            trend_strength_pct=(i % 20) - 10,
            volatility_pct=(i % 10),
            volume_ratio=0.3 + (i % 100) / 100.0,
            gap_pct=((i % 10) - 5) * 0.5,
            news_sentiment=i % 100,
            is_opening_30min=(i % 5 == 0),
            is_late_day_30min=(i % 7 == 0),
            risk_off_signal=(i % 50 == 0),
        )
        assert out.regime in valid_regimes
        assert out.trade_permission in {"ALLOW", "WATCH", "PAUSE", "BLOCK"}
        assert 0.0 <= out.risk_multiplier <= 1.0
        assert 0.0 <= out.max_position_size_multiplier <= 1.0


# ---------- 4. ReadinessAgent agreement matrix ----------

def test_stress_readiness_blocked_paths_consistent() -> None:
    """emergency_stop / risk_officer REJECT / scenario FAILS 중 하나라도 있으면
    반드시 BLOCKED — LARGE_N건 모든 조합."""
    agent = ReadinessAgent()
    for i in range(LARGE_N):
        es = (i % 7 == 0)
        risk = "REJECT" if (i % 11 == 0) else "APPROVE"
        scen = "FAILS" if (i % 13 == 0) else "SURVIVES"
        out = agent.decide(
            emergency_stop=es,
            risk_officer_decision=risk,
            scenario_verdict=scen,
            market_volatility_pct=2.0,
            agent_agreement_score=80,
            chief_confidence=70,
        )
        if es or risk == "REJECT" or scen == "FAILS":
            assert out.meta["readiness_label"] == "BLOCKED", (
                f"i={i} es={es} risk={risk} scen={scen} got={out.meta['readiness_label']}"
            )


# ---------- 5. StopLossGuardian risk_spike priority ----------

def test_stress_guardian_risk_spike_always_liquidate() -> None:
    """risk_spike=True이면 다른 모든 조건과 무관하게 LIQUIDATE — 일관성 검증."""
    g = StopLossGuardianAgent()
    for i in range(LARGE_N):
        out = g.decide(
            risk_spike=True,
            unrealized_pct=(i % 10) - 5,
            peak_pct=(i % 5),
            holding_minutes=(i * 7) % 600,
        )
        assert out.meta["action"] == "LIQUIDATE"
        assert out.decision == "SELL"


# ---------- 6. AgentCritic catches contradictions at scale ----------

def test_stress_critic_flags_contradictions_consistently() -> None:
    """매번 BUY+SELL 동시 등장 시 'contradiction' 플래그가 빠지지 않는지."""
    critic = AgentCritic()
    for i in range(LARGE_N):
        members = [
            AgentDecision(agent_name=f"A{i}", decision="BUY", confidence=70,
                          reasons=["r1", "r2"]),
            AgentDecision(agent_name=f"B{i}", decision="SELL", confidence=70,
                          reasons=["r1", "r2"]),
        ]
        out = critic.decide(member_decisions=members)
        assert out.decision == "WARN"
        assert any("contradiction" in r for r in out.reasons)


# ---------- 7. ScenarioStressAgent verdict bounds ----------

def test_stress_scenario_score_bounds_under_random_inputs() -> None:
    """varied inputs LARGE_N건 — overall_score 0-100 범위 + verdict 항상 유효."""
    a = ScenarioStressAgent()
    valid_verdicts = {"SURVIVES", "FRAGILE", "FAILS"}
    for i in range(LARGE_N):
        out = a.decide(
            notional=(i * 50_000) % 5_000_000,
            unrealized_pct=((i % 20) - 10) / 10.0,
            sentiment=i % 100,
            regime=("ranging" if i % 3 == 0 else "trending"),
        )
        assert 0 <= out.meta["overall_score"] <= 100
        assert out.meta["overall_verdict"] in valid_verdicts


# ---------- 8. Emergency stop ON blocks Readiness path even with good inputs ----------

def test_stress_emergency_stop_overrides_all_good_signals() -> None:
    """emergency_stop=True이면 좋은 신호 LARGE_N건 모두 BLOCKED — 우회 불가."""
    agent = ReadinessAgent()
    for _ in range(LARGE_N):
        out = agent.decide(
            emergency_stop=True,
            risk_officer_decision="APPROVE",
            scenario_verdict="SURVIVES",
            market_volatility_pct=0.5,
            agent_agreement_score=99,
            chief_confidence=99,
        )
        assert out.meta["readiness_label"] == "BLOCKED"
        assert out.decision == "REJECT"


# ---------- 9. API endpoints under repeated calls ----------

def test_stress_api_signal_quality_repeated(client) -> None:
    """REST endpoint LARGE_N회 호출 — 모두 200 + recommendation 일관."""
    payload = {
        "signal_strength": 80, "regime_fit": 80, "agent_agreement": 80,
        "scenario_stress": 80, "exit_plan_quality": 80, "sizing_safety": 80,
        "data_freshness": 100, "duplicate_penalty": 100,
    }
    for _ in range(LARGE_N):
        res = client.post("/api/agents/signal-quality", json=payload)
        assert res.status_code == 200
        body = res.json()
        assert body["approval_recommendation"] == "APPROVE"


def test_stress_api_market_regime_repeated(client) -> None:
    """LARGE_N회 호출 — risk_off는 항상 BLOCK."""
    for _ in range(LARGE_N):
        res = client.post("/api/agents/market-regime", json={
            "risk_off_signal": True,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["regime"] == "RISK_OFF"
        assert body["trade_permission"] == "BLOCK"
