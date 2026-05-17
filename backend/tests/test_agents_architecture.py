"""#51: Agent architecture contract + 6 role agents tests.

Coverage:
- `AgentBase` ABC + 6 roles enum + `AgentDecision` enum
- `AgentOutput.is_order_intent=True` 시 ValueError (dataclass 가드)
- `AgentOutput.can_execute_order=True` 시 ValueError (dataclass 가드)
- 6개 mock 전략 deterministic 동작:
  - ObserverAgent: OBSERVE + watchlist/signal counts in metadata
  - AnalystAgent: ANALYZE + confidence based on signal count
  - RiskAuditorAgent: WARN/REJECT/OBSERVE based on flags
  - StrategyResearcherAgent: REPORT/RECOMMEND based on backtest summary
  - ReportWriterAgent: REPORT with audit summary
  - ExecutionRecommenderAgent: APPROVAL_CANDIDATE + payload, NO_OP if no signals
- `build_default_registry()` returns all 6 roles
- 정적 가드: agents.base + agents.roles 모듈은 broker / OrderExecutor /
  route_order import 0건
- /api/agents/architecture, /catalog, /mock-run endpoints
- ExecutionRecommender의 approval_candidate.is_order_intent=False
"""

from __future__ import annotations

import pytest

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentOutput,
    AgentRole,
)
from app.agents.roles import (
    AnalystAgent,
    ExecutionRecommenderAgent,
    ObserverAgent,
    ReportWriterAgent,
    RiskAuditorAgent,
    StrategyResearcherAgent,
    build_default_registry,
)


# ====================================================================
# 1. AgentOutput dataclass invariants
# ====================================================================


def test_agent_output_rejects_is_order_intent_true():
    with pytest.raises(ValueError, match="is_order_intent"):
        AgentOutput(
            role=AgentRole.OBSERVER,
            decision=AgentDecision.OBSERVE,
            summary="x",
            is_order_intent=True,
        )


def test_agent_output_rejects_can_execute_order_true():
    with pytest.raises(ValueError, match="can_execute_order"):
        AgentOutput(
            role=AgentRole.EXECUTION_RECOMMENDER,
            decision=AgentDecision.APPROVAL_CANDIDATE,
            summary="x",
            can_execute_order=True,
        )


def test_agent_output_rejects_invalid_confidence():
    with pytest.raises(ValueError, match="confidence"):
        AgentOutput(
            role=AgentRole.ANALYST,
            decision=AgentDecision.ANALYZE,
            summary="x",
            confidence=150,
        )


def test_agent_output_default_is_advisory():
    out = AgentOutput(
        role=AgentRole.OBSERVER,
        decision=AgentDecision.OBSERVE,
        summary="x",
    )
    assert out.is_order_intent is False
    assert out.can_execute_order is False


def test_agent_output_to_dict_has_all_fields():
    out = AgentOutput(
        role=AgentRole.RISK_AUDITOR,
        decision=AgentDecision.WARN,
        summary="x",
        reasons=["r1"],
        risk_flags=["f1"],
        confidence=70,
    )
    d = out.to_dict()
    for key in ("role", "decision", "summary", "reasons", "confidence",
                "risk_flags", "approval_candidate", "metadata",
                "is_order_intent", "can_execute_order", "created_at"):
        assert key in d
    assert d["is_order_intent"] is False
    assert d["can_execute_order"] is False
    assert d["confidence"] == 70


# ====================================================================
# 2. AgentRole + AgentDecision enums
# ====================================================================


def test_agent_role_has_six_standard_values():
    expected = {
        "OBSERVER", "ANALYST", "RISK_AUDITOR",
        "STRATEGY_RESEARCHER", "REPORT_WRITER", "EXECUTION_RECOMMENDER",
    }
    assert {r.value for r in AgentRole} == expected


def test_agent_decision_has_expected_categories():
    # 8개: OBSERVE, ANALYZE, WARN, REJECT, REPORT, RECOMMEND,
    #      APPROVAL_CANDIDATE, NO_OP
    expected = {
        "OBSERVE", "ANALYZE", "WARN", "REJECT", "REPORT",
        "RECOMMEND", "APPROVAL_CANDIDATE", "NO_OP",
    }
    assert {d.value for d in AgentDecision} == expected


