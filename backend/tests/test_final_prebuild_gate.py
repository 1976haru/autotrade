"""체크리스트 11-00 — final_prebuild_gate 모듈 + endpoint 테스트."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.system.final_prebuild_gate import (
    BUILD_BLOCKED,
    BUILD_READY,
    BUILD_READY_WITH_WARNINGS,
    FinalPrebuildReport,
    GateInputs,
    render_markdown,
    run_final_prebuild_gate,
    to_dict,
)

_REPO = Path(__file__).resolve().parents[2]
_REQUIRED_SECTIONS = {
    "REPOSITORY_STATUS", "BACKEND_QUALITY", "FRONTEND_QUALITY", "SECURITY_SECRET_SCAN",
    "HEALTH_PREFLIGHT", "CONFIG_ENV", "DB", "KIS_CREDENTIALS", "KIS_PAPER_ORDER_PATH",
    "UNIVERSE", "PORTFOLIO", "LIVE_SAFETY", "UI_API", "DOCS_RUNBOOK", "EXE_BUILD_INPUTS",
    "BUILD_01_PROGRAM_INTEGRITY", "BUILD_02A_PREMARKET", "BUILD_02B_KIS_PAPER_AUDIT",
    "INTRADAY_REAL_DATA",
}


def _all_green(**over):
    base = dict(
        backend_quality="PASS", ruff_pass=True, pytest_collection_ok=True,
        frontend_quality="PASS", frontend_build_ok=True, security_findings=0,
        security_scanned=True, db_status="OK", preflight_status="OK", backend_health_ok=True,
        kis_credentials_present=True, agent_pipeline_status="PASS", order_quality_status="PASS",
        build01_verdict=True, build02a_ready=True, build02b_ready=True,
        ui_api_manifest_status="PASS", backtest_status="PASS", walk_forward_status="PASS",
        stress_fail_count=0, intraday_final_judgement="PROMISING_FOR_PAPER_TEST",
        paper_sample_count=120)
    base.update(over)
    return GateInputs(**base)


def test_fast_default_buildable_with_warnings():
    r = run_final_prebuild_gate(GateInputs(security_scanned=False))
    assert r.overall_status == BUILD_READY_WITH_WARNINGS
    assert r.exe_build_allowed is True


def test_all_green_build_ready():
    r = run_final_prebuild_gate(_all_green())
    assert r.overall_status == BUILD_READY
    assert r.ready_for_market_rehearsal is True


def test_all_sections_present():
    r = run_final_prebuild_gate(GateInputs())
    names = {s.name for s in r.sections}
    assert _REQUIRED_SECTIONS <= names


def test_live_flag_blocked():
    for bad in ({"enable_live_trading": True}, {"enable_ai_execution": True},
                {"enable_futures_live_trading": True}, {"kis_is_paper": False}):
        r = run_final_prebuild_gate(GateInputs(**bad))
        assert r.overall_status == BUILD_BLOCKED, bad
        assert r.exe_build_allowed is False


def test_security_finding_blocked():
    r = run_final_prebuild_gate(GateInputs(security_findings=1))
    assert r.overall_status == BUILD_BLOCKED


def test_secret_exposure_blocked():
    r = run_final_prebuild_gate(GateInputs(secret_exposure=True))
    assert r.overall_status == BUILD_BLOCKED


def test_backend_fail_blocked():
    r = run_final_prebuild_gate(GateInputs(backend_quality="FAIL"))
    assert r.overall_status == BUILD_BLOCKED


def test_frontend_build_fail_blocked():
    r = run_final_prebuild_gate(GateInputs(frontend_quality="FAIL", frontend_build_ok=False))
    assert r.overall_status == BUILD_BLOCKED


def test_kis_live_order_path_open_blocked():
    r = run_final_prebuild_gate(GateInputs(kis_live_order_path_blocked=False))
    assert r.overall_status == BUILD_BLOCKED


def test_broker_direct_call_blocked():
    r = run_final_prebuild_gate(GateInputs(broker_direct_call_found=True))
    assert r.overall_status == BUILD_BLOCKED


def test_order_buttons_blocked():
    r = run_final_prebuild_gate(GateInputs(order_buttons_found=True))
    assert r.overall_status == BUILD_BLOCKED


def test_db_fail_blocked():
    r = run_final_prebuild_gate(GateInputs(db_status="FAIL"))
    assert r.overall_status == BUILD_BLOCKED


def test_exe_inputs_missing_blocked():
    r = run_final_prebuild_gate(GateInputs(exe_build_inputs_ok=False))
    assert r.overall_status == BUILD_BLOCKED


# --------------------------- WARN (not blocked) ----------------------------

def test_kis_credentials_missing_default_warn_not_blocked():
    r = run_final_prebuild_gate(_all_green(kis_credentials_present=False, security_scanned=True))
    assert r.overall_status == BUILD_READY_WITH_WARNINGS
    assert r.exe_build_allowed is True
    assert any("KIS_CREDENTIALS" in s.name and s.status == "WARN" for s in r.sections)


def test_require_kis_credentials_makes_fail():
    r = run_final_prebuild_gate(_all_green(kis_credentials_present=False,
                                           require_kis_credentials=True))
    assert r.overall_status == BUILD_BLOCKED


def test_intraday_worth_more_research_warn_not_blocked():
    r = run_final_prebuild_gate(_all_green(intraday_final_judgement="WORTH_MORE_RESEARCH"))
    assert r.overall_status == BUILD_READY_WITH_WARNINGS
    assert r.exe_build_allowed is True


def test_intraday_blocked_by_data_warn_not_build_blocked():
    r = run_final_prebuild_gate(_all_green(intraday_final_judgement="BLOCKED_BY_DATA"))
    assert r.exe_build_allowed is True  # 데이터 부족은 빌드 차단 아님


def test_paper_sample_zero_warn():
    r = run_final_prebuild_gate(_all_green(paper_sample_count=0))
    assert r.overall_status == BUILD_READY_WITH_WARNINGS


# --------------------------- invariants / report ---------------------------

def test_report_invariants():
    r = run_final_prebuild_gate(GateInputs())
    assert r.contains_secret is False
    assert r.is_live_authorization is False
    assert r.broker_order_sent is False
    assert r.order_created is False
    assert r.no_profit_guarantee is True


@pytest.mark.parametrize("bad", [
    {"is_live_authorization": True}, {"broker_order_sent": True},
    {"order_created": True}, {"contains_secret": True}, {"no_profit_guarantee": False},
])
def test_report_guard(bad):
    base = dict(generated_at="x", overall_status=BUILD_READY, user_line="x",
                exe_build_allowed=True, ready_for_market_rehearsal=False,
                ready_for_paper_rehearsal=True, sections=(), counts={})
    base.update(bad)
    with pytest.raises(ValueError):
        FinalPrebuildReport(**base)


def test_to_dict_and_markdown_safe():
    r = run_final_prebuild_gate(_all_green())
    d = to_dict(r)
    for k in ("overall_status", "exe_build_allowed", "sections", "blocked_reasons",
              "is_live_authorization", "contains_secret", "no_profit_guarantee"):
        assert k in d
    md = render_markdown(r)
    assert "실전 승인 아님" in md and "수익 보장 아님" in md
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", md)


def test_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "system" / "final_prebuild_gate.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src


# --------------------------- endpoint --------------------------------------

def test_final_prebuild_gate_endpoint(client, safe_default_flags):
    r = client.get("/api/system/final-prebuild-gate")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["overall_status"] in (BUILD_READY, BUILD_READY_WITH_WARNINGS, BUILD_BLOCKED)
    # safe_default_flags → 안전 → 빌드 차단 아님.
    assert d["exe_build_allowed"] is True
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)


def test_final_prebuild_gate_get_only(client, safe_default_flags):
    assert client.post("/api/system/final-prebuild-gate", json={}).status_code in (404, 405)
