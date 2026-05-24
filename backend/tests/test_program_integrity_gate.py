"""BUILD-01: 전체 프로그램 정합성 점검 테스트.

핵심 invariant:
- 16개 섹션(Universe~Live safety) 모두 점검, 각 PASS/WARN/FAIL.
- BUY 흐름 연결: votes → council → exit_plan → quality → decision → KIS paper
  decision(broker_order_type=KIS_PAPER) → fake order → order_quality → portfolio.
- HOLD 면 KIS Paper decision/order 없음.
- feedback auto_apply_allowed=False + 다음 quality 반영.
- Live safety PASS + build_ready 산출.
- is_live_authorization/broker_order_sent/order_created_live=False, secret 0건,
  broker/OrderExecutor/route_order import·호출 0건.
"""

from __future__ import annotations

import json

import pytest

from app.system import program_integrity_gate as pig
from app.system.program_integrity_gate import (
    EXPECTED_API_ENDPOINTS,
    GateInputs,
    GateSection,
    GateVerdict,
    ProgramIntegrityReport,
    render_markdown_report,
    run_program_integrity_gate,
    summarize_program_integrity,
)


@pytest.fixture(scope="module")
def report() -> ProgramIntegrityReport:
    return run_program_integrity_gate()


def _sec(report, name):
    return next((s for s in report.sections if s.section == name), None)


# ── 섹션 존재 + 흐름 연결 ────────────────────────────────────────────────────


def test_all_sections_present(report):
    names = {s.section for s in report.sections}
    for sec in GateSection:
        assert sec.value in names, f"missing section {sec.value}"


def test_universe_section_pass(report):
    s = _sec(report, "UNIVERSE")
    assert s.verdict == GateVerdict.PASS.value


def test_kis_readiness_pass_or_warn(report):
    s = _sec(report, "KIS_PAPER_READINESS")
    # 빌드 환경 자격 미설정 → WARN 허용 (FAIL 아님).
    assert s.verdict in (GateVerdict.PASS.value, GateVerdict.WARN.value)


def test_strategy_votes_pass(report):
    assert _sec(report, "STRATEGY_VOTES").verdict == GateVerdict.PASS.value


def test_agent_council_pass(report):
    s = _sec(report, "AGENT_COUNCIL")
    assert s.verdict == GateVerdict.PASS.value
    assert s.evidence.get("final_action") in ("BUY", "SELL", "HOLD")


def test_risk_officer_pass(report):
    assert _sec(report, "RISK_OFFICER").verdict == GateVerdict.PASS.value


def test_exit_plan_pass(report):
    assert _sec(report, "EXIT_PLAN").verdict == GateVerdict.PASS.value


def test_quality_gate_pass(report):
    s = _sec(report, "QUALITY_GATE")
    assert s.verdict == GateVerdict.PASS.value
    assert "enhanced_quality_score" in s.evidence.get("quality_gate_result", {})


def test_decision_pass(report):
    assert _sec(report, "DECISION").verdict == GateVerdict.PASS.value


def test_kis_paper_decision_paper_type(report):
    s = _sec(report, "KIS_PAPER_DECISION")
    assert s.verdict == GateVerdict.PASS.value
    bot = s.evidence.get("broker_order_type")
    # BUY/SELL → KIS_PAPER (never LIVE); HOLD → None.
    assert bot in ("KIS_PAPER", None)
    assert bot != "KIS_LIVE"


def test_order_result_fake_paper(report):
    s = _sec(report, "ORDER_RESULT")
    assert s.verdict == GateVerdict.PASS.value
    if s.evidence.get("broker_order_type"):
        assert s.evidence["broker_order_type"] == "KIS_PAPER"
        assert s.evidence.get("is_live_authorization") is False


def test_order_quality_pass(report):
    assert _sec(report, "ORDER_QUALITY").verdict in (GateVerdict.PASS.value, GateVerdict.WARN.value)


def test_portfolio_pass(report):
    s = _sec(report, "PORTFOLIO")
    assert s.verdict == GateVerdict.PASS.value
    assert s.evidence.get("source") == "PAPER_SIMULATED"


def test_outcome_review_pass(report):
    assert _sec(report, "OUTCOME_REVIEW").verdict in (GateVerdict.PASS.value, GateVerdict.WARN.value)


def test_feedback_quality_pass(report):
    s = _sec(report, "FEEDBACK_QUALITY")
    assert s.verdict == GateVerdict.PASS.value
    assert "feedback_penalty" in s.evidence


def test_ui_api_section(report):
    s = _sec(report, "UI_API")
    assert s.verdict in (GateVerdict.PASS.value, GateVerdict.WARN.value)


def test_live_safety_pass(report):
    s = _sec(report, "LIVE_SAFETY")
    assert s.verdict == GateVerdict.PASS.value
    assert s.evidence.get("enable_live_trading") is False
    assert s.evidence.get("kis_is_paper") is True
    assert s.evidence.get("live_path_gated") is True


# ── build_ready ──────────────────────────────────────────────────────────────


def test_build_ready_default(report):
    # 기본(안전) 입력 → FAIL 0 → build_ready True.
    assert report.counts.get("FAIL", 0) == 0
    assert report.build_ready is True
    assert report.overall_verdict in (GateVerdict.PASS.value, GateVerdict.WARN.value)