# ====================================================================
# 3. AgentBase ABC
# ====================================================================


def test_agent_base_is_abstract():
    with pytest.raises(TypeError):
        AgentBase()  # type: ignore[abstract]


def test_required_methods_are_declared_abstract():
    abstracts = set(getattr(AgentBase, "__abstractmethods__", set()))
    assert "metadata" in abstracts
    assert "run" in abstracts


# ====================================================================
# 4. ObserverAgent
# ====================================================================


def test_observer_emits_observe_with_counts():
    agent = ObserverAgent()
    out = agent.run(AgentContext(
        watchlist=["005930", "000660"],
        recent_signals=[{"strength": 50}],
    ))
    assert out.role == AgentRole.OBSERVER
    assert out.decision == AgentDecision.OBSERVE
    assert out.metadata["watchlist_size"] == 2
    assert out.metadata["signal_count"] == 1
    assert out.is_order_intent is False
    assert out.can_execute_order is False


def test_observer_handles_empty_context():
    agent = ObserverAgent()
    out = agent.run(AgentContext())
    assert out.decision == AgentDecision.OBSERVE
    assert out.metadata["watchlist_size"] == 0


# ====================================================================
# 5. AnalystAgent
# ====================================================================


def test_analyst_emits_analyze_with_confidence_scaling_with_signals():
    agent = AnalystAgent()
    no_signals = agent.run(AgentContext())
    many_signals = agent.run(AgentContext(
        recent_signals=[{"strength": 80}, {"strength": 75}, {"strength": 90}],
    ))
    assert no_signals.decision == AgentDecision.ANALYZE
    assert many_signals.decision == AgentDecision.ANALYZE
    assert many_signals.confidence > no_signals.confidence


def test_analyst_counts_high_strength_signals():
    agent = AnalystAgent()
    out = agent.run(AgentContext(recent_signals=[
        {"strength": 80}, {"strength": 50}, {"strength": 90},
    ]))
    assert out.metadata["notable_signal_count"] == 2  # 80, 90


# ====================================================================
# 6. RiskAuditorAgent
# ====================================================================


def test_risk_auditor_observes_when_no_flags():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext())
    assert out.decision == AgentDecision.OBSERVE
    assert out.risk_flags == []


def test_risk_auditor_rejects_on_emergency_stop():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext(risk_state={"emergency_stop": True}))
    assert out.decision == AgentDecision.REJECT
    assert "emergency_stop_active" in out.risk_flags


def test_risk_auditor_rejects_on_critical_daily_loss():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext(risk_state={"daily_loss_pct": 90}))
    assert out.decision == AgentDecision.REJECT
    assert "daily_loss_critical" in out.risk_flags


def test_risk_auditor_warns_on_elevated_daily_loss():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext(risk_state={"daily_loss_pct": 60}))
    assert out.decision == AgentDecision.WARN
    assert "daily_loss_elevated" in out.risk_flags


def test_risk_auditor_warns_on_stale_data():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext(audit_summary={"stale_price_rejections": 3}))
    assert out.decision == AgentDecision.WARN
    assert "stale_data_recent" in out.risk_flags


def test_risk_auditor_warns_on_duplicates():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext(audit_summary={"duplicate_rejections": 5}))
    assert out.decision == AgentDecision.WARN
    assert "duplicate_orders_recent" in out.risk_flags


# ====================================================================
# 7. StrategyResearcherAgent
# ====================================================================


def test_researcher_recommends_when_below_thresholds():
    agent = StrategyResearcherAgent()
    out = agent.run(AgentContext(extra={
        "backtest_summary": {"win_rate": 0.40, "profit_factor": 0.95},
    }))
    assert out.decision == AgentDecision.RECOMMEND


def test_researcher_reports_when_healthy():
    agent = StrategyResearcherAgent()
    out = agent.run(AgentContext(extra={
        "backtest_summary": {"win_rate": 0.55, "profit_factor": 1.5},
    }))
    assert out.decision == AgentDecision.REPORT


