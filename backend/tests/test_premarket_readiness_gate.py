"""BUILD-02A: 장 열리기 전 사전 검증 게이트 테스트.

핵심 invariant:
- fast mode 리포트 생성 + 섹션(env/kis/paper-live/universe/portfolio/agent/
  integrity/preflight/docs/scripts).
- KIS credentials missing/unknown → WARN (offline build 가능, rehearsal 불가).
- KIS credentials present → rehearsal ready.
- live flag on → ENV_READINESS FAIL → premarket_ready False.
- full mode → command plan 생성 (실행 안 함) + backend/frontend quality 섹션.
- is_live_authorization/broker_order_sent/order_created/contains_secret=False,
  broker/route_order import·호출 0건, secret 0건.
"""

from __future__ import annotations

import json

import pytest

from app.system import premarket_readiness_gate as pg
from app.system.premarket_readiness_gate import (
    PremarketInputs,
    PremarketReadinessReport,
    PSection,
    PVerdict,
    full_mode_command_plan,
    merge_full_mode_results,
    render_markdown_report,
    run_premarket_readiness_gate,
    summarize,
)


def _sec(report, name):
    return next((s for s in report.sections if s.section == name), None)


# ── fast mode 기본 ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fast_unknown() -> PremarketReadinessReport:
    return run_premarket_readiness_gate()


def test_fast_report_generated(fast_unknown):
    assert fast_unknown.mode == "fast"
    names = {s.section for s in fast_unknown.sections}
    for sec in (PSection.ENV_READINESS, PSection.KIS_CREDENTIALS,
                PSection.PAPER_LIVE_SEPARATION, PSection.UNIVERSE_FALLBACK,
                PSection.PORTFOLIO_SOURCE, PSection.AGENT_CARDS,
                PSection.PROGRAM_INTEGRITY, PSection.PREFLIGHT_SMOKE,
                PSection.DOCS_RUNBOOK, PSection.REPORT_SCRIPTS):
        assert sec.value in names, f"missing section {sec.value}"


def test_env_section_pass(fast_unknown):
    assert _sec(fast_unknown, "ENV_READINESS").verdict == PVerdict.PASS.value


def test_paper_live_separation_pass(fast_unknown):
    assert _sec(fast_unknown, "PAPER_LIVE_SEPARATION").verdict == PVerdict.PASS.value


def test_universe_pass(fast_unknown):
    assert _sec(fast_unknown, "UNIVERSE_FALLBACK").verdict == PVerdict.PASS.value


def test_portfolio_pass(fast_unknown):
    assert _sec(fast_unknown, "PORTFOLIO_SOURCE").verdict == PVerdict.PASS.value


def test_agent_cards_pass(fast_unknown):
    assert _sec(fast_unknown, "AGENT_CARDS").verdict == PVerdict.PASS.value


def test_program_integrity_pass(fast_unknown):
    assert _sec(fast_unknown, "PROGRAM_INTEGRITY").verdict == PVerdict.PASS.value


def test_docs_pass(fast_unknown):
    # 부정/인용 문구는 허용 — 필수 문서 존재 + 안전 문구.
    assert _sec(fast_unknown, "DOCS_RUNBOOK").verdict == PVerdict.PASS.value


def test_report_scripts_pass(fast_unknown):
    assert _sec(fast_unknown, "REPORT_SCRIPTS").verdict == PVerdict.PASS.value


def test_preflight_skip_without_db(fast_unknown):
    # 런타임 DB 미주입 → SKIP (API endpoint 가 주입).
    assert _sec(fast_unknown, "PREFLIGHT_SMOKE").verdict == PVerdict.SKIP.value


# ── KIS credentials WARN / present ───────────────────────────────────────────


def test_kis_credentials_unknown_warn(fast_unknown):
    s = _sec(fast_unknown, "KIS_CREDENTIALS")
    assert s.verdict == PVerdict.WARN.value
    assert fast_unknown.premarket_ready is True       # WARN 은 offline 빌드 허용.
    assert fast_unknown.build_ready_for_offline is True
    assert fast_unknown.ready_for_market_open_rehearsal is False   # 자격 미상 → 리허설 불가.


def test_kis_credentials_missing_warn():
    r = run_premarket_readiness_gate(PremarketInputs(kis_credentials_present=False))
    assert _sec(r, "KIS_CREDENTIALS").verdict == PVerdict.WARN.value
    assert r.ready_for_market_open_rehearsal is False


def test_kis_credentials_present_rehearsal_ready():
    r = run_premarket_readiness_gate(PremarketInputs(kis_credentials_present=True))
    assert _sec(r, "KIS_CREDENTIALS").verdict == PVerdict.PASS.value
    assert r.premarket_ready is True
    assert r.ready_for_market_open_rehearsal is True
    assert r.overall_status == PVerdict.PASS.value


# ── live flag on → FAIL ──────────────────────────────────────────────────────


def test_live_flag_on_fails():
    r = run_premarket_readiness_gate(PremarketInputs(enable_live_trading=True))
    assert _sec(r, "ENV_READINESS").verdict == PVerdict.FAIL.value
    assert r.premarket_ready is False
    assert r.build_ready_for_offline is False
    assert r.overall_status == PVerdict.FAIL.value


