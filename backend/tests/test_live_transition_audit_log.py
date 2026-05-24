"""5-05: Live 전환 감사 로그 (append-only) 테스트.

핵심 invariant:
- operator/reason 필수, action 표준 집합
- timestamp(created_at) 자동 생성, audit_id immutable
- 모든 snapshot(capital review/paper gate/canary gate/manual approval) 저장
- append-only: update/delete 메서드·API 없음, previous_audit_id 연결
- secret-like 값 → fail-closed (기록 거부)
- 기록해도 is_live_authorization/broker_order_sent/order_created = False
"""

from __future__ import annotations

import pytest

from app.governance import live_transition_audit as la
from app.governance.live_transition_audit import (
    LiveAuditError,
    get_live_transition_audit_log,
    reset_live_transition_audit_log_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_live_transition_audit_log_for_tests()
    yield
    reset_live_transition_audit_log_for_tests()


def _record(**over):
    log = get_live_transition_audit_log()
    base = dict(
        operator="op-1",
        action="LIVE_CANARY_REVIEW_REQUESTED",
        reason="Paper Gate 기준 검토 요청",
        risk_profile="CONSERVATIVE",
        symbol_whitelist=["005930"],
        max_order_notional=30000,
        daily_live_limit=30000,
        capital_review_snapshot={"status": "REVIEW_REQUIRED"},
        paper_gate_verdict={"verdict": "READY_FOR_LIVE_REVIEW"},
        canary_gate_verdict={"reason_code": "CANARY_REVIEW_READY"},
        manual_approval_snapshot={"manual_approval_present": True},
        notes="검토 메모",
    )
    base.update(over)
    return log.record(**base)


# ── 필수 필드 ─────────────────────────────────────────────────────────────────


def test_operator_required():
    with pytest.raises(LiveAuditError) as ei:
        _record(operator="")
    assert ei.value.reason_code == la.LIVE_AUDIT_OPERATOR_REQUIRED


def test_reason_required():
    with pytest.raises(LiveAuditError) as ei:
        _record(reason="  ")
    assert ei.value.reason_code == la.LIVE_AUDIT_REASON_REQUIRED


def test_invalid_action_rejected():
    with pytest.raises(LiveAuditError) as ei:
        _record(action="APPROVED")  # 표준 집합 아님 + 오해 소지 단어.
    assert ei.value.reason_code == la.LIVE_AUDIT_INVALID_ACTION


# ── 기록 + 필드 저장 ─────────────────────────────────────────────────────────


def test_records_all_fields():
    e = _record()
    assert e.operator == "op-1"
    assert e.action == "LIVE_CANARY_REVIEW_REQUESTED"
    assert e.reason
    assert e.reason_code == la.LIVE_AUDIT_RECORDED
    assert e.created_at  # timestamp 자동 생성
    assert e.audit_id
    assert e.risk_profile == "CONSERVATIVE"
    assert e.symbol_whitelist == ("005930",)
    assert e.max_order_notional == 30000
    assert e.daily_live_limit == 30000
    assert e.capital_review_snapshot.get("status") == "REVIEW_REQUIRED"
    assert e.paper_gate_verdict.get("verdict") == "READY_FOR_LIVE_REVIEW"
    assert e.canary_gate_verdict.get("reason_code") == "CANARY_REVIEW_READY"
    assert e.manual_approval_snapshot.get("manual_approval_present") is True


def test_safety_invariants_on_entry():
    e = _record()
    assert e.is_order_signal is False
    assert e.is_live_authorization is False
    assert e.broker_order_sent is False
    assert e.order_created is False
    assert e.auto_apply_allowed is False
    assert e.contains_secret is False
    d = e.to_dict()
    assert d["recorded"] is True
    assert d["is_live_authorization"] is False


def test_all_standard_actions_accepted():
    for action in la.STANDARD_ACTIONS:
        e = _record(action=action)
        assert e.action == action


# ── append-only ──────────────────────────────────────────────────────────────


def test_append_only_links_previous():
    e1 = _record(action="LIVE_REVIEW_REQUESTED")
    e2 = _record(action="LIVE_AUDIT_NOTE_ADDED")
    assert e1.previous_audit_id is None
    assert e2.previous_audit_id == e1.audit_id
    assert e1.audit_id != e2.audit_id


def test_no_update_or_delete_methods():
    log = get_live_transition_audit_log()
    for forbidden in ("update", "delete", "edit", "remove", "modify",
                      "update_entry", "delete_entry"):
        assert not hasattr(log, forbidden), f"append-only 위반: {forbidden}"


def test_module_has_no_update_delete_symbols():
    src = open(la.__file__, encoding="utf-8").read()
    for forbidden in ("def update_audit", "def delete_audit", "def remove_audit",
                      "def edit_audit", ".pop(", "del self._entries"):
        assert forbidden not in src, f"수정/삭제 심볼 발견: {forbidden}"


def test_entry_is_frozen_immutable():
    e = _record()
    with pytest.raises(Exception):
        e.operator = "tampered"  # frozen dataclass


def test_recent_returns_newest_first():
    _record(action="LIVE_REVIEW_REQUESTED")
    e2 = _record(action="LIVE_REVIEW_REJECTED")
    recent = get_live_transition_audit_log().recent(limit=10)
    assert recent[0].audit_id == e2.audit_id


# ── secret fail-closed ───────────────────────────────────────────────────────


def test_secret_in_reason_blocked():
    with pytest.raises(LiveAuditError) as ei:
        _record(reason="token sk-ABCDEFGHIJKLMNOPQRSTUVWX leaked")
    assert ei.value.reason_code == la.LIVE_AUDIT_SECRET_BLOCKED


def test_account_in_snapshot_blocked():
    with pytest.raises(LiveAuditError) as ei:
        _record(capital_review_snapshot={"acct": "12345678-01"})
    assert ei.value.reason_code == la.LIVE_AUDIT_SECRET_BLOCKED


def test_no_broker_imports_in_module():
    src = open(la.__file__, encoding="utf-8").read()
    for forbidden in ("from app.brokers", "from app.execution", ".place_order(",
                      "route_order(", "import httpx", "import requests"):
        assert forbidden not in src


# ── API (append-only: POST + GET only) ───────────────────────────────────────


def _payload(**over):
    base = dict(
        operator="op-api", action="LIVE_REVIEW_REQUESTED", reason="api 검토 요청",
        risk_profile="CONSERVATIVE", symbol_whitelist=["005930"],
        max_order_notional=30000, daily_live_limit=30000,
        paper_gate_verdict={"verdict": "READY_FOR_LIVE_REVIEW"},
    )
    base.update(over)
    return base


def test_api_post_records(client):
    r = client.post("/api/governance/live-transition-audit", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["recorded"] is True
    assert body["is_live_authorization"] is False
    assert body["broker_order_sent"] is False
    assert body["order_created"] is False
    assert body["audit_id"]


def test_api_post_operator_required(client):
    r = client.post("/api/governance/live-transition-audit", json=_payload(operator=""))
    assert r.status_code == 400
    assert r.json()["detail"]["reason_code"] == la.LIVE_AUDIT_OPERATOR_REQUIRED


def test_api_post_secret_blocked(client):
    r = client.post("/api/governance/live-transition-audit",
                    json=_payload(reason="sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA leak"))
    assert r.status_code == 400
    assert r.json()["detail"]["reason_code"] == la.LIVE_AUDIT_SECRET_BLOCKED


def test_api_get_lists(client):
    client.post("/api/governance/live-transition-audit", json=_payload())
    r = client.get("/api/governance/live-transition-audit?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_api_no_put_patch_delete(client):
    for method in ("put", "patch", "delete"):
        r = getattr(client, method)("/api/governance/live-transition-audit")
        # 라우트 미정의 → 405 Method Not Allowed (또는 404).
        assert r.status_code in (404, 405), f"{method} 가 허용되면 안 됨"


def test_api_post_no_broker_order(client):
    client.post("/api/governance/live-transition-audit", json=_payload())
    assert len(client.test_broker.orders) == 0


def test_api_no_secret_in_response(client):
    import json as _json
    r = client.post("/api/governance/live-transition-audit", json=_payload())
    text = _json.dumps(r.json(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "Bearer ", "sk-ant-", "access_token="):
        assert forbidden not in text
