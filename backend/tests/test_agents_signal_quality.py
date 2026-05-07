"""Signal Quality Gate (226, MUST) — agent-aware scoring tests."""

from __future__ import annotations

from app.agents.signal_quality import evaluate_signal_quality


def test_high_quality_signal_recommended_approve() -> None:
    out = evaluate_signal_quality(
        signal_strength=80, regime_fit=90, agent_agreement=95,
        scenario_stress=80, exit_plan_quality=80, sizing_safety=90,
        data_freshness=100, duplicate_penalty=100,
    )
    assert out.quality_score >= 80
    assert out.quality_grade in ("A", "B")
    assert out.approval_recommendation == "APPROVE"
    assert out.rejection_reasons == []


def test_low_quality_signal_recommended_reject() -> None:
    out = evaluate_signal_quality(
        signal_strength=30, regime_fit=20, agent_agreement=10,
        scenario_stress=10, exit_plan_quality=10, sizing_safety=10,
        data_freshness=20, duplicate_penalty=10,
    )
    assert out.quality_score < 60
    assert out.approval_recommendation == "REJECT"
    assert "agent_agreement" in " ".join(out.rejection_reasons)


def test_borderline_score_marked_needs_review() -> None:
    """가중치 평균 ~70인 케이스 — 점수 임계값은 통과지만 일부 항목 부족."""
    out = evaluate_signal_quality(
        signal_strength=70, regime_fit=80, agent_agreement=70,
        scenario_stress=70, exit_plan_quality=70, sizing_safety=70,
        data_freshness=80, duplicate_penalty=80,
    )
    assert out.approval_recommendation == "NEEDS_REVIEW"


def test_score_clamped_to_0_100() -> None:
    over = evaluate_signal_quality(signal_strength=200)
    assert over.breakdown["signal_strength"] == 100
    under = evaluate_signal_quality(signal_strength=-50)
    assert under.breakdown["signal_strength"] == 0


def test_grade_mapping_a_through_f() -> None:
    """0,60,70,80,90 경계."""
    cases = [(95, "A"), (85, "B"), (75, "C"), (65, "D"), (50, "F")]
    for score, expected_grade in cases:
        # uniform input giving exactly this weighted score
        out = evaluate_signal_quality(
            signal_strength=score, regime_fit=score, agent_agreement=score,
            scenario_stress=score, exit_plan_quality=score, sizing_safety=score,
            data_freshness=score, duplicate_penalty=score,
        )
        assert out.quality_grade == expected_grade, f"score={score}: got {out.quality_grade}"


def test_rejection_reason_includes_threshold() -> None:
    """sizing_safety가 임계값 미만이면 명확한 사유 노출."""
    out = evaluate_signal_quality(
        signal_strength=80, regime_fit=80, agent_agreement=80,
        scenario_stress=80, exit_plan_quality=80, sizing_safety=20,
        data_freshness=80, duplicate_penalty=80,
    )
    assert any("sizing_safety<50" in r for r in out.rejection_reasons)


def test_operator_summary_three_lines() -> None:
    out = evaluate_signal_quality(
        signal_strength=70, regime_fit=70, agent_agreement=70,
        scenario_stress=70, exit_plan_quality=70, sizing_safety=70,
        data_freshness=70, duplicate_penalty=70,
    )
    assert len(out.operator_summary) == 3
    assert "C" in out.operator_summary[0] or "B" in out.operator_summary[0]


def test_min_required_score_threshold_blocks_borderline() -> None:
    out = evaluate_signal_quality(
        signal_strength=80, regime_fit=80, agent_agreement=80,
        scenario_stress=80, exit_plan_quality=80, sizing_safety=80,
        data_freshness=80, duplicate_penalty=80,
        min_required_score=85,
    )
    assert out.approval_recommendation == "REJECT"


def test_api_signal_quality_round_trip(client) -> None:
    res = client.post("/api/agents/signal-quality", json={
        "signal_strength": 80, "regime_fit": 90, "agent_agreement": 90,
        "scenario_stress": 80, "exit_plan_quality": 80, "sizing_safety": 90,
        "data_freshness": 100, "duplicate_penalty": 100,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["approval_recommendation"] == "APPROVE"
    assert body["quality_grade"] in ("A", "B")
    assert body["breakdown"]["agent_agreement"] == 90


def test_api_signal_quality_low_score_rejects(client) -> None:
    res = client.post("/api/agents/signal-quality", json={
        "agent_agreement": 10, "sizing_safety": 10,
    })
    body = res.json()
    assert body["approval_recommendation"] == "REJECT"
    assert any("agent_agreement" in r for r in body["rejection_reasons"])