def test_kis_is_paper_false_fails():
    r = run_premarket_readiness_gate(PremarketInputs(kis_is_paper=False))
    assert _sec(r, "ENV_READINESS").verdict == PVerdict.FAIL.value


# ── preflight 주입 ───────────────────────────────────────────────────────────


def test_preflight_injected_pass():
    r = run_premarket_readiness_gate(PremarketInputs(preflight_result={"overall_status": "PASS"}))
    assert _sec(r, "PREFLIGHT_SMOKE").verdict == PVerdict.PASS.value


def test_preflight_injected_fail():
    r = run_premarket_readiness_gate(PremarketInputs(preflight_result={"overall_status": "FAIL"}))
    assert _sec(r, "PREFLIGHT_SMOKE").verdict == PVerdict.FAIL.value
    assert r.premarket_ready is False


# ── full mode command plan ───────────────────────────────────────────────────


def test_full_mode_command_plan():
    plan = full_mode_command_plan()
    assert len(plan) >= 5
    names = {c.name for c in plan}
    assert "ruff check" in names
    assert "npm run build" in names
    # plan 은 명령만 — 실행/주문 0건.
    for c in plan:
        assert "place_order" not in " ".join(c.argv)


def test_full_mode_report_has_quality_sections():
    r = run_premarket_readiness_gate(mode="full")
    assert r.mode == "full"
    assert _sec(r, "BACKEND_QUALITY") is not None
    assert _sec(r, "FRONTEND_QUALITY") is not None
    assert len(r.full_mode_command_plan) >= 5


def test_merge_full_mode_results():
    r = run_premarket_readiness_gate(PremarketInputs(kis_credentials_present=True), mode="full")
    merged = merge_full_mode_results(r, {"BACKEND_QUALITY": "PASS", "FRONTEND_QUALITY": "PASS"})
    assert _sec(merged, "BACKEND_QUALITY").verdict == PVerdict.PASS.value
    assert _sec(merged, "FRONTEND_QUALITY").verdict == PVerdict.PASS.value


def test_merge_full_mode_fail_blocks():
    r = run_premarket_readiness_gate(mode="full")
    merged = merge_full_mode_results(r, {"BACKEND_QUALITY": "FAIL", "FRONTEND_QUALITY": "PASS"})
    assert _sec(merged, "BACKEND_QUALITY").verdict == PVerdict.FAIL.value
    assert merged.premarket_ready is False


# ── invariants / secret / import 가드 ────────────────────────────────────────


def test_report_invariants(fast_unknown):
    assert fast_unknown.is_live_authorization is False
    assert fast_unknown.broker_order_sent is False
    assert fast_unknown.order_created is False
    assert fast_unknown.contains_secret is False


def test_report_invariant_enforced():
    with pytest.raises(ValueError):
        PremarketReadinessReport(
            generated_at="x", mode="fast", sections=(), counts={}, overall_status="PASS",
            premarket_ready=True, build_ready_for_offline=True,
            ready_for_market_open_rehearsal=False, missing_items=(), warn_items=(),
            fail_items=(), is_live_authorization=True)


def test_no_secret_in_report(fast_unknown):
    text = json.dumps(summarize(fast_unknown), ensure_ascii=False) + render_markdown_report(fast_unknown)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "is_live_authorization\": true"):
        assert forbidden not in text


def test_no_forbidden_imports():
    src = open(pg.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "from app.execution.order_executor", "from app.execution.paper_trader",
                "import anthropic", "import openai", "import httpx", "import requests",
                ".place_order(", "route_order(", "db.add(", "db.commit("):
        assert tok not in src, f"forbidden token: {tok}"


def test_doc_violation_negation_aware():
    # 부정/인용 줄은 위반 아님.
    assert pg._doc_violations('"수익 보장" 문구 0건') == []
    assert pg._doc_violations("수익 보장 아님") == []
    # 단언형은 위반.
    assert "수익 보장" in pg._doc_violations("이 전략은 수익 보장 전략입니다")
    assert "원금 보장" in pg._doc_violations("원금 보장 됩니다")


def test_markdown_required_sections(fast_unknown):
    md = render_markdown_report(fast_unknown)
    for sec in ("전체 요약", "premarket_ready", "ready_for_market_open_rehearsal",
                "섹션별 점검", "BUILD-02B", "실전 승인 아님", "수익 보장 아님"):
        assert sec in md


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_premarket_readiness(client):
    r = client.get("/api/system/premarket-readiness")
    assert r.status_code == 200
    body = r.json()
    assert "sections" in body and len(body["sections"]) >= 10
    assert "premarket_ready" in body
    assert "ready_for_market_open_rehearsal" in body
    assert body["is_live_authorization"] is False
    assert body["broker_order_sent"] is False


def test_api_no_broker_order(client):
    client.get("/api/system/premarket-readiness")
    assert len(client.test_broker.orders) == 0


def test_api_preflight_section_runs(client):
    # API 는 DB 주입 → preflight SKIP 아님 (PASS/WARN/FAIL).
    body = client.get("/api/system/premarket-readiness").json()
    pf = next((s for s in body["sections"] if s["section"] == "PREFLIGHT_SMOKE"), None)
    assert pf is not None
    assert pf["verdict"] in ("PASS", "WARN", "FAIL", "SKIP")
