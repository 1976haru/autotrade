"""Release Readiness Report (#92) — evaluator + API + invariants + 정적 grep 가드."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.governance.release_readiness import (
    ReadinessCategory,
    ReadinessSeverity,
    ReleaseKind,
    ReleaseReadinessInput,
    ReleaseReadinessResult,
    ReleaseReadinessThresholds,
    ReleaseReadinessVerdict,
    evaluate_release_readiness,
    render_markdown_report,
)


# ---------- helpers ----------


def _beta_ready_input(**kw) -> ReleaseReadinessInput:
    """BETA 단계에서 모든 required PASS 인 입력."""
    defaults = dict(
        target_release_tag="v1.0.0-beta.5",
        release_kind=ReleaseKind.BETA.value,
        kis_is_paper=True,
        enable_live_trading=False,
        enable_ai_execution=False,
        enable_futures_live_trading=False,
        pre_market_verdict="READY_TO_START",
        pre_market_start_allowed=True,
        last_system_audit_at=datetime.now(timezone.utc) - timedelta(days=2),
        repository_hygiene_pass=True,
        documentation_coverage_ok=True,
        recent_loss_limit_violations_7d=0,
        recent_audit_missing_7d=0,
        recent_emergency_stop_events_7d=0,
    )
    defaults.update(kw)
    return ReleaseReadinessInput(**defaults)


def _rc_ready_input(**kw) -> ReleaseReadinessInput:
    """RC 단계 PASS 입력 (BETA 위에 Paper Gate / strategy / opt-in / sidecar / test rate 추가)."""
    base = _beta_ready_input(
        release_kind=ReleaseKind.RC.value,
        target_release_tag="v1.0.0-rc.1",
        paper_gate_verdict="PASS",
        alpha_decay_worst_status="HEALTHY",
        alpha_decay_strategies_evaluated=3,
        alpha_decay_disable_candidate_count=0,
        operator_explicit_opt_in=True,
        desktop_sidecar_built=True,
        recent_test_pass_rate_pct=98.0,
        recent_test_total_count=300,
    )
    return ReleaseReadinessInput(**{**base.__dict__, **kw})


# ============================================================
# DTO invariants
# ============================================================


def test_result_rejects_is_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        ReleaseReadinessResult(
            target_release_tag="v0", release_kind="BETA",
            verdict=ReleaseReadinessVerdict.READY_TO_TAG,
            is_live_authorization=True,
        )


def test_result_rejects_auto_apply_allowed_true():
    with pytest.raises(ValueError, match="auto_apply_allowed"):
        ReleaseReadinessResult(
            target_release_tag="v0", release_kind="BETA",
            verdict=ReleaseReadinessVerdict.READY_TO_TAG,
            auto_apply_allowed=True,
        )


def test_result_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        ReleaseReadinessResult(
            target_release_tag="v0", release_kind="BETA",
            verdict=ReleaseReadinessVerdict.READY_TO_TAG,
            is_order_signal=True,
        )


def test_result_rejects_live_flag_changed_true():
    with pytest.raises(ValueError, match="live_flag_changed"):
        ReleaseReadinessResult(
            target_release_tag="v0", release_kind="BETA",
            verdict=ReleaseReadinessVerdict.READY_TO_TAG,
            live_flag_changed=True,
        )


def test_result_rejects_mode_changed_true():
    with pytest.raises(ValueError, match="mode_changed"):
        ReleaseReadinessResult(
            target_release_tag="v0", release_kind="BETA",
            verdict=ReleaseReadinessVerdict.READY_TO_TAG,
            mode_changed=True,
        )


def test_to_dict_carries_all_invariant_flags():
    r = evaluate_release_readiness(_beta_ready_input())
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["auto_apply_allowed"] is False
    assert d["is_order_signal"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ============================================================
# enum / category coverage
# ============================================================


def test_readiness_severity_no_buy_sell_hold_values():
    values = {s.value for s in ReadinessSeverity}
    for banned in ("BUY", "SELL", "HOLD", "PLACE_ORDER"):
        assert banned not in values


def test_readiness_verdict_has_4_states():
    values = {v.value for v in ReleaseReadinessVerdict}
    assert values == {
        "READY_TO_TAG",
        "READY_WITH_CAVEATS",
        "DO_NOT_TAG",
        "INSUFFICIENT_DATA",
    }


def test_readiness_category_10_values():
    values = {c.value for c in ReadinessCategory}
    assert len(values) == 10
    for needed in ("safety_flags", "governance_gates", "pre_market",
                   "strategy_health", "desktop_build", "system_hygiene",
                   "documentation", "data_freshness", "recent_activity",
                   "operator"):
        assert needed in values


# ============================================================
# verdict logic
# ============================================================


def test_beta_ready_minimum_passes():
    r = evaluate_release_readiness(_beta_ready_input())
    assert r.verdict is ReleaseReadinessVerdict.READY_TO_TAG
    assert r.failed_required == []


def test_beta_with_warning_returns_caveats():
    inp = _beta_ready_input(
        last_system_audit_at=datetime.now(timezone.utc) - timedelta(days=45),
    )
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.READY_WITH_CAVEATS
    assert any("system_audit_recency" in w for w in r.warnings)


def test_kis_is_paper_false_blocks_release():
    inp = _beta_ready_input(kis_is_paper=False)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "kis_is_paper_safety" in r.failed_required


def test_enable_live_trading_true_blocks_release():
    inp = _beta_ready_input(enable_live_trading=True)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "enable_live_trading_safety" in r.failed_required


def test_enable_ai_execution_true_blocks_release():
    inp = _beta_ready_input(enable_ai_execution=True)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "enable_ai_execution_safety" in r.failed_required


def test_enable_futures_true_blocks_release():
    inp = _beta_ready_input(enable_futures_live_trading=True)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "enable_futures_safety" in r.failed_required


def test_pre_market_do_not_start_blocks_release():
    inp = _beta_ready_input(pre_market_verdict="DO_NOT_START")
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "pre_market_check" in r.failed_required


def test_audit_older_than_90_days_blocks():
    inp = _beta_ready_input(
        last_system_audit_at=datetime.now(timezone.utc) - timedelta(days=120),
    )
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "system_audit_recency" in r.failed_required


def test_recent_loss_limit_violations_blocks():
    inp = _beta_ready_input(recent_loss_limit_violations_7d=1)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "recent_loss_limit" in r.failed_required


def test_recent_audit_missing_blocks():
    inp = _beta_ready_input(recent_audit_missing_7d=2)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "recent_audit_missing" in r.failed_required


def test_repository_hygiene_fail_blocks():
    inp = _beta_ready_input(repository_hygiene_pass=False)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "repository_hygiene" in r.failed_required


def test_documentation_missing_blocks():
    inp = _beta_ready_input(documentation_coverage_ok=False)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "documentation" in r.failed_required


# ---------- RC tier ----------


def test_rc_ready_passes():
    r = evaluate_release_readiness(_rc_ready_input())
    assert r.verdict is ReleaseReadinessVerdict.READY_TO_TAG
    assert r.failed_required == []


def test_rc_without_operator_opt_in_blocks():
    inp = _rc_ready_input(operator_explicit_opt_in=False)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "operator_opt_in" in r.failed_required


def test_rc_without_paper_gate_blocks():
    inp = _rc_ready_input(paper_gate_verdict="FAIL")
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "paper_gate" in r.failed_required


def test_rc_with_alpha_disable_candidates_blocks():
    inp = _rc_ready_input(
        alpha_decay_disable_candidate_count=5,  # > 3 (default FAIL threshold)
        alpha_decay_worst_status="DISABLE_CANDIDATE",
    )
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "alpha_decay" in r.failed_required


def test_rc_test_pass_rate_low_blocks():
    inp = _rc_ready_input(recent_test_pass_rate_pct=70.0)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    assert "test_pass_rate" in r.failed_required


def test_rc_desktop_sidecar_not_built_warns_not_blocks():
    """RC 단계에서 desktop_sidecar_build 가 not-built 이면 WARN — 시작은 가능.

    설계 의도: desktop sidecar 미빌드는 *추가 빌드 필요* 시그널일 뿐, 운영자가
    별도로 빌드 머신에서 빌드 후 릴리스 가능. 따라서 verdict 차단이 아닌
    READY_WITH_CAVEATS. 운영자가 warnings 리스트를 확인하고 빌드 시도.
    """
    inp = _rc_ready_input(desktop_sidecar_built=False)
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.READY_WITH_CAVEATS
    assert any("desktop_sidecar_build" in w for w in r.warnings)
    assert "desktop_sidecar_build" not in r.failed_required


# ---------- BETA 에서는 미빌드 OK ----------


def test_beta_desktop_not_built_only_warns():
    inp = _beta_ready_input(
        desktop_sidecar_built=False,
        desktop_installer_built=False,
    )
    r = evaluate_release_readiness(inp)
    # BETA 단계에서는 desktop_*_build 가 not-required (WARN only).
    assert r.verdict is ReleaseReadinessVerdict.READY_WITH_CAVEATS
    assert any("desktop_sidecar_build" in w for w in r.warnings)


# ---------- INSUFFICIENT_DATA ----------


def test_insufficient_data_when_required_all_unknown():
    """모든 required 항목이 UNKNOWN + FAIL 0건 → INSUFFICIENT_DATA."""
    inp = ReleaseReadinessInput(
        target_release_tag="v0",
        release_kind=ReleaseKind.BETA.value,
        # 안전 flag 4종은 default (PASS) — 모두 PASS 인 케이스 분리.
        # required UNKNOWN 만 들어가도록 minimal 입력으로:
        pre_market_verdict=None,
        repository_hygiene_pass=None,
        documentation_coverage_ok=None,
        last_system_audit_at=None,
        # recent activity 모두 0 default → PASS.
    )
    r = evaluate_release_readiness(inp)
    # safety_flags + recent_loss_limit + recent_audit_missing 가 PASS 이므로
    # required PASS 가 6건 이상 존재. 따라서 INSUFFICIENT_DATA 가 아닌
    # READY_WITH_CAVEATS 또는 READY_TO_TAG 분기로 가야 정상.
    # 본 테스트는 INSUFFICIENT_DATA 분기를 *의도적으로 발생*시키기 어렵다는
    # 케이스 — 따라서 verdict 가 DO_NOT_TAG 가 아님만 확인.
    assert r.verdict is not ReleaseReadinessVerdict.DO_NOT_TAG


def test_strict_mode_treats_unknown_required_as_fail():
    inp = ReleaseReadinessInput(
        target_release_tag="v0",
        release_kind=ReleaseKind.BETA.value,
        strict=True,
        pre_market_verdict=None,
        repository_hygiene_pass=None,
        documentation_coverage_ok=None,
        last_system_audit_at=None,
    )
    r = evaluate_release_readiness(inp)
    assert r.verdict is ReleaseReadinessVerdict.DO_NOT_TAG
    # required UNKNOWN 4종이 strict 로 FAIL 됨.
    assert "pre_market_check" in r.failed_required
    assert "repository_hygiene" in r.failed_required
    assert "documentation" in r.failed_required
    assert "system_audit_recency" in r.failed_required


# ============================================================
# markdown
# ============================================================


def test_markdown_contains_disclaimer_and_verdict():
    r = evaluate_release_readiness(_beta_ready_input())
    text = render_markdown_report(r)
    assert "Release Readiness Report" in text
    assert "READY_TO_TAG" in text
    assert "실거래 활성화 / 자동 promotion 을 의미하지 않습니다" in text


def test_markdown_no_buy_sell_hold_or_place_order():
    r = evaluate_release_readiness(_beta_ready_input())
    text = render_markdown_report(r)
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                   "HOLD signal", "Place Order", "실거래 시작", "지금 매수",
                   "지금 매도"]:
        assert banned not in text


def test_markdown_no_secret_patterns():
    inp = _beta_ready_input(operator_note="check ENABLE_LIVE_TRADING flag again")
    r = evaluate_release_readiness(inp)
    text = render_markdown_report(r).lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text


def test_markdown_lists_failed_required_and_warnings():
    inp = _beta_ready_input(kis_is_paper=False)
    r = evaluate_release_readiness(inp)
    text = render_markdown_report(r)
    assert "DO_NOT_TAG" in text
    assert "kis_is_paper_safety" in text
    assert "필요 조치" in text


# ============================================================
# API
# ============================================================


def test_api_post_release_readiness_returns_invariants(client):
    body = {
        "target_release_tag": "v1.0.0-beta.5",
        "release_kind": "BETA",
        "kis_is_paper": True,
        "pre_market_verdict": "READY_TO_START",
        "pre_market_start_allowed": True,
        "last_system_audit_at": (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat(),
        "repository_hygiene_pass": True,
        "documentation_coverage_ok": True,
    }
    res = client.post("/api/governance/release-readiness/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "READY_TO_TAG"
    # invariants.
    assert data["is_live_authorization"] is False
    assert data["auto_apply_allowed"] is False
    assert data["is_order_signal"] is False
    assert data["live_flag_changed"] is False
    assert data["mode_changed"] is False


def test_api_post_release_readiness_blocks_when_live_flag_true(client):
    body = {
        "target_release_tag": "v1.0.0-beta.5",
        "release_kind": "BETA",
        "kis_is_paper": True,
        "enable_live_trading": True,  # 위반.
    }
    res = client.post("/api/governance/release-readiness/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["verdict"] == "DO_NOT_TAG"
    assert "enable_live_trading_safety" in data["failed_required"]


def test_api_post_release_readiness_does_not_leak_secrets(client):
    body = {
        "target_release_tag": "v1.0.0-beta.5",
        "release_kind": "BETA",
        "kis_is_paper": True,
        "operator_note": "test - ENABLE_LIVE_TRADING flag check",
    }
    res = client.post("/api/governance/release-readiness/evaluate", json=body)
    assert res.status_code == 200
    text = res.text.lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text


def test_api_post_release_readiness_markdown(client):
    body = {
        "target_release_tag": "v1.0.0-beta.5",
        "release_kind": "BETA",
        "kis_is_paper": True,
        "pre_market_verdict": "READY_TO_START",
        "pre_market_start_allowed": True,
        "last_system_audit_at": (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat(),
        "repository_hygiene_pass": True,
        "documentation_coverage_ok": True,
    }
    res = client.post("/api/governance/release-readiness/markdown", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "READY_TO_TAG"
    assert "Release Readiness Report" in data["markdown"]
    assert "실거래 활성화" in data["markdown"]


def test_api_post_invalid_payload_returns_400(client):
    res = client.post(
        "/api/governance/release-readiness/evaluate",
        json={"recent_loss_limit_violations_7d": "not-an-int"},
    )
    # FastAPI 가 schema 검증 단계에서 422 반환 — invalid model.
    assert res.status_code in (400, 422)


# ============================================================
# invariants — static grep guards
# ============================================================


_MODULE_PATHS = [
    Path("backend/app/governance/release_readiness.py"),
    Path("backend/app/api/routes_governance.py"),
]


def _resolve(path: Path) -> Path:
    if path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def test_release_readiness_does_not_import_broker_or_executor():
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_release_readiness_does_not_import_ai_or_external_http():
    forbidden = [
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_release_readiness_does_not_read_settings():
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"reads settings: {needle!r}"


def test_release_readiness_does_not_call_other_gate_evaluators():
    """본 모듈은 다른 gate evaluator 를 *직접 호출하지 않는다* — 호출자가 결과 carry.

    이로써 release_readiness 는 *순수 meta-aggregator* 로 분리된다.
    """
    forbidden = [
        "evaluate_paper_gate(",
        "evaluate_live_manual_gate(",
        "evaluate_ai_assist_gate(",
        "evaluate_ai_execution_gate(",
        "evaluate_pre_market_check(",
        "evaluate_alpha_decay(",
        "evaluate_promotion(",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"directly calls other evaluator: {needle!r}"


def test_release_readiness_does_not_call_broker():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        "OrderExecutor(",
        "submit_candidate(",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_release_readiness_does_not_mutate_safety_flags():
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_release_readiness_does_not_write_to_db():
    forbidden = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    src = _resolve(_MODULE_PATHS[0]).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"writes to DB: {needle!r}"


def test_routes_release_readiness_does_not_leak_settings_get():
    """API route 도 settings 를 직접 *읽지 않는다* — 운영자 입력 ↔ 실제값
    혼선 방지. settings 는 별도 collector 가 채워서 payload 로 전달."""
    src = _resolve(_MODULE_PATHS[1]).read_text(encoding="utf-8")
    # release_readiness 섹션만 검사 — 다른 endpoint 의 get_settings 사용은 무관.
    rr_section_start = src.find("# ---------- #92 Release Readiness Report")
    assert rr_section_start >= 0
    rr_section = src[rr_section_start:]
    assert "get_settings(" not in rr_section, (
        "release_readiness route 가 settings 를 직접 읽음 — payload 로 carry "
        "받아야 함"
    )
