"""Live Manual Gate (#73) tests — evaluator + collector + API + invariants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import (
    EmergencyStopEvent,
    OrderAuditLog,
    PendingApproval,
)
from app.governance.live_manual_gate import (
    LiveManualGateInput,
    LiveManualGateResult,
    LiveManualGateThresholds,
    LiveManualGateVerdict,
    evaluate_live_manual_gate,
    render_markdown_report,
)
from app.governance.live_manual_gate_collector import summarize_live_manual_period


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_pass_input() -> LiveManualGateInput:
    end = _utcnow()
    return LiveManualGateInput(
        strategy_name="sma_cross",
        period_start=end - timedelta(days=30),
        period_end=end,
        paper_gate_passed=True,
        promotion_gate_passed=True,
        user_explicit_opt_in=True,
        approval_required=True,
        ai_execution_enabled=False,
        futures_live_enabled=False,
        enable_live_trading=False,
        current_max_order_notional_krw=30_000,
        current_max_daily_loss_krw=8_000,
        current_max_open_positions=2,
        allowed_symbols=("005930", "000660"),
        operating_days=30,
        total_live_manual_orders=20,
        approved_orders=15,
        rejected_orders=3,
        expired_or_cancelled_orders=2,
        approval_bypass_attempts=0,
        audit_missing_count=0,
        system_errors=0,
    )


# ---------- DTO invariants ----------


def test_result_rejects_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        LiveManualGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=LiveManualGateVerdict.PASS,
            is_live_authorization=True,
        )


def test_result_rejects_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        LiveManualGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=LiveManualGateVerdict.PASS,
            is_order_signal=True,
        )


def test_to_dict_has_invariant_flags():
    r = evaluate_live_manual_gate(_make_pass_input())
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- evaluator ----------


def test_pass_when_all_criteria_met():
    r = evaluate_live_manual_gate(_make_pass_input())
    assert r.verdict is LiveManualGateVerdict.PASS
    assert not r.blocked_criteria
    assert "검토 가능" in r.next_step


def test_blocked_when_paper_gate_failed():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "paper_gate_passed": False})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("Paper Gate" in c for c in r.blocked_criteria)


def test_blocked_when_promotion_gate_failed():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "promotion_gate_passed": False})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("Promotion Gate" in c for c in r.blocked_criteria)


def test_blocked_when_user_opt_in_missing():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "user_explicit_opt_in": False})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("opt-in" in c for c in r.blocked_criteria)


def test_blocked_when_approval_not_required():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "approval_required": False})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("approval_required" in c for c in r.blocked_criteria)


def test_blocked_when_ai_execution_enabled():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "ai_execution_enabled": True})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("AI" in c for c in r.blocked_criteria)


def test_blocked_when_futures_live_enabled():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "futures_live_enabled": True})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("FUTURES" in c for c in r.blocked_criteria)


def test_blocked_when_order_notional_too_high():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{
        **inp.__dict__, "current_max_order_notional_krw": 1_000_000,
    })
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("주문 한도" in c for c in r.blocked_criteria)


def test_blocked_when_daily_loss_limit_too_high():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{
        **inp.__dict__, "current_max_daily_loss_krw": 100_000,
    })
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("손실한도" in c for c in r.blocked_criteria)


def test_blocked_when_max_positions_too_high():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "current_max_open_positions": 10})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED


def test_blocked_when_system_errors_present():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "system_errors": 1})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("시스템 오류" in c for c in r.blocked_criteria)


def test_blocked_when_audit_missing_present():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "audit_missing_count": 1})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED


def test_blocked_when_approval_bypass_attempts_present():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "approval_bypass_attempts": 1})
    r = evaluate_live_manual_gate(bad)
    assert r.verdict is LiveManualGateVerdict.BLOCKED
    assert any("우회" in c for c in r.blocked_criteria)


def test_caution_when_operating_days_below_min_but_other_criteria_met():
    inp = _make_pass_input()
    cautious = LiveManualGateInput(**{**inp.__dict__, "operating_days": 10})
    r = evaluate_live_manual_gate(cautious)
    assert r.verdict is LiveManualGateVerdict.CAUTION
    assert any("운영" in c for c in r.cautions)


def test_caution_when_enable_live_trading_true():
    inp = _make_pass_input()
    cautious = LiveManualGateInput(**{**inp.__dict__, "enable_live_trading": True})
    r = evaluate_live_manual_gate(cautious)
    assert r.verdict is LiveManualGateVerdict.CAUTION
    assert any("ENABLE_LIVE_TRADING" in c for c in r.cautions)


def test_threshold_override_changes_outcome():
    inp = _make_pass_input()
    strict = LiveManualGateThresholds(max_order_notional_krw=10_000)
    r = evaluate_live_manual_gate(inp, strict)
    assert r.verdict is LiveManualGateVerdict.BLOCKED


# ---------- markdown ----------


def test_markdown_report_contains_disclaimer_and_no_buy_sell():
    r = evaluate_live_manual_gate(_make_pass_input())
    text = render_markdown_report(r)
    assert "실거래 허가" in text
    assert "Live Manual Approval" in text
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal"]:
        assert banned not in text


def test_markdown_report_lists_blocked_reasons():
    inp = _make_pass_input()
    bad = LiveManualGateInput(**{**inp.__dict__, "user_explicit_opt_in": False})
    r = evaluate_live_manual_gate(bad)
    text = render_markdown_report(r)
    assert "BLOCKED" in text
    assert "opt-in" in text


# ---------- collector ----------


def _add_audit(db, *, decision, mode="LIVE_MANUAL_APPROVAL", executed=False,
               minutes_ago=10):
    row = OrderAuditLog(
        created_at=_utcnow() - timedelta(minutes=minutes_ago),
        mode=mode, requested_by_ai=False,
        symbol="005930", side="BUY", quantity=1,
        order_type="MARKET", latest_price=70_000,
        decision=decision, reasons=[], executed=executed,
        filled_quantity=1 if executed else 0,
        message="t",
    )
    db.add(row)
    db.commit()
    return row


def test_collector_counts_approved_rejected_needs_approval(client):
    db = client.test_db_factory()
    try:
        _add_audit(db, decision="APPROVED")
        _add_audit(db, decision="APPROVED")
        _add_audit(db, decision="REJECTED")
        _add_audit(db, decision="NEEDS_APPROVAL")
        # 다른 모드 row — 제외 대상.
        _add_audit(db, decision="APPROVED", mode="SIMULATION")
        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=1)
        s = summarize_live_manual_period(db, start_date=start, end_date=end)
        assert s["total_live_manual_orders"] == 4
        assert s["approved_orders"] == 2
        assert s["rejected_orders"] == 1
        assert s["needs_approval_orders"] == 1
    finally:
        db.close()


def test_collector_detects_approval_bypass(client):
    db = client.test_db_factory()
    try:
        # APPROVED + executed, 그리고 PendingApproval row 없음 → 우회 의심.
        _add_audit(db, decision="APPROVED", executed=True)
        # APPROVED + executed, PendingApproval 큐 거침 (정상).
        normal = _add_audit(db, decision="APPROVED", executed=True)
        db.add(PendingApproval(
            created_at=normal.created_at,
            audit_id=normal.id,
            symbol="005930", side="BUY", quantity=1,
            order_type="MARKET",
            mode="LIVE_MANUAL_APPROVAL",
            status="APPROVED",
        ))
        db.commit()
        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=1)
        s = summarize_live_manual_period(db, start_date=start, end_date=end)
        assert s["approval_bypass_attempts"] == 1
        assert s["approved_via_queue"] == 1
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
        s = summarize_live_manual_period(db, start_date=start, end_date=end)
        assert s["emergency_stops_in_period"] == 1
    finally:
        db.close()


# ---------- API ----------


def test_route_evaluate_returns_pass(client):
    body = {
        "strategy_name":        "sma_cross",
        "paper_gate_passed":    True,
        "promotion_gate_passed": True,
        "user_explicit_opt_in": True,
        "approval_required":    True,
        "current_max_order_notional_krw": 30_000,
        "current_max_daily_loss_krw":     8_000,
        "current_max_open_positions":     2,
        "allowed_symbols":      ["005930"],
        "operating_days":       30,
    }
    res = client.post("/api/governance/live-manual-gate/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "PASS"
    assert data["is_live_authorization"] is False
    assert data["live_flag_changed"] is False


def test_route_evaluate_blocks_without_opt_in(client):
    body = {
        "strategy_name":        "sma_cross",
        "paper_gate_passed":    True,
        "promotion_gate_passed": True,
        "user_explicit_opt_in": False,
        "approval_required":    True,
        "current_max_order_notional_krw": 30_000,
        "current_max_daily_loss_krw":     8_000,
        "current_max_open_positions":     2,
    }
    res = client.post("/api/governance/live-manual-gate/evaluate", json=body)
    assert res.status_code == 200
    assert res.json()["verdict"] == "BLOCKED"


def test_route_period_summary(client):
    db = client.test_db_factory()
    try:
        _add_audit(db, decision="APPROVED")
        end = _utcnow() + timedelta(minutes=1)
        start = end - timedelta(days=1)
    finally:
        db.close()

    res = client.get(
        "/api/governance/live-manual-gate/period-summary",
        params={
            "period_start": start.isoformat(),
            "period_end":   end.isoformat(),
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "total_live_manual_orders" in body


def test_route_response_does_not_leak_secrets(client):
    body = {"strategy_name": "x"}
    res = client.post("/api/governance/live-manual-gate/evaluate", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- invariants ----------


_MODULE_PATHS = [
    Path("backend/app/governance/live_manual_gate.py"),
    Path("backend/app/governance/live_manual_gate_collector.py"),
    Path("backend/app/api/routes_governance.py"),
]


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_live_manual_gate_does_not_import_broker_or_executor():
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
        "from app.ai.assist",
        "import httpx", "import requests",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            # routes_governance.py 는 다른 게이트 endpoint에서 위 모듈을 일부
            # *직접* import할 수 있지만, live_manual_gate.py / collector는 완전
            # 금지. 본 테스트는 live_manual_gate 모듈 두 개에 한해 엄격 검사.
            if "routes_governance" in str(rel) and needle in (
                "import httpx", "import requests",
            ):
                continue
            if "live_manual_gate" in str(rel):
                assert needle not in src, (
                    f"{rel} imports forbidden: {needle!r}"
                )


def test_live_manual_gate_does_not_call_broker_or_route_order():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} contains forbidden call: {needle!r}"
            )


def test_live_manual_gate_does_not_mutate_safety_flags():
    """안전 플래그 *값 변경* 만 차단. 문서 / message 문자열의 *언급*은 허용."""
    forbidden_assignments = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
        ".emergency_stop = True",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden_assignments:
            assert needle not in src, (
                f"{rel} mutates safety flag: {needle!r}"
            )


def test_live_manual_gate_modules_do_not_write_to_db():
    write_patterns = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    # collector / evaluator만 검사. routes_governance.py 는 다른 endpoint
    # (PaperGate 등)도 들어있어 본 테스트 대상 아님 — routes_governance 자체는
    # 다른 테스트(test_paper_gate)에서 검증됨. 본 테스트는 #73 신규 두 모듈만.
    modules = [
        Path("backend/app/governance/live_manual_gate.py"),
        Path("backend/app/governance/live_manual_gate_collector.py"),
    ]
    for rel in modules:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in write_patterns:
            assert needle not in src, (
                f"{rel} writes to DB: {needle!r}"
            )


def test_live_manual_gate_modules_do_not_read_settings():
    """본 모듈은 안전 플래그 값을 *입력으로만* 받는다 — settings를 직접 읽지 않음.

    위반 시 운영자가 입력으로 false 보냈는데 settings가 true를 가져오는 식의
    혼선 발생 가능. evaluator는 *입력에 명시된 값*만 평가.
    """
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    modules = [
        Path("backend/app/governance/live_manual_gate.py"),
        Path("backend/app/governance/live_manual_gate_collector.py"),
    ]
    for rel in modules:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} reads settings directly: {needle!r}"
            )