def test_live_unsafe_blocks_build():
    rep = run_program_integrity_gate(GateInputs(enable_live_trading=True, kis_is_paper=False))
    ls = _sec(rep, "LIVE_SAFETY")
    assert ls.verdict == GateVerdict.FAIL.value
    assert rep.build_ready is False
    assert rep.reason_code == "BUILD_BLOCKED_BY_FAIL"


# ── HOLD 흐름 (약한 입력 → KIS decision/order 없음) ──────────────────────────


def test_hold_flow_no_order(monkeypatch):
    # council 을 HOLD 로 강제해 KIS decision/order 없음 확인.
    import app.system.program_integrity_gate as g

    class _HoldDecision:
        from app.agents.agent_council import CouncilAction as _CA
        final_action = _CA.HOLD

        def to_dict(self):
            return {"final_action": "HOLD", "confidence": 0.3, "quality_score": 40,
                    "selected_strategies": [], "buy_score": 0, "sell_score": 0,
                    "hold_score": 90, "risk_veto_result": {}, "risk_flags": [],
                    "has_exit_plan": False, "exit_plan": {}, "exit_plan_validation": {},
                    "quality_gate_result": {"enhanced_quality_score": 40, "quality_grade": "F",
                                            "should_hold": True}, "pre_quality_action": "HOLD"}

    def _fake_council(inp, mi):
        return [g._r(GateSection.AGENT_COUNCIL, GateVerdict.PASS, "COUNCIL_OK", "HOLD",
                     final_action="HOLD", confidence=0.3, quality_score=40,
                     buy_score=0, sell_score=0, hold_score=90),
                g._r(GateSection.RISK_OFFICER, GateVerdict.PASS, "RISK_OFFICER_OK", "ok"),
                g._r(GateSection.EXIT_PLAN, GateVerdict.PASS, "EXIT_PLAN_NA", "HOLD"),
                g._r(GateSection.QUALITY_GATE, GateVerdict.PASS, "QUALITY_GATE_OK", "ok",
                     quality_gate_result={"enhanced_quality_score": 40}),
                g._r(GateSection.DECISION, GateVerdict.PASS, "DECISION_OK", "HOLD"),
                g._r(GateSection.KIS_PAPER_DECISION, GateVerdict.PASS, "HOLD_NO_DECISION",
                     "HOLD", broker_order_type=None)], _HoldDecision()

    monkeypatch.setattr(g, "_check_council_and_downstream", _fake_council)
    rep = run_program_integrity_gate()
    order = _sec(rep, "ORDER_RESULT")
    assert order.reason_code == "NO_ORDER_FOR_HOLD"
    assert order.evidence.get("broker_order_type") is None


# ── invariants / secret / import 가드 ────────────────────────────────────────


def test_report_invariants(report):
    assert report.is_live_authorization is False
    assert report.broker_order_sent is False
    assert report.order_created_live is False
    assert report.contains_secret is False


def test_report_invariant_enforced():
    with pytest.raises(ValueError):
        ProgramIntegrityReport(
            generated_at="x", sections=(), counts={}, overall_verdict="PASS",
            build_ready=True, reason_code="BUILD_READY", is_live_authorization=True)


def test_no_secret_in_report(report):
    text = json.dumps(summarize_program_integrity(report), ensure_ascii=False)
    text += render_markdown_report(report)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "수익 보장", "실전 전환 승인", "is_live_authorization\": true"):
        assert forbidden not in text


def test_no_forbidden_imports():
    src = open(pig.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "from app.execution.order_executor", "from app.execution.paper_trader",
                "import anthropic", "import openai", "import httpx", "import requests",
                ".place_order(", "route_order(", "db.add(", "db.commit("):
        assert tok not in src, f"forbidden token: {tok}"


def test_expected_endpoints_manifest():
    # 표시 대상 endpoint manifest 가 핵심 흐름 endpoint 를 포함.
    for e in ("/api/auto-paper/universe-status", "/api/agents/feedback-loop",
              "/api/agents/decision-quality", "/api/status/live-safety"):
        assert e in EXPECTED_API_ENDPOINTS


def test_markdown_required_sections(report):
    md = render_markdown_report(report)
    for section in ("전체 요약", "build_ready", "섹션별 점검", "FAIL / WARN 조치",
                    "최종 빌드 가능 여부", "실전 승인이 아니며", "수익을 보장하지 않습니다"):
        assert section in md


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_program_integrity(client):
    r = client.get("/api/system/program-integrity")
    assert r.status_code == 200
    body = r.json()
    assert "sections" in body and len(body["sections"]) >= 16
    assert body["is_live_authorization"] is False
    assert body["broker_order_sent"] is False
    assert "build_ready" in body


def test_api_no_broker_order(client):
    client.get("/api/system/program-integrity")
    assert len(client.test_broker.orders) == 0


def test_api_ui_api_section_sees_real_routes(client):
    body = client.get("/api/system/program-integrity").json()
    ui = next((s for s in body["sections"] if s["section"] == "UI_API"), None)
    # 실제 app route 주입 → live-safety endpoint 가 존재해야 PASS/WARN.
    assert ui is not None
    assert ui["verdict"] in ("PASS", "WARN")
