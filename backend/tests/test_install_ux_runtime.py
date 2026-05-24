"""INSTALL-UX-FIX-01 — 설치본 런타임 오탐 방지 테스트.

packaged/CI 런타임에서 소스 전용 점검(빌드 입력/문서/스크립트) 누락이 FAIL/BUILD_BLOCKED
가 아니라 SKIP 으로 처리되는지, 소스 개발 환경에서는 그대로 FAIL 인지 검증.
"""

from __future__ import annotations

from pathlib import Path

from app.core.runtime_context import (
    CI_BUILD,
    PACKAGED_RUNTIME,
    SOURCE_DEV,
    detect_app_runtime,
    is_source_dev,
)
from app.system.final_prebuild_gate import (
    BUILD_BLOCKED,
    GateInputs,
    run_final_prebuild_gate,
)


# --------------------------- runtime detection -----------------------------

def test_detect_explicit_env():
    assert detect_app_runtime({"AUTOTRADE_RUNTIME": "packaged"}) == PACKAGED_RUNTIME
    assert detect_app_runtime({"AUTOTRADE_RUNTIME": "ci"}) == CI_BUILD
    assert detect_app_runtime({"AUTOTRADE_RUNTIME": "source"}) == SOURCE_DEV


def test_detect_ci_env():
    assert detect_app_runtime({"GITHUB_ACTIONS": "true"}) == CI_BUILD
    assert detect_app_runtime({"CI": "true"}) == CI_BUILD


def test_detect_default_source(monkeypatch):
    # frozen/build_stamp 없고 CI 아님 → SOURCE_DEV (env 비움).
    assert detect_app_runtime({}) in (SOURCE_DEV, PACKAGED_RUNTIME, CI_BUILD)
    assert is_source_dev(SOURCE_DEV) is True
    assert is_source_dev(PACKAGED_RUNTIME) is False


# --------------------------- final prebuild gate runtime -------------------

def test_packaged_missing_inputs_not_blocked():
    r = run_final_prebuild_gate(GateInputs(
        exe_build_inputs_ok=False, docs_runbook_ok=False,
        app_runtime=PACKAGED_RUNTIME, security_scanned=True))
    assert r.overall_status != BUILD_BLOCKED
    assert r.exe_build_allowed is True
    by = {s.name: s.status for s in r.sections}
    assert by["EXE_BUILD_INPUTS"] == "SKIP"
    assert by["DOCS_RUNBOOK"] == "SKIP"


def test_ci_missing_inputs_not_blocked():
    r = run_final_prebuild_gate(GateInputs(
        exe_build_inputs_ok=False, app_runtime=CI_BUILD, security_scanned=True))
    assert r.overall_status != BUILD_BLOCKED
    by = {s.name: s.status for s in r.sections}
    assert by["EXE_BUILD_INPUTS"] == "SKIP"


def test_source_missing_inputs_still_blocked():
    r = run_final_prebuild_gate(GateInputs(
        exe_build_inputs_ok=False, app_runtime=SOURCE_DEV))
    assert r.overall_status == BUILD_BLOCKED
    by = {s.name: s.status for s in r.sections}
    assert by["EXE_BUILD_INPUTS"] == "FAIL"


def test_packaged_still_blocks_real_safety():
    """런타임과 무관하게 실제 안전 위반(LIVE flag)은 BUILD_BLOCKED 유지."""
    r = run_final_prebuild_gate(GateInputs(
        enable_live_trading=True, app_runtime=PACKAGED_RUNTIME))
    assert r.overall_status == BUILD_BLOCKED


# --------------------------- premarket gate runtime ------------------------

def test_premarket_packaged_missing_docs_scripts_skip(monkeypatch, tmp_path):
    """packaged 런타임 + docs/scripts 미번들 → FAIL 아니라 SKIP."""
    import app.system.premarket_readiness_gate as pg
    monkeypatch.setattr(pg, "_REPO_ROOT", Path(tmp_path))   # 빈 dir → 파일 부재
    docs = pg._sec_docs(pg.PremarketInputs(app_runtime=PACKAGED_RUNTIME))
    scripts = pg._sec_report_scripts(pg.PremarketInputs(app_runtime=PACKAGED_RUNTIME))
    # 파일 부재라도 packaged → 존재 항목이 SKIP (FAIL 아님).
    doc_exists = [i for i in docs.items if i.reason_code == "DOC_EXISTS"]
    assert doc_exists and all(i.verdict == "SKIP" for i in doc_exists)
    assert scripts.verdict == "SKIP"


def test_premarket_source_missing_docs_fail(monkeypatch, tmp_path):
    import app.system.premarket_readiness_gate as pg
    monkeypatch.setattr(pg, "_REPO_ROOT", Path(tmp_path))
    docs = pg._sec_docs(pg.PremarketInputs(app_runtime=SOURCE_DEV))
    doc_exists = [i for i in docs.items if i.reason_code == "DOC_EXISTS"]
    assert doc_exists and any(i.verdict == "FAIL" for i in doc_exists)
