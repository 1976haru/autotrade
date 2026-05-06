"""E2E approval / order / fill / audit flow (132, MUST).

이 파일은 단위 테스트(test_permission_gate, test_executor, test_approvals_routes,
test_audit_routes 등)가 분산해서 검증하는 흐름을 *한 시나리오로 trace*한다.
운영자/감사가 'LIVE_MANUAL_APPROVAL 모드에서 한 주문이 어떻게 흘러가는지'
한 파일에서 읽을 수 있게 하는 게 목적 — 사고 분석 시 진입점으로 사용한다.

각 시나리오는:
    1. (필요 시) 모드/플래그 세팅
    2. POST /api/broker/orders (HTTP 진입)
    3. POST /api/approvals/{id}/<action>
    4. /api/audit/orders 응답 + DB row 검증

CLAUDE.md '단일 주문 진입점' 원칙 — 모든 경로는 route_order를 통과한다는
사실을 trace로 확인한다.
"""

from sqlalchemy import select

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval


def _enable_live_manual(monkeypatch, client):
    monkeypatch.setattr(
        get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL
    )
    client.test_risk_manager.policy.enable_live_trading = True


def _submit(client, symbol="005930", side="BUY", quantity=1):
    return client.post("/api/broker/orders", json={
        "symbol": symbol, "side": side, "quantity": quantity,
    })


# ---------- happy path ----------

