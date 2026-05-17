"""체크리스트 #64: /api/notifications/* 라우트 테스트.

invariant:
  - GET /status는 token / chat_id 미포함
  - POST /test는 NOTIFICATIONS_ENABLED=false에서 NoOp skip 반환 (broker 호출 0건)
  - POST /mock-event는 dry_run 기본이라 외부 호출 없음
  - 알림 실패가 200 응답을 깨지 않음 (SendResult로 carry)
"""

from __future__ import annotations

import json


def test_get_status_returns_secret_free_envelope(client):
    r = client.get("/api/notifications/status")
    assert r.status_code == 200
    body = r.json()
    # 필수 키
    for key in ["enabled", "channel", "channel_configured",
                "telegram_configured", "min_severity",
                "min_severity_name", "dedupe_window_seconds",
                "always_send_critical", "notice"]:
        assert key in body, f"missing key: {key}"
    # token / chat_id는 응답 어디에도 없음
    serialized = json.dumps(body)
    assert "telegram_bot_token" not in serialized
    assert "telegram_chat_id" not in serialized
    # 안내 문구
    assert "backend/.env" in body["notice"]


def test_get_status_when_disabled_reports_noop(client):
    r = client.get("/api/notifications/status")
    body = r.json()
    # 기본은 disabled — channel은 noop, telegram_configured는 false
    assert body["enabled"] is False
    assert body["channel"] == "noop"
    assert body["telegram_configured"] is False


def test_post_test_skips_silently_when_disabled(client):
    r = client.post("/api/notifications/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # disabled 또는 noop_channel skip
    assert body["skipped_reason"] in (
        "notifications_disabled", "noop_channel", "below_min_severity",
    )


def test_post_mock_event_emergency_stop_dry_run(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "emergency_stop",
        "enabled": True,
        "level": "LEVEL_1",
        "reason_code": "manual_operator",
        "decided_by": "ops1",
        "dry_run": True,
    })
    assert r.status_code == 200
    body = r.json()
    # noop_channel skip
    assert body["ok"] is True


def test_post_mock_event_rejects_unknown_kind(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "definitely_not_a_kind",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "unknown kind" in (body.get("error") or "")


def test_post_mock_event_data_stale(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "data_stale", "symbol": "005930", "age_seconds": 120,
        "dry_run": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_post_mock_event_daily_loss_warning(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "daily_loss_warning",
        "current_loss": -180_000, "limit": 200_000, "pct": 90,
        "dry_run": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_post_mock_event_broker_error(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "broker_error",
        "broker": "kis", "operation": "get_balance",
        "message": "timeout after 5s", "dry_run": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_post_mock_event_margin_risk(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "margin_risk",
        "used_pct": 92.0, "liquidation_distance_pct": 2.0,
        "dry_run": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_post_mock_event_approval_pending(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "approval_pending",
        "approval_id": 17, "symbol": "005930", "side": "BUY",
        "quantity": 5, "strategy": "sma_crossover",
        "requested_by_ai": True, "dry_run": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_post_mock_event_repeated_rejection(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "repeated_rejection",
        "count": 7, "window_seconds": 60, "threshold": 3,
        "dry_run": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_post_mock_event_risk_auditor_warn(client):
    r = client.post("/api/notifications/mock-event", json={
        "kind": "risk_auditor_warn",
        "audit_level": "RED", "risk_score": 85,
        "summary": "high rejection rate",
        "dry_run": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ====================================================================
# Emergency stop API hook — 알림 실패가 응답을 깨지 않는 invariant
# ====================================================================


def test_emergency_stop_toggle_works_when_notifications_disabled(client):
    """notifications_enabled=false (default)여도 emergency_stop 응답은 정상."""
    r = client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops1",
        "reason_code": "manual_operator", "level": "LEVEL_1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["emergency_stop"] is True
    assert body["level"] == "LEVEL_1"


def test_emergency_stop_toggle_does_not_raise_even_if_notification_path_fails(
    client, monkeypatch,
):
    """알림 hook은 try/except로 감싸 있어야 한다 — 강제 raise해도 200."""

    def bad_build_event(**kwargs):  # noqa: ARG001
        raise RuntimeError("notification builder crashed")

    # 동적 import 경로를 직접 patch — routes_risk.set_emergency_stop 안에서
    # `from app.notifications import build_emergency_stop_event`이 호출됨.
    monkeypatch.setattr(
        "app.notifications.build_emergency_stop_event", bad_build_event,
    )

    r = client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops1",
        "reason_code": "manual_operator",
    })
    # 알림 실패에도 200 응답 유지
    assert r.status_code == 200
    assert r.json()["emergency_stop"] is True