def test_researcher_recommends_on_empty_summary():
    """Empty summary → all metrics 0 → RECOMMEND (보수적 default)."""
    agent = StrategyResearcherAgent()
    out = agent.run(AgentContext())
    assert out.decision == AgentDecision.RECOMMEND


# ====================================================================
# 8. ReportWriterAgent
# ====================================================================


def test_report_writer_emits_report_with_counts():
    agent = ReportWriterAgent()
    out = agent.run(AgentContext(audit_summary={
        "total_orders": 12, "approved": 8, "rejected": 4,
    }))
    assert out.role == AgentRole.REPORT_WRITER
    assert out.decision == AgentDecision.REPORT
    assert out.metadata["total_orders"] == 12
    assert out.metadata["approved"] == 8
    assert out.metadata["rejected"] == 4


def test_report_writer_handles_empty_context():
    agent = ReportWriterAgent()
    out = agent.run(AgentContext())
    assert out.decision == AgentDecision.REPORT


# ====================================================================
# 9. ExecutionRecommenderAgent — direct order forbidden
# ====================================================================


def test_execution_recommender_emits_no_op_without_signals():
    agent = ExecutionRecommenderAgent()
    out = agent.run(AgentContext())
    assert out.decision == AgentDecision.NO_OP
    assert out.approval_candidate is None


def test_execution_recommender_emits_approval_candidate():
    agent = ExecutionRecommenderAgent()
    out = agent.run(AgentContext(recent_signals=[
        {"symbol": "005930", "side": "BUY", "strength": 80, "confidence": 75,
         "reasons": ["bull cross"]},
        {"symbol": "000660", "side": "BUY", "strength": 60, "confidence": 50},
    ]))
    assert out.role == AgentRole.EXECUTION_RECOMMENDER
    assert out.decision == AgentDecision.APPROVAL_CANDIDATE
    assert out.approval_candidate is not None
    # 가장 강한 신호(80) 채택.
    assert out.approval_candidate["symbol"] == "005930"
    assert out.approval_candidate["confidence"] == 75
    assert "bull cross" in out.approval_candidate["supporting_reasons"]


def test_execution_recommender_candidate_payload_marks_not_order_intent():
    """approval_candidate payload는 *주문 객체가 아님*을 명시."""
    agent = ExecutionRecommenderAgent()
    out = agent.run(AgentContext(recent_signals=[
        {"symbol": "005930", "side": "BUY", "strength": 90},
    ]))
    assert out.approval_candidate["is_order_intent"] is False
    # 본 invariant는 caller가 candidate를 *주문 객체*로 오해하지 않도록
    # 명시적 marker.


def test_execution_recommender_metadata_marks_no_execute():
    agent = ExecutionRecommenderAgent()
    md = agent.metadata
    assert md.role == AgentRole.EXECUTION_RECOMMENDER
    assert md.can_execute_order is False
    # forbidden 리스트에 "broker", "OrderExecutor", "route_order" 명시.
    forbidden_text = " ".join(md.forbidden)
    assert "broker" in forbidden_text
    assert "OrderExecutor" in forbidden_text or "route_order" in forbidden_text


def test_execution_recommender_output_can_execute_order_is_false():
    agent = ExecutionRecommenderAgent()
    out = agent.run(AgentContext(recent_signals=[
        {"symbol": "005930", "side": "BUY", "strength": 80},
    ]))
    assert out.can_execute_order is False


# ====================================================================
# 10. Registry
# ====================================================================


def test_registry_has_all_six_roles():
    reg = build_default_registry()
    assert set(reg.keys()) == set(AgentRole)
    for role, agent in reg.items():
        assert isinstance(agent, AgentBase)
        assert agent.metadata.role == role


def test_registry_all_agents_have_can_execute_order_false():
    reg = build_default_registry()
    for agent in reg.values():
        assert agent.metadata.can_execute_order is False


# ====================================================================
# 11. Static guards — no broker / executor / route_order imports
# ====================================================================