def test_e2e_submit_approve_execute_audit(client, monkeypatch):
    """제출 → 승인 → 브로커 체결 → audit log + approval status 모두 일관."""
    _enable_live_manual(monkeypatch, client)

    # 1) 제출 — 202 PENDING_APPROVAL
    submit = _submit(client).json()
    approval_id = submit["approval_id"]
    assert submit["status"] == "PENDING_APPROVAL"

    # 2) DB: PENDING approval + audit row(NEEDS_APPROVAL, executed=False)
    with client.test_db_factory() as db:
        ap = db.execute(select(PendingApproval)).scalar_one()
        assert ap.id == approval_id
        assert ap.status == "PENDING"
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.decision == "NEEDS_APPROVAL"
        assert au.executed is False

    # 3) 승인 — broker fill + status APPROVED
    res = client.post(f"/api/approvals/{approval_id}/approve",
                      json={"decided_by": "ops1", "note": "ok"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approval"]["status"]   == "APPROVED"
    assert body["approval"]["decided_by"] == "ops1"
    assert body["result"]["status"]     == "FILLED"

    # 4) audit 갱신: executed=True, broker_status=FILLED
    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.executed is True
        assert au.broker_status == "FILLED"
        assert au.filled_quantity == 1

    # 5) /api/audit/orders 응답에서 같은 row가 보임
    audit = client.get("/api/audit/orders").json()
    assert len(audit) == 1
    assert audit[0]["executed"] is True
    assert audit[0]["broker_status"] == "FILLED"


# ---------- reject path ----------

def test_e2e_submit_reject_no_execution(client, monkeypatch):
    _enable_live_manual(monkeypatch, client)
    submit = _submit(client).json()
    approval_id = submit["approval_id"]

    res = client.post(f"/api/approvals/{approval_id}/reject",
                      json={"decided_by": "ops1", "note": "stale signal"})
    assert res.status_code == 200, res.text
    # reject/cancel은 ApprovalOut 단일 객체로 응답 (approve만 ApproveResponse).
    assert res.json()["status"] == "REJECTED"

    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.executed is False
        assert (au.broker_status or "") in ("", "NEEDS_APPROVAL")


def test_e2e_submit_cancel_no_execution(client, monkeypatch):
    _enable_live_manual(monkeypatch, client)
    submit = _submit(client).json()
    approval_id = submit["approval_id"]

    res = client.post(f"/api/approvals/{approval_id}/cancel",
                      json={"decided_by": "ops1", "note": "operator dropped"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "CANCELLED"

    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.executed is False


# ---------- safety guards (060/061) ----------

def test_e2e_emergency_stop_blocks_submission_in_live_manual(client, monkeypatch):
    """060: emergency_stop ON이면 LIVE_MANUAL_APPROVAL 모드에서도 제출이 거부.
    NEEDS_APPROVAL 큐에 들어가지 않는다 (hard reject across all modes)."""
    _enable_live_manual(monkeypatch, client)
    client.test_risk_manager.emergency_stop = True

    res = _submit(client)
    assert res.status_code == 400
    detail = res.json()["detail"]
    # detail 형태 — risk 사유 포함, emergency_stop 라벨이 있어야
    assert any("emergency" in r.lower() for r in detail.get("reasons", [])) \
        or "emergency" in str(detail).lower()

    with client.test_db_factory() as db:
        # PendingApproval row가 만들어지지 않아야 한다.
        assert db.execute(select(PendingApproval)).all() == []
        # audit log 자체는 거부 사유와 함께 기록 (CLAUDE.md 'audit 로그')
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.decision == "REJECTED"
        assert au.executed is False


def test_e2e_enable_live_trading_off_blocks_queue(client, monkeypatch):
    """061: LIVE_MANUAL_APPROVAL이라도 enable_live_trading=False면 큐 안 만듦.
    운영자 명시 옵트인 없이는 manual queue 생성 자체가 거부."""
    monkeypatch.setattr(
        get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL
    )
    # enable_live_trading = False (기본)
    res = _submit(client)
    assert res.status_code == 400

    with client.test_db_factory() as db:
        assert db.execute(select(PendingApproval)).all() == []


# ---------- approval-time re-eval (070/076) ----------

def test_e2e_double_approval_is_blocked(client, monkeypatch):
    """이미 APPROVED된 항목에 다시 approve를 보내면 409 Conflict —
    ApprovalAlreadyDecidedError가 throw되어 PermissionGate가 status=PENDING만
    처리한다는 invariant를 보장."""
    _enable_live_manual(monkeypatch, client)
    submit = _submit(client).json()
    approval_id = submit["approval_id"]

    first = client.post(f"/api/approvals/{approval_id}/approve",
                        json={"decided_by": "ops1"})
    assert first.status_code == 200

    second = client.post(f"/api/approvals/{approval_id}/approve",
                         json={"decided_by": "ops1"})
    assert second.status_code == 409


def test_e2e_emergency_stop_at_approve_time_blocks_execution_with_attempts(
    client, monkeypatch,
):
    """070/076: 제출 후 운영자 결정 사이에 emergency_stop이 켜졌다면
    approve가 re-evaluation에서 막히고, 시도 이력이 attempts JSON에 누적."""
    _enable_live_manual(monkeypatch, client)
    submit = _submit(client).json()
    approval_id = submit["approval_id"]

    # 결재 사이에 운영자가 emergency_stop을 켰다고 가정
    client.test_risk_manager.emergency_stop = True

    res = client.post(f"/api/approvals/{approval_id}/approve",
                      json={"decided_by": "ops1"})
    # re-eval 실패 — 070이 `409 Conflict + risk_check_failed_at_approve`로
    # 응답하고 (test_approvals_routes 패턴), approval은 PENDING 유지 + attempts에
    # 시도 누적(076).
    assert res.status_code == 409, res.text

    with client.test_db_factory() as db:
        ap = db.execute(select(PendingApproval)).scalar_one()
        # 여전히 PENDING — 결정되지 않음
        assert ap.status == "PENDING"
        # attempts에 한 건 이상 누적 (076)
        assert isinstance(ap.attempts, list)
        assert len(ap.attempts) >= 1
        # broker로는 안 갔다
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.executed is False


# ---------- audit endpoint coverage ----------

def test_e2e_audit_orders_lists_all_events_in_order(client, monkeypatch):
    _enable_live_manual(monkeypatch, client)
    s1 = _submit(client, "005930").json()
    s2 = _submit(client, "000660").json()

    # 첫 건은 거부, 둘째는 승인
    client.post(f"/api/approvals/{s1['approval_id']}/reject",
                json={"decided_by": "ops1"})
    client.post(f"/api/approvals/{s2['approval_id']}/approve",
                json={"decided_by": "ops1"})

    audit = client.get("/api/audit/orders").json()
    by_symbol = {a["symbol"]: a for a in audit}
    assert by_symbol["005930"]["executed"] is False
    assert by_symbol["000660"]["executed"] is True
    assert by_symbol["000660"]["broker_status"] == "FILLED"
