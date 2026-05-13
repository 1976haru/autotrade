"""AI Execution Activation Gate (#75) — evaluator + API + invariants.

본 테스트 모듈은 #45 `app/risk/ai_execution_gate.py` (order-time gate)와는
*별개 파일*인 #75 `app/governance/ai_execution_gate.py`(activation readiness gate)
를 검증한다.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path

import pytest

from app.governance.ai_execution_gate import (
    AIExecutionActivationGateResult,
    AIExecutionGateInput,
    AIExecutionGateThresholds,
    AIExecutionGateVerdict,
    evaluate_ai_execution_gate,
    get_policy_summary,
    render_markdown_report,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_ready_input() -> AIExecutionGateInput:
    """READY_FOR_REVIEW 를 만들 수 있는 입력."""
    return AIExecutionGateInput(
        strategy_name="ai_signals",
        evaluated_at=_utcnow(),
        promotion_gate_passed=True,
        paper_gate_passed=True,
        ai_assist_gate_passed=True,
        live_manual_gate_passed=True,
        user_explicit_opt_in=True,
        enable_live_trading=True,        # 이미 LIVE 라우팅 활성 (별도 PR로)
        enable_ai_execution=False,       # 본 게이트 평가 시점에는 false
        enable_futures_live_trading=False,
        live_manual_days=30,
        ai_assist_days=30,
        risk_manager_active=True,
        order_guard_active=True,
        ai_permission_gate_active=True,
        audit_log_complete=True,
        kill_switch_ready=True,
        circuit_breaker_configured=True,
        current_max_order_notional_krw=20_000,
        current_max_daily_loss_krw=4_000,
        current_max_daily_order_count=8,
        current_max_open_positions=2,
        allowed_symbols=("005930", "000660"),
        explicit_time_window_set=True,
        window_start_kst=time(9, 30),
        window_end_kst=time(14, 30),
        ai_confidence_threshold=80,
        signal_quality_threshold=75,
        system_errors=0,
        audit_missing_count=0,
        approval_bypass_attempts=0,
        futures_target=False,
    )


# ---------- DTO invariants ----------


def test_result_rejects_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        AIExecutionActivationGateResult(
            strategy_name="x",
            evaluated_at=_utcnow(),
            verdict=AIExecutionGateVerdict.READY_FOR_REVIEW,
            is_live_authorization=True,
        )


def test_result_rejects_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        AIExecutionActivationGateResult(
            strategy_name="x",
            evaluated_at=_utcnow(),
            verdict=AIExecutionGateVerdict.READY_FOR_REVIEW,
            is_order_signal=True,
        )


def test_result_rejects_investment_advice_true():
    with pytest.raises(ValueError, match="is_investment_advice"):
        AIExecutionActivationGateResult(
            strategy_name="x",
            evaluated_at=_utcnow(),
            verdict=AIExecutionGateVerdict.READY_FOR_REVIEW,
            is_investment_advice=True,
        )


def test_result_rejects_futures_allowed_true():
    """선물 AI execution은 본 게이트가 영구 불허."""
    with pytest.raises(ValueError, match="futures_allowed"):
        AIExecutionActivationGateResult(
            strategy_name="x",
            evaluated_at=_utcnow(),
            verdict=AIExecutionGateVerdict.READY_FOR_REVIEW,
            futures_allowed=True,
        )


def test_to_dict_has_invariant_flags():
    r = evaluate_ai_execution_gate(_make_ready_input())
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["is_investment_advice"] is False
    assert d["futures_allowed"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- happy path ----------


def test_ready_for_review_when_all_criteria_met():
    r = evaluate_ai_execution_gate(_make_ready_input())
    assert r.verdict is AIExecutionGateVerdict.READY_FOR_REVIEW
    assert not r.blocked_criteria
    assert "활성화" in r.next_step and "검토 가능" in r.next_step


# ---------- BLOCKED paths ----------


@pytest.mark.parametrize("field,bad_value", [
    ("promotion_gate_passed",  False),
    ("paper_gate_passed",      False),
    ("ai_assist_gate_passed",  False),
    ("live_manual_gate_passed", False),
    ("user_explicit_opt_in",   False),
    ("risk_manager_active",    False),
    ("order_guard_active",     False),
    ("ai_permission_gate_active", False),
    ("audit_log_complete",     False),
    ("kill_switch_ready",      False),
    ("circuit_breaker_configured", False),
])
def test_blocked_when_required_flag_missing(field, bad_value):
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, field: bad_value})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_live_manual_days_under_28():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "live_manual_days": 10})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_ai_assist_days_under_28():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "ai_assist_days": 5})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_order_notional_too_high():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{
        **inp.__dict__, "current_max_order_notional_krw": 100_000,
    })
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_daily_loss_too_high():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{
        **inp.__dict__, "current_max_daily_loss_krw": 50_000,
    })
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_daily_order_count_too_high():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{
        **inp.__dict__, "current_max_daily_order_count": 100,
    })
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_too_many_positions():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "current_max_open_positions": 10})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_no_allowed_symbols():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "allowed_symbols": ()})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_too_many_allowed_symbols():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{
        **inp.__dict__,
        "allowed_symbols": ("A", "B", "C", "D", "E", "F", "G"),
    })
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_no_explicit_time_window():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "explicit_time_window_set": False})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_ai_confidence_threshold_too_low():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "ai_confidence_threshold": 50})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_signal_quality_threshold_too_low():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "signal_quality_threshold": 30})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_system_errors_present():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "system_errors": 1})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_audit_missing():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "audit_missing_count": 1})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


def test_blocked_when_approval_bypass_attempts():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "approval_bypass_attempts": 1})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


# ---------- futures forbidden ----------


def test_blocked_when_futures_target_true():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "futures_target": True})
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED
    assert any("선물" in c for c in r.blocked_criteria)


def test_blocked_when_enable_futures_live_trading_true():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{
        **inp.__dict__, "enable_futures_live_trading": True,
    })
    r = evaluate_ai_execution_gate(bad)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


# ---------- CAUTION paths ----------


def test_caution_when_enable_ai_execution_already_true():
    """게이트 평가 시점에 이미 ENABLE_AI_EXECUTION=true 면 CAUTION."""
    inp = _make_ready_input()
    cautious = AIExecutionGateInput(**{**inp.__dict__, "enable_ai_execution": True})
    r = evaluate_ai_execution_gate(cautious)
    assert r.verdict is AIExecutionGateVerdict.CAUTION
    assert any("ENABLE_AI_EXECUTION" in c for c in r.cautions)


def test_caution_when_enable_live_trading_false():
    """ENABLE_LIVE_TRADING=false 상태도 정보성 CAUTION (활성화 PR 별도 필요)."""
    inp = _make_ready_input()
    cautious = AIExecutionGateInput(**{**inp.__dict__, "enable_live_trading": False})
    r = evaluate_ai_execution_gate(cautious)
    assert r.verdict is AIExecutionGateVerdict.CAUTION
    assert any("ENABLE_LIVE_TRADING" in c for c in r.cautions)


def test_caution_when_time_window_outside_recommended():
    inp = _make_ready_input()
    cautious = AIExecutionGateInput(**{
        **inp.__dict__,
        "window_start_kst": time(9, 0),     # 시가 전
        "window_end_kst":   time(15, 20),   # 동시호가 들어감
    })
    r = evaluate_ai_execution_gate(cautious)
    assert r.verdict is AIExecutionGateVerdict.CAUTION


# ---------- threshold override ----------


def test_threshold_override_changes_outcome():
    inp = _make_ready_input()
    strict = AIExecutionGateThresholds(max_order_notional_krw=10_000)
    r = evaluate_ai_execution_gate(inp, strict)
    assert r.verdict is AIExecutionGateVerdict.BLOCKED


# ---------- markdown ----------


def test_markdown_report_contains_disclaimers_and_no_buy_sell():
    r = evaluate_ai_execution_gate(_make_ready_input())
    text = render_markdown_report(r)
    assert "실제 활성화가 아니다" in text
    assert "별도 옵트인 PR" in text
    assert "선물 AI Execution" in text or "선물 AI" in text
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal", "HOLD signal"]:
        assert banned not in text


def test_markdown_report_lists_blocked_reasons():
    inp = _make_ready_input()
    bad = AIExecutionGateInput(**{**inp.__dict__, "user_explicit_opt_in": False})
    r = evaluate_ai_execution_gate(bad)
    text = render_markdown_report(r)
    assert "BLOCKED" in text
    assert "opt-in" in text


# ---------- policy summary ----------


def test_policy_summary_marks_futures_disallowed():
    p = get_policy_summary()
    assert p["futures_allowed"] is False
    assert p["activation_requires_separate_pr"] is True
    assert "promotion_gate" in p["required_gates"]
    assert "paper_gate" in p["required_gates"]
    assert "ai_assist_gate" in p["required_gates"]
    assert "live_manual_gate" in p["required_gates"]
    assert "kill_switch_ready" in p["required_infrastructure"]


# ---------- API ----------


def test_route_evaluate_returns_ready_for_review(client):
    body = {
        "strategy_name":            "ai_signals",
        "promotion_gate_passed":    True,
        "paper_gate_passed":        True,
        "ai_assist_gate_passed":    True,
        "live_manual_gate_passed":  True,
        "user_explicit_opt_in":     True,
        "enable_live_trading":      True,
        "enable_ai_execution":      False,
        "enable_futures_live_trading": False,
        "live_manual_days":         30,
        "ai_assist_days":           30,
        "risk_manager_active":      True,
        "order_guard_active":       True,
        "ai_permission_gate_active": True,
        "audit_log_complete":       True,
        "kill_switch_ready":        True,
        "circuit_breaker_configured": True,
        "current_max_order_notional_krw": 20000,
        "current_max_daily_loss_krw":     4000,
        "current_max_daily_order_count":  8,
        "current_max_open_positions":     2,
        "allowed_symbols": ["005930", "000660"],
        "explicit_time_window_set": True,
        "window_start_kst": "09:30",
        "window_end_kst":   "14:30",
        "ai_confidence_threshold":  80,
        "signal_quality_threshold": 75,
    }
    res = client.post("/api/governance/ai-execution-gate/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "READY_FOR_REVIEW"
    assert data["is_live_authorization"] is False
    assert data["is_order_signal"] is False
    assert data["is_investment_advice"] is False
    assert data["futures_allowed"] is False
    assert data["live_flag_changed"] is False


def test_route_evaluate_returns_blocked_for_futures_target(client):
    body = {
        "strategy_name":            "ai_signals",
        "promotion_gate_passed":    True,
        "paper_gate_passed":        True,
        "ai_assist_gate_passed":    True,
        "live_manual_gate_passed":  True,
        "user_explicit_opt_in":     True,
        "futures_target":           True,
        "live_manual_days":         30,
        "ai_assist_days":           30,
        "risk_manager_active":      True,
        "order_guard_active":       True,
        "ai_permission_gate_active": True,
        "audit_log_complete":       True,
        "kill_switch_ready":        True,
        "circuit_breaker_configured": True,
        "current_max_order_notional_krw": 20000,
        "current_max_daily_loss_krw":     4000,
        "current_max_daily_order_count":  8,
        "current_max_open_positions":     2,
        "allowed_symbols": ["005930"],
        "explicit_time_window_set": True,
        "window_start_kst": "09:30",
        "window_end_kst":   "14:30",
        "ai_confidence_threshold":  80,
        "signal_quality_threshold": 75,
    }
    res = client.post("/api/governance/ai-execution-gate/evaluate", json=body)
    assert res.status_code == 200
    assert res.json()["verdict"] == "BLOCKED"


def test_route_policy_endpoint_returns_futures_disallowed(client):
    res = client.get("/api/governance/ai-execution-gate/policy")
    assert res.status_code == 200
    body = res.json()
    assert body["futures_allowed"] is False
    assert body["activation_requires_separate_pr"] is True


def test_route_response_does_not_leak_secrets(client):
    res = client.post(
        "/api/governance/ai-execution-gate/evaluate",
        json={"strategy_name": "x"},
    )
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- invariants — static grep guards ----------


_MODULE_PATH = Path("backend/app/governance/ai_execution_gate.py")


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_module_does_not_import_broker_or_executor():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_call_broker_or_route_order():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_module_does_not_write_to_db():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    write_patterns = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for needle in write_patterns:
        assert needle not in src, f"writes to DB: {needle!r}"


def test_module_does_not_read_settings():
    """안전 플래그 값을 *입력으로만* 받는다 — settings 직접 읽기 금지."""
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    for needle in forbidden:
        assert needle not in src, f"reads settings directly: {needle!r}"