def test_agents_base_module_does_not_import_broker_or_executor():
    import app.agents.base as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "from app.permission.gate",
        "broker.place_order(",
        ".place_order(",
        ".cancel_order(",
        "route_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.agents.base must not contain '{snippet}' — "
            "agents are advisory only."
        )


def test_agents_roles_module_does_not_import_broker_or_executor():
    import app.agents.roles as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "from app.permission.gate",
        "broker.place_order(",
        ".place_order(",
        ".cancel_order(",
        "route_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.agents.roles must not contain '{snippet}'"
        )


# ====================================================================
# 12. AgentContext — no broker / api key fields
# ====================================================================


def test_agent_context_does_not_carry_broker_or_keys():
    """AgentContext가 broker 인스턴스 / API key / Secret 필드를 *받지 않음*을
    검증. 본 dataclass의 필드 이름이 명시적으로 advisory 메타만 carry."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(AgentContext)}
    forbidden = {"broker", "api_key", "secret", "kis_app_key",
                 "kis_app_secret", "anthropic_api_key", "account_no",
                 "executor", "order_executor", "route_order"}
    overlap = field_names & forbidden
    assert overlap == set(), (
        f"AgentContext must not carry forbidden fields: {overlap}"
    )


# ====================================================================
# 13. /api/agents/architecture, /catalog, /mock-run endpoints
# ====================================================================


def test_api_architecture_lists_six_roles(client):
    res = client.get("/api/agents/architecture")
    assert res.status_code == 200
    body = res.json()
    assert len(body["roles"]) == 6
    role_set = {r["role"] for r in body["roles"]}
    assert role_set == {r.value for r in AgentRole}
    for r in body["roles"]:
        assert r["can_execute_order"] is False


def test_api_architecture_includes_forbidden_invariants(client):
    res = client.get("/api/agents/architecture")
    body = res.json()
    forbidden_text = " ".join(body["forbidden_for_all"])
    assert "broker" in forbidden_text
    assert "OrderExecutor" in forbidden_text or "route_order" in forbidden_text


def test_api_catalog_returns_six_agents(client):
    res = client.get("/api/agents/catalog")
    assert res.status_code == 200
    catalog = res.json()
    assert len(catalog) == 6
    for entry in catalog:
        assert entry["can_execute_order"] is False


def test_api_mock_run_observer(client):
    res = client.post("/api/agents/mock-run", json={
        "role": "OBSERVER",
        "watchlist": ["005930"],
        "recent_signals": [{"strength": 50}],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "OBSERVER"
    assert body["decision"] == "OBSERVE"
    assert body["is_order_intent"] is False
    assert body["can_execute_order"] is False


def test_api_mock_run_execution_recommender_emits_candidate_payload(client):
    res = client.post("/api/agents/mock-run", json={
        "role": "EXECUTION_RECOMMENDER",
        "recent_signals": [
            {"symbol": "005930", "side": "BUY", "strength": 90,
             "confidence": 85},
        ],
    })
    body = res.json()
    assert body["decision"] == "APPROVAL_CANDIDATE"
    assert body["approval_candidate"] is not None
    assert body["approval_candidate"]["symbol"] == "005930"
    assert body["approval_candidate"]["is_order_intent"] is False
    assert body["can_execute_order"] is False


def test_api_mock_run_unknown_role_does_not_500(client):
    res = client.post("/api/agents/mock-run", json={"role": "BOGUS"})
    assert res.status_code == 200
    body = res.json()
    assert body["decision"] == "NO_OP"
    assert "unknown" in body["summary"].lower()


def test_api_mock_run_does_not_create_audit_or_orders(client):
    """mock-run은 read-only — DB / audit / approval row 변경 0건."""
    from sqlalchemy import select
    from app.db.models import OrderAuditLog, PendingApproval

    client.post("/api/agents/mock-run", json={
        "role": "EXECUTION_RECOMMENDER",
        "recent_signals": [
            {"symbol": "005930", "side": "BUY", "strength": 80},
        ],
    })
    with client.test_db_factory() as db:
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []
