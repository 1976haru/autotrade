"""AI Assist Gate (#74) tests — evaluator + collector + API + invariants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import (
    EmergencyStopEvent,
    OrderAuditLog,
    PendingApproval,
)
from app.governance.ai_assist_gate import (
    AIAssistFailureReason,
    AIAssistGateInput,
    AIAssistGateResult,
    AIAssistGateThresholds,
    AIAssistGateVerdict,
    evaluate_ai_assist_gate,
    render_markdown_report,
)
from app.governance.ai_assist_gate_collector import (
    build_ai_assist_gate_input,
    list_ai_assist_strategies,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_pass_input() -> AIAssistGateInput:
    end = _utcnow()
    return AIAssistGateInput(
        strategy_name="ai_signals",
        period_start=end - timedelta(days=30),
        period_end=end,
        proposal_count=150,
        approved_proposals=80,
        risk_rejected_proposals=30,
        operator_rejected_proposals=30,
        expired_or_cancelled=10,
        approved_expectancy=250.0,
        approved_winning_pnl_sum=200_000,
        approved_losing_pnl_sum=120_000,
        approved_win_count=50,
        approved_loss_count=30,
        confidence_calibration=0.75,
        avg_confidence=72.0,
        rejected_but_would_have_won=5,
        ai_decision_audit_drift=0,
        emergency_stops_in_period=0,
        active_days=22,
        failure_reason_counts={
            "operator_rejected": 25,
            "risk_limit": 20,
            "data_stale": 18,
            "regime_mismatch": 12,
        },
    )


# ---------- DTO invariants ----------


def test_result_rejects_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        AIAssistGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=AIAssistGateVerdict.PASS,
            is_live_authorization=True,
        )


def test_result_rejects_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        AIAssistGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=AIAssistGateVerdict.PASS,
            is_order_signal=True,
        )


def test_result_rejects_investment_advice_true():
    with pytest.raises(ValueError, match="is_investment_advice"):
        AIAssistGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=AIAssistGateVerdict.PASS,
            is_investment_advice=True,
        )


def test_to_dict_has_invariant_flags():
    r = evaluate_ai_assist_gate(_make_pass_input())
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["is_investment_advice"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- evaluator ----------


def test_pass_when_all_criteria_met():
    r = evaluate_ai_assist_gate(_make_pass_input())
    assert r.verdict is AIAssistGateVerdict.PASS
    assert not r.failed_criteria
    assert "LIVE_AI_EXECUTION" in r.next_step


def test_fail_when_proposal_count_under_100():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{**inp.__dict__, "proposal_count": 30})
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL
    assert any("AI 제안" in c for c in r.failed_criteria)


def test_fail_when_period_under_28_days():
    inp = _make_pass_input()
    short = AIAssistGateInput(**{
        **inp.__dict__,
        "period_start": inp.period_end - timedelta(days=10),
    })
    r = evaluate_ai_assist_gate(short)
    assert r.verdict is AIAssistGateVerdict.FAIL


def test_fail_when_expectancy_zero_or_negative():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{**inp.__dict__, "approved_expectancy": -10.0})
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL
    assert any("expectancy" in c for c in r.failed_criteria)


def test_fail_when_approved_loss_rate_high():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{
        **inp.__dict__,
        "approved_win_count": 10,
        "approved_loss_count": 70,
    })
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL
    assert any("손실" in c for c in r.failed_criteria)


def test_fail_when_risk_rejection_high():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{
        **inp.__dict__,
        "proposal_count": 100,
        "risk_rejected_proposals": 80,
    })
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL


def test_fail_when_operator_rejection_high():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{
        **inp.__dict__,
        "proposal_count": 100,
        "operator_rejected_proposals": 70,
    })
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL


def test_fail_when_confidence_calibration_low():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{**inp.__dict__, "confidence_calibration": 0.2})
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL


def test_fail_when_audit_drift_present():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{**inp.__dict__, "ai_decision_audit_drift": 1})
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL


def test_fail_when_too_many_emergency_stops():
    inp = _make_pass_input()
    bad = AIAssistGateInput(**{**inp.__dict__, "emergency_stops_in_period": 5})
    r = evaluate_ai_assist_gate(bad)
    assert r.verdict is AIAssistGateVerdict.FAIL


def test_caution_when_rejected_but_would_have_won_high():
    inp = _make_pass_input()
    cautious = AIAssistGateInput(**{
        **inp.__dict__,
        "risk_rejected_proposals": 20,
        "operator_rejected_proposals": 20,
        "rejected_but_would_have_won": 20,  # 50% of rejected
    })
    r = evaluate_ai_assist_gate(cautious)
    assert r.verdict is AIAssistGateVerdict.CAUTION


def test_caution_when_failure_reason_concentrated():
    inp = _make_pass_input()
    cautious = AIAssistGateInput(**{
        **inp.__dict__,
        "failure_reason_counts": {"data_stale": 80, "risk_limit": 10},
    })
    r = evaluate_ai_assist_gate(cautious)
    assert r.verdict is AIAssistGateVerdict.CAUTION
    assert any("실패 사유" in c for c in r.cautions)


def test_caution_when_calibration_moderate_but_above_min():
    inp = _make_pass_input()
    cautious = AIAssistGateInput(**{**inp.__dict__, "confidence_calibration": 0.55})
    r = evaluate_ai_assist_gate(cautious)
    assert r.verdict is AIAssistGateVerdict.CAUTION


def test_caution_when_expired_rate_high():
    inp = _make_pass_input()
    cautious = AIAssistGateInput(**{
        **inp.__dict__,
        "proposal_count": 100,
        "expired_or_cancelled": 50,
    })
    r = evaluate_ai_assist_gate(cautious)
    assert r.verdict is AIAssistGateVerdict.CAUTION


def test_threshold_override_changes_outcome():
    inp = _make_pass_input()
    strict = AIAssistGateThresholds(min_proposal_count=500)
    r = evaluate_ai_assist_gate(inp, strict)
    assert r.verdict is AIAssistGateVerdict.FAIL


# ---------- markdown ----------


def test_markdown_report_contains_disclaimers_and_no_buy_sell():
    r = evaluate_ai_assist_gate(_make_pass_input())
    text = render_markdown_report(r)
    assert "투자 조언이 아니라" in text
    assert "AI 자동매매" in text
    assert "실거래 허가가 아닙니다" in text
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal", "HOLD"]:
        assert banned not in text


def test_markdown_report_lists_failure_reason_table():
    inp = _make_pass_input()
    r = evaluate_ai_assist_gate(inp)
    text = render_markdown_report(r)
    assert "Failure Reason" in text
    assert "operator_rejected" in text


def test_failure_reason_enum_has_no_order_signals():
    """advisory tag만 — BUY/SELL/HOLD 0개."""
    values = {m.value.upper() for m in AIAssistFailureReason}
    for banned in ("BUY", "SELL", "HOLD"):
        assert banned not in values, f"forbidden enum value: {banned}"


# ---------- collector ----------


def _add_ai_assist_audit(
    db,
    *,
    decision="NEEDS_APPROVAL",
    executed=False,
    minutes_ago=5,
    confidence=70,
    strategy="ai_signals",
    reasons=None,
):
    """AI Assist 흐름의 audit row 추가."""
    row = OrderAuditLog(
        created_at=_utcnow() - timedelta(minutes=minutes_ago),
        mode="LIVE_AI_ASSIST", requested_by_ai=True,
        symbol="005930", side="BUY", quantity=10,
        order_type="MARKET", latest_price=70_000,
        decision=decision, reasons=reasons or [],
        trade_reason="ai_assist",
        executed=executed,
        filled_quantity=10 if executed else 0,
        message="ai",
        strategy=strategy,
        ai_decision_meta={"confidence": confidence, "source": "AI_ASSIST"},
    )
    db.add(row)
    db.commit()
    return row


def test_collector_counts_proposals_and_decisions(client):
    db = client.test_db_factory()
    try:
        # APPROVED via queue.
        a1 = _add_ai_assist_audit(db, decision="NEEDS_APPROVAL")
        db.add(PendingApproval(
            created_at=a1.created_at, audit_id=a1.id,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET",
            mode="LIVE_AI_ASSIST", status="APPROVED",
        ))
        # REJECTED by operator.
        a2 = _add_ai_assist_audit(db, decision="NEEDS_APPROVAL")
        db.add(PendingApproval(
            created_at=a2.created_at, audit_id=a2.id,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET",
            mode="LIVE_AI_ASSIST", status="REJECTED",
        ))
        # EXPIRED.
        a3 = _add_ai_assist_audit(db, decision="NEEDS_APPROVAL")
        db.add(PendingApproval(
            created_at=a3.created_at, audit_id=a3.id,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET",
            mode="LIVE_AI_ASSIST", status="EXPIRED",
        ))
        # RiskManager pre-rejected (no PA row).
        _add_ai_assist_audit(db, decision="REJECTED", reasons=["max_daily_loss exceeded"])
        # 다른 모드 / non-AI row — 제외 대상.
        db.add(OrderAuditLog(
            created_at=_utcnow(),
            mode="PAPER", requested_by_ai=False,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET", latest_price=70_000,
            decision="APPROVED", reasons=[], executed=True,
            filled_quantity=10, message="paper",
            strategy="ai_signals",
        ))
        db.commit()

        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=30)
        inp = build_ai_assist_gate_input(
            db, strategy="ai_signals",
            period_start=start, period_end=end,
            approved_expectancy=200, approved_win_count=1, approved_loss_count=0,
        )
        assert inp.proposal_count == 4
        assert inp.approved_proposals == 1
        assert inp.operator_rejected_proposals == 1
        assert inp.expired_or_cancelled == 1
        assert inp.risk_rejected_proposals == 1
    finally:
        db.close()


def test_collector_tags_failure_reasons(client):
    db = client.test_db_factory()
    try:
        _add_ai_assist_audit(
            db, decision="REJECTED", reasons=["stale quote price"],
        )
        _add_ai_assist_audit(
            db, decision="REJECTED", reasons=["max_daily_loss exceeded"],
        )
        a3 = _add_ai_assist_audit(db, decision="NEEDS_APPROVAL")
        db.add(PendingApproval(
            created_at=a3.created_at, audit_id=a3.id,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET",
            mode="LIVE_AI_ASSIST", status="REJECTED",
        ))
        db.commit()

        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=30)
        inp = build_ai_assist_gate_input(
            db, strategy=None,
            period_start=start, period_end=end,
        )
        tags = inp.failure_reason_counts
        assert tags.get("data_stale", 0) >= 1
        assert tags.get("risk_limit", 0) >= 1
        assert tags.get("operator_rejected", 0) >= 1
    finally:
        db.close()


def test_collector_counts_emergency_stop_events(client):
    db = client.test_db_factory()
    try:
        db.add(EmergencyStopEvent(
            created_at=_utcnow() - timedelta(hours=1),
            enabled=True, decided_by="op", note="t",
        ))
        db.commit()
        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=1)
        inp = build_ai_assist_gate_input(
            db, strategy=None,
            period_start=start, period_end=end,
        )
        assert inp.emergency_stops_in_period == 1
    finally:
        db.close()


def test_list_ai_assist_strategies(client):
    db = client.test_db_factory()
    try:
        _add_ai_assist_audit(db, strategy="ai_signals")
        _add_ai_assist_audit(db, strategy="ai_breakout")
        # 다른 모드 row — 제외.
        db.add(OrderAuditLog(
            created_at=_utcnow(),
            mode="PAPER", requested_by_ai=False,
            symbol="X", side="BUY", quantity=1,
            order_type="MARKET", latest_price=1,
            decision="APPROVED", reasons=[], executed=True,
            filled_quantity=1, message="paper",
            strategy="non_ai",
        ))
        db.commit()
        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=30)
        names = list_ai_assist_strategies(db, period_start=start, period_end=end)
        assert names == ["ai_breakout", "ai_signals"]
    finally:
        db.close()


# ---------- API ----------


def test_route_evaluate_returns_pass(client):
    body = {
        "strategy_name":              "ai_signals",
        "proposal_count":             150,
        "approved_proposals":         80,
        "risk_rejected_proposals":    30,
        "operator_rejected_proposals": 30,
        "expired_or_cancelled":       10,
        "approved_expectancy":        250.0,
        "approved_winning_pnl_sum":   200_000,
        "approved_losing_pnl_sum":    120_000,
        "approved_win_count":         50,
        "approved_loss_count":        30,
        "confidence_calibration":     0.75,
        "active_days":                22,
    }
    res = client.post("/api/governance/ai-assist-gate/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "PASS"
    assert data["is_live_authorization"] is False
    assert data["is_order_signal"] is False
    assert data["is_investment_advice"] is False


def test_route_evaluate_returns_fail_when_under_threshold(client):
    body = {
        "strategy_name":         "weak",
        "proposal_count":        50,
        "approved_proposals":    10,
        "risk_rejected_proposals": 30,
        "approved_expectancy":   -50.0,
        "approved_win_count":    2,
        "approved_loss_count":   8,
        "confidence_calibration": 0.3,
    }
    res = client.post("/api/governance/ai-assist-gate/evaluate", json=body)
    assert res.status_code == 200
    assert res.json()["verdict"] == "FAIL"


def test_route_response_does_not_leak_secrets(client):
    body = {"strategy_name": "x"}
    res = client.post("/api/governance/ai-assist-gate/evaluate", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- invariants — static grep guards ----------


_MODULE_PATHS = [
    Path("backend/app/governance/ai_assist_gate.py"),
    Path("backend/app/governance/ai_assist_gate_collector.py"),
    Path("scripts/evaluate_ai_assist_gate.py"),
]


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_ai_assist_gate_does_not_import_broker_or_executor():
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
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} imports forbidden: {needle!r}"
            )


def test_ai_assist_gate_does_not_call_broker_or_route_order():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} contains forbidden call: {needle!r}"
            )


def test_ai_assist_gate_does_not_mutate_safety_flags():
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} mutates safety flag: {needle!r}"
            )


def test_ai_assist_gate_modules_do_not_write_to_db():
    """SELECT only. evaluator + collector 만 검사."""
    write_patterns = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    modules = [
        Path("backend/app/governance/ai_assist_gate.py"),
        Path("backend/app/governance/ai_assist_gate_collector.py"),
    ]
    for rel in modules:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in write_patterns:
            assert needle not in src, (
                f"{rel} writes to DB: {needle!r}"
            )


def test_ai_assist_gate_modules_do_not_read_settings():
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    modules = [
        Path("backend/app/governance/ai_assist_gate.py"),
        Path("backend/app/governance/ai_assist_gate_collector.py"),
    ]
    for rel in modules:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} reads settings directly: {needle!r}"
            )
