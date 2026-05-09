"""#54: Risk Auditor Agent tests.

Coverage:
- `RiskAuditorReport.is_order_signal=True` 시 ValueError
- `risk_score` 0~100 범위 검증
- `audit_risk()` deterministic
- 데이터 부족 시 GREEN + summary "no events" friendly fallback
- Risk event 감지: daily_loss / rejected_burst / duplicate / stale /
  broker_error / AI overconf / AI low_conf / emergency_flapping /
  agent_warn / margin_risk / futures_liquidation
- `audit_level` 결정: GREEN / YELLOW / ORANGE / RED
- `pause_trading_recommended` / `emergency_stop_recommended` 권고
- `recommended_stop_reason`이 EmergencyStopReason enum 값
- DB helpers (read-only)
- `RiskAuditorAgent` (#51 AgentBase 호환)
- `/api/agents/risk-auditor/report` + `/mock` endpoints
- 정적 가드: broker / executor / route_order / set_emergency_stop /
  외부 HTTP / DB write 호출 0건
- BUY/SELL/HOLD 결정 0건
- 중지권한 우선 — RED일 때 Auditor가 직접 토글 X (advisory만)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.risk_auditor import (
    AuditLevel,
    RiskAuditorAgent,
    RiskAuditorInput,
    RiskAuditorReport,
    RiskEvent,
    RiskEventSeverity,
    RiskEventType,
    audit_risk,
    load_recent_agent_decisions,
    load_recent_audit_rows,
    load_recent_emergency_events,
)
from app.risk.emergency_reasons import EmergencyStopReason


# ====================================================================
# 1. Output dataclass guards
# ====================================================================


def test_report_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        RiskAuditorReport(
            audit_level=AuditLevel.GREEN,
            risk_score=0,
            summary_lines=[],
            events=[],
            pause_trading_recommended=False,
            emergency_stop_recommended=False,
            recommended_stop_reason=None,
            is_order_signal=True,
        )


def test_report_rejects_invalid_risk_score():
    with pytest.raises(ValueError, match="risk_score"):
        RiskAuditorReport(
            audit_level=AuditLevel.GREEN,
            risk_score=150,
            summary_lines=[],
            events=[],
            pause_trading_recommended=False,
            emergency_stop_recommended=False,
            recommended_stop_reason=None,
        )


def test_no_events_emits_green_with_friendly_summary():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
    ))
    assert report.audit_level == AuditLevel.GREEN
    assert report.risk_score == 0
    assert report.is_order_signal is False
    assert report.pause_trading_recommended is False
    assert report.emergency_stop_recommended is False
    assert len(report.events) == 0
    # summary에 "정상" 안내.
    joined = " ".join(report.summary_lines)
    assert "정상" in joined or "no events" in joined.lower()
    # 응답 dict에 BUY/SELL/HOLD 키 없음.
    d = report.to_dict()
    forbidden = {"buy", "sell", "hold", "order", "side", "decision"}
    assert forbidden.isdisjoint(d.keys())


# ====================================================================
# 2. Daily loss detection
# ====================================================================


def test_daily_loss_critical_triggers_red_and_stop_recommendation():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        daily_realized_pnl=-200_000, max_daily_loss=200_000,  # 100%
    ))
    assert report.audit_level == AuditLevel.RED
    assert report.emergency_stop_recommended is True
    assert report.recommended_stop_reason == EmergencyStopReason.DAILY_LOSS_LIMIT
    assert any(e.type == RiskEventType.DAILY_LOSS_BREACH for e in report.events)


def test_daily_loss_high_triggers_orange_and_pause():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        daily_realized_pnl=-160_000, max_daily_loss=200_000,  # 80%
    ))
    assert report.audit_level == AuditLevel.ORANGE
    assert report.pause_trading_recommended is True
    assert report.emergency_stop_recommended is False


def test_daily_loss_zero_max_disables_check():
    """max_daily_loss=0이면 검사 비활성 — 음수 PnL이 있어도 GREEN."""
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        daily_realized_pnl=-1_000_000, max_daily_loss=0,
    ))
    assert report.audit_level == AuditLevel.GREEN


def test_positive_pnl_does_not_trigger_loss_event():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        daily_realized_pnl=500_000, max_daily_loss=200_000,
    ))
    # 양수 PnL — 손실 이벤트 없음.
    assert not any(
        e.type == RiskEventType.DAILY_LOSS_BREACH for e in report.events
    )


# ====================================================================
# 3. Audit row analysis
# ====================================================================


class _FakeRow:
    """OrderAuditLog와 같은 attribute를 가진 fake — DB 의존 없는 단위 테스트."""
    def __init__(self, **kw):
        defaults = dict(
            id=1, decision="APPROVED", reasons=[], message="",
            requested_by_ai=False, signal_confidence=None,
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


def test_rejected_burst_warn():
    rows = [_FakeRow(id=i, decision="REJECTED", reasons=["x"]) for i in range(5)]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert any(
        e.type == RiskEventType.REPEATED_ORDER_FAILURE for e in report.events
    )


def test_rejected_high_count_triggers_orange():
    rows = [_FakeRow(id=i, decision="REJECTED", reasons=["x"]) for i in range(10)]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert any(
        e.type == RiskEventType.ABNORMAL_REJECTION_RATE for e in report.events
    )
    assert report.audit_level in (AuditLevel.ORANGE, AuditLevel.RED)


def test_duplicate_burst_detection():
    rows = [
        _FakeRow(id=i, decision="REJECTED",
                  reasons=["duplicate fingerprint blocked"])
        for i in range(3)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert any(
        e.type == RiskEventType.DUPLICATE_ORDER_BURST for e in report.events
    )


def test_stale_data_triggers_event_and_pause():
    rows = [
        _FakeRow(id=i, decision="REJECTED",
                  reasons=["stale price (60s+ old)"])
        for i in range(2)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    stale_event = next(
        e for e in report.events if e.type == RiskEventType.DATA_STALE
    )
    assert stale_event.severity == RiskEventSeverity.HIGH
    assert report.pause_trading_recommended is True


def test_stale_data_critical_when_count_over_3():
    rows = [
        _FakeRow(id=i, decision="REJECTED",
                  reasons=["stale price detected"])
        for i in range(4)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert report.emergency_stop_recommended is True
    assert report.recommended_stop_reason == EmergencyStopReason.DATA_STALE


def test_broker_error_burst():
    rows = [
        _FakeRow(id=i, decision="REJECTED", reasons=[],
                  message="broker error: timeout connecting")
        for i in range(3)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert any(
        e.type == RiskEventType.BROKER_ERROR_BURST for e in report.events
    )


def test_ai_overconfidence_burst():
    rows = [
        _FakeRow(id=i, decision="REJECTED", reasons=["x"],
                  requested_by_ai=True, signal_confidence=90)
        for i in range(3)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert any(
        e.type == RiskEventType.AI_OVERCONFIDENCE for e in report.events
    )


def test_ai_low_confidence_burst_is_info():
    rows = [
        _FakeRow(id=i, decision="APPROVED", reasons=[],
                  requested_by_ai=True, signal_confidence=15)
        for i in range(5)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    ai_low = next(
        e for e in report.events
        if e.type == RiskEventType.AI_LOW_CONFIDENCE_BURST
    )
    assert ai_low.severity == RiskEventSeverity.INFO


# ====================================================================
# 4. Emergency flapping + agent warn
# ====================================================================


def test_emergency_stop_flapping_triggers_event():
    fake_events = [
        _FakeRow(id=i, enabled=(i % 2 == 0), reason_code=None, level=None)
        for i in range(4)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=fake_events, agent_decisions=[],
    ))
    assert any(
        e.type == RiskEventType.EMERGENCY_STOP_FLAPPING for e in report.events
    )


def test_agent_warn_burst():
    fake_decisions = [
        _FakeRow(id=i, decision="WARN", agent_name="x", reasons=[], meta=None)
        for i in range(5)
    ]
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=fake_decisions,
    ))
    assert any(
        e.type == RiskEventType.AGENT_WARN_BURST for e in report.events
    )


# ====================================================================
# 5. Margin / liquidation risk (선물)
# ====================================================================


def test_margin_risk_critical():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        margin_risk_pct=60.0,
    ))
    assert report.audit_level == AuditLevel.RED
    assert report.recommended_stop_reason == EmergencyStopReason.MARGIN_RISK


def test_margin_risk_high():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        margin_risk_pct=35.0,
    ))
    assert report.audit_level == AuditLevel.ORANGE
    assert report.pause_trading_recommended is True


def test_liquidation_distance_critical():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        futures_liquidation_pct=2.0,
    ))
    assert report.audit_level == AuditLevel.RED
    assert (report.recommended_stop_reason
            == EmergencyStopReason.FUTURES_LIQUIDATION_RISK)


def test_liquidation_distance_warning():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
        futures_liquidation_pct=5.0,
    ))
    assert report.audit_level == AuditLevel.ORANGE


# ====================================================================
# 6. Risk score + audit_level matrix
# ====================================================================


def test_risk_score_clamped_to_100():
    """과다 이벤트 입력 — 점수는 100으로 cap."""
    rows = [_FakeRow(id=i, decision="REJECTED",
                       reasons=["stale price"], message="")
              for i in range(20)]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
        daily_realized_pnl=-500_000, max_daily_loss=200_000,
    ))
    assert 0 <= report.risk_score <= 100


def test_audit_level_yellow_when_only_warn():
    """WARN 이벤트만 있으면 YELLOW + no pause/stop."""
    rows = [_FakeRow(id=i, decision="REJECTED", reasons=["x"])
              for i in range(5)]  # WARN — REPEATED_ORDER_FAILURE
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    assert report.audit_level == AuditLevel.YELLOW
    assert report.pause_trading_recommended is False
    assert report.emergency_stop_recommended is False


def test_summary_includes_disclaimer():
    report = audit_risk(RiskAuditorInput(
        audit_rows=[], emergency_events=[], agent_decisions=[],
    ))
    joined = " ".join(report.summary_lines)
    assert "주문 신호가 아닙니다" in joined or "advisory" in joined.lower()


# ====================================================================
# 7. 중지권한 우선 — Auditor는 직접 토글 X
# ====================================================================


def test_red_level_does_not_auto_toggle_emergency_stop():
    """audit_level=RED + emergency_stop_recommended=True여도, 본 함수는
    어떤 부수효과도 만들지 않는다 (순수 dataclass 반환)."""
    rows = [_FakeRow(id=i, decision="REJECTED",
                       reasons=["stale price"], message="")
              for i in range(5)]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
        daily_realized_pnl=-300_000, max_daily_loss=200_000,
    ))
    assert report.audit_level == AuditLevel.RED
    assert report.emergency_stop_recommended is True
    # 본 report는 advisory dataclass — 호출 후 어떤 외부 상태 변경도 없음.
    # (caller가 운영자/Kill Switch UI에서 수동 토글)


def test_recommended_stop_reason_is_valid_enum():
    rows = [_FakeRow(id=i, decision="REJECTED",
                       reasons=["stale price"], message="")
              for i in range(5)]
    report = audit_risk(RiskAuditorInput(
        audit_rows=rows, emergency_events=[], agent_decisions=[],
    ))
    if report.recommended_stop_reason is not None:
        assert isinstance(report.recommended_stop_reason, EmergencyStopReason)


# ====================================================================
# 8. DB helpers (read-only)
# ====================================================================


def test_db_helpers_return_empty_for_fresh_session(client):
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    with client.test_db_factory() as db:
        assert load_recent_audit_rows(db, since=since) == []
        assert load_recent_emergency_events(db, since=since) == []
        assert load_recent_agent_decisions(db, since=since) == []


def test_db_helper_loads_seeded_audit_rows(client):
    from datetime import datetime, timedelta, timezone
    from app.db.models import OrderAuditLog
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    with client.test_db_factory() as db:
        db.add(OrderAuditLog(
            mode="SIMULATION", requested_by_ai=False,
            symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", limit_price=None,
            latest_price=70_000, decision="REJECTED",
            reasons=["mock reject"], executed=False,
        ))
        db.commit()
        rows = load_recent_audit_rows(db, since=since)
        assert len(rows) == 1
        assert rows[0].decision == "REJECTED"


# ====================================================================
# 9. RiskAuditorAgent (#51 AgentBase 호환)
# ====================================================================


def test_agent_metadata_marks_no_execute():
    agent = RiskAuditorAgent()
    md = agent.metadata
    assert md.role == AgentRole.RISK_AUDITOR
    assert md.can_execute_order is False
    forbidden_text = " ".join(md.forbidden)
    assert "BUY" in forbidden_text or "주문 신호" in forbidden_text
    assert "broker" in forbidden_text
    assert "set_emergency_stop" in forbidden_text or "토글" in forbidden_text


def test_agent_run_with_empty_input_returns_observe():
    agent = RiskAuditorAgent()
    out = agent.run(AgentContext())
    assert out.role == AgentRole.RISK_AUDITOR
    assert out.decision == AgentDecision.OBSERVE
    assert out.is_order_intent is False
    assert out.can_execute_order is False


def test_agent_run_with_critical_input_returns_reject():
    agent = RiskAuditorAgent()
    rows = [_FakeRow(id=i, decision="REJECTED",
                       reasons=["stale price"], message="")
              for i in range(5)]
    out = agent.run(AgentContext(extra={
        "risk_auditor_input": RiskAuditorInput(
            audit_rows=rows, emergency_events=[], agent_decisions=[],
            daily_realized_pnl=-300_000, max_daily_loss=200_000,
        )
    }))
    assert out.decision == AgentDecision.REJECT
    assert "emergency_stop_recommended" in out.risk_flags


# ====================================================================
# 10. /api/agents/risk-auditor endpoints
# ====================================================================


def test_api_report_empty_returns_green(client):
    res = client.get("/api/agents/risk-auditor/report")
    assert res.status_code == 200
    body = res.json()
    assert body["is_order_signal"] is False
    assert body["audit_level"] == "GREEN"
    assert body["risk_score"] == 0


def test_api_report_does_not_mutate_db(client):
    from app.db.models import OrderAuditLog, PendingApproval, EmergencyStopEvent
    client.get("/api/agents/risk-auditor/report?window_seconds=3600")
    with client.test_db_factory() as db:
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []
        assert db.execute(select(EmergencyStopEvent)).scalars().all() == []


def test_api_mock_simulates_red_with_critical_inputs(client):
    res = client.post("/api/agents/risk-auditor/mock", json={
        "rejected_count": 5,
        "stale_rejected_count": 4,    # critical → STOP
        "daily_realized_pnl": -300_000,
        "max_daily_loss": 200_000,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["is_order_signal"] is False
    assert body["audit_level"] == "RED"
    assert body["emergency_stop_recommended"] is True


def test_api_mock_simulates_orange_with_high_inputs(client):
    res = client.post("/api/agents/risk-auditor/mock", json={
        "rejected_count": 10,
    })
    body = res.json()
    assert body["audit_level"] in ("ORANGE", "RED")  # rejected_count=10 → high


def test_api_mock_does_not_mutate_db(client):
    from app.db.models import OrderAuditLog, EmergencyStopEvent
    client.post("/api/agents/risk-auditor/mock", json={
        "stale_rejected_count": 5, "rejected_count": 5,
    })
    with client.test_db_factory() as db:
        # API가 row를 만들지 않음.
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(EmergencyStopEvent)).scalars().all() == []


# ====================================================================
# 11. Static guards
# ====================================================================


def test_module_does_not_import_broker_or_executor():
    import app.agents.risk_auditor as mod
    src_path = mod.__file__
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
            f"app.agents.risk_auditor must not contain '{snippet}'"
        )


def test_module_does_not_call_set_emergency_stop():
    """Risk Auditor는 emergency_stop을 *직접 토글하지 않는다* — 권고만.

    docstring에 정책 설명으로 등장하는 것은 OK. 실제 코드 호출 / attribute
    할당이 발견되면 차단."""
    import app.agents.risk_auditor as mod
    src_path = mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    # 코드 호출 패턴만 검사 — docstring의 정책 설명("POST /api/risk/emergency-stop
    # 호출 X" 같은 문장)은 OK.
    forbidden_calls = (
        ".set_emergency_stop(",
        "risk.emergency_stop = True",     # 직접 attribute 할당 (정책 설명 X)
        "risk.emergency_stop=True",
        "self.emergency_stop = True",
    )
    for snippet in forbidden_calls:
        assert snippet not in src, (
            f"risk_auditor must not toggle emergency stop directly: '{snippet}'"
        )


def test_module_does_not_emit_db_writes():
    import app.agents.risk_auditor as mod
    src_path = mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden_calls = (
        "db.add(", "db.add_all(", "db.delete(", "db.commit(",
        ".update().where", "session.add(", "session.add_all(",
    )
    for snippet in forbidden_calls:
        assert snippet not in src, (
            f"risk_auditor must not contain DB write '{snippet}'"
        )


def test_module_does_not_import_external_http_or_ai():
    import app.agents.risk_auditor as mod
    src_path = mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "import httpx",
        "import requests",
        "from anthropic",
        "import anthropic",
        "import openai",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"risk_auditor must not import '{snippet}'"
        )


def test_no_buy_sell_hold_in_module_logic():
    """RiskEventType / AuditLevel enum에 BUY/SELL/HOLD 값 없음."""
    from app.agents.risk_auditor import AuditLevel, RiskEventType
    audit_values = {e.value for e in AuditLevel}
    event_values = {e.value for e in RiskEventType}
    forbidden = {"BUY", "SELL", "HOLD"}
    assert forbidden.isdisjoint(audit_values)
    assert forbidden.isdisjoint(event_values)
