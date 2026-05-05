from sqlalchemy import select

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval


def _enable_manual_approval(monkeypatch):
    monkeypatch.setattr(get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL)


def _submit_buy(client, symbol="005930", qty=1):
    return client.post(
        "/api/broker/orders",
        json={"symbol": symbol, "side": "BUY", "quantity": qty},
    )


def test_order_in_manual_mode_returns_202_with_pending(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    res = _submit_buy(client)
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert isinstance(body["approval_id"], int)
    assert any("manual approval" in r for r in body["reasons"])


def test_list_pending_returns_submitted_approvals(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    res = client.get("/api/approvals")
    assert res.status_code == 200
    assert res.json() == []

    s1 = _submit_buy(client, "005930").json()
    s2 = _submit_buy(client, "000660").json()
    pending = client.get("/api/approvals").json()
    assert {p["id"] for p in pending} == {s1["approval_id"], s2["approval_id"]}
    assert all(p["status"] == "PENDING" for p in pending)


def test_get_unknown_approval_returns_404(client):
    res = client.get("/api/approvals/9999")
    assert res.status_code == 404


def test_approve_executes_order_and_marks_approved(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client, qty=1).json()
    approval_id = submit["approval_id"]

    res = client.post(f"/api/approvals/{approval_id}/approve",
                      json={"decided_by": "user", "note": "go"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approval"]["status"] == "APPROVED"
    assert body["approval"]["decided_by"] == "user"
    assert body["result"]["status"] == "FILLED"

    with client.test_db_factory() as db:
        audit = db.execute(select(OrderAuditLog)).scalar_one()
        assert audit.executed is True
        assert audit.broker_status == "FILLED"
        approval = db.execute(select(PendingApproval)).scalar_one()
        assert approval.status == "APPROVED"

    pending = client.get("/api/approvals").json()
    assert pending == []


def test_reject_does_not_execute(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]

    res = client.post(f"/api/approvals/{approval_id}/reject",
                      json={"note": "no thanks"})
    assert res.status_code == 200
    assert res.json()["status"] == "REJECTED"
    assert res.json()["note"] == "no thanks"

    with client.test_db_factory() as db:
        audit = db.execute(select(OrderAuditLog)).scalar_one()
        assert audit.executed is False
        approval = db.execute(select(PendingApproval)).scalar_one()
        assert approval.status == "REJECTED"


def test_double_approve_returns_409(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve")
    res = client.post(f"/api/approvals/{approval_id}/approve")
    assert res.status_code == 409


def test_reject_after_approve_returns_409(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve")
    res = client.post(f"/api/approvals/{approval_id}/reject")
    assert res.status_code == 409


def test_approve_without_body_uses_defaults(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    res = client.post(f"/api/approvals/{submit['approval_id']}/approve")
    assert res.status_code == 200
    assert res.json()["approval"]["decided_by"] is None
    assert res.json()["approval"]["note"] is None


def test_simulation_mode_does_not_create_pending(client):
    # default mode is SIMULATION; small order should be approved immediately
    res = _submit_buy(client)
    assert res.status_code == 200
    assert res.json()["status"] == "FILLED"
    with client.test_db_factory() as db:
        approvals = db.execute(select(PendingApproval)).scalars().all()
        assert approvals == []


# ---------- cancel ----------

def test_cancel_marks_approval_cancelled_and_keeps_audit_unexecuted(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]

    res = client.post(f"/api/approvals/{approval_id}/cancel",
                      json={"decided_by": "user", "note": "stale signal"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"]     == "CANCELLED"
    assert body["decided_by"] == "user"
    assert body["note"]       == "stale signal"

    with client.test_db_factory() as db:
        audit = db.execute(select(OrderAuditLog)).scalar_one()
        assert audit.executed is False
        approval = db.execute(select(PendingApproval)).scalar_one()
        assert approval.status == "CANCELLED"


def test_cancelled_approval_disappears_from_pending_list(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    client.post(f"/api/approvals/{submit['approval_id']}/cancel")
    assert client.get("/api/approvals").json() == []


def test_cancel_unknown_id_returns_404(client):
    res = client.post("/api/approvals/9999/cancel")
    assert res.status_code == 404


def test_cancel_after_approve_returns_409(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve")
    res = client.post(f"/api/approvals/{approval_id}/cancel")
    assert res.status_code == 409


def test_approve_after_cancel_returns_409(client, monkeypatch):
    """Cancelled approvals are settled — cannot be reopened by approve."""
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/cancel")
    res = client.post(f"/api/approvals/{approval_id}/approve")
    assert res.status_code == 409


def test_cancel_without_body_uses_defaults(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    submit = _submit_buy(client).json()
    res = client.post(f"/api/approvals/{submit['approval_id']}/cancel")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "CANCELLED"
    assert body["decided_by"] is None
    assert body["note"] is None


# ---------- history ----------

def test_history_excludes_pending_and_returns_only_decided(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    a1 = _submit_buy(client, "005930").json()
    a2 = _submit_buy(client, "000660").json()
    _submit_buy(client, "035720")  # third stays PENDING — must not appear

    client.post(f"/api/approvals/{a1['approval_id']}/approve")
    client.post(f"/api/approvals/{a2['approval_id']}/reject")

    history = client.get("/api/approvals/history").json()
    assert len(history) == 2
    statuses = {h["status"] for h in history}
    assert statuses == {"APPROVED", "REJECTED"}


def test_history_orders_most_recent_first(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    a1 = _submit_buy(client, "005930").json()
    a2 = _submit_buy(client, "000660").json()

    client.post(f"/api/approvals/{a1['approval_id']}/reject")
    client.post(f"/api/approvals/{a2['approval_id']}/cancel")

    history = client.get("/api/approvals/history").json()
    # decided_at desc → a2 (cancelled last) comes first
    assert history[0]["status"] == "CANCELLED"
    assert history[1]["status"] == "REJECTED"


def test_history_status_filter_narrows_results(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    a1 = _submit_buy(client).json()
    a2 = _submit_buy(client).json()
    a3 = _submit_buy(client).json()
    client.post(f"/api/approvals/{a1['approval_id']}/cancel")
    client.post(f"/api/approvals/{a2['approval_id']}/cancel")
    client.post(f"/api/approvals/{a3['approval_id']}/reject")

    cancelled = client.get("/api/approvals/history?status=CANCELLED").json()
    assert len(cancelled) == 2
    assert all(h["status"] == "CANCELLED" for h in cancelled)

    rejected = client.get("/api/approvals/history?status=REJECTED").json()
    assert len(rejected) == 1
    assert rejected[0]["status"] == "REJECTED"


def test_history_empty_when_nothing_decided(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    _submit_buy(client)  # PENDING but never decided
    assert client.get("/api/approvals/history").json() == []


def test_history_rejects_invalid_status_filter(client):
    res = client.get("/api/approvals/history?status=PENDING")
    assert res.status_code == 422  # FastAPI's Literal validation rejects PENDING


def test_history_limit_caps_results(client, monkeypatch):
    _enable_manual_approval(monkeypatch)
    ids = []
    for _ in range(5):
        ids.append(_submit_buy(client).json()["approval_id"])
    for i in ids:
        client.post(f"/api/approvals/{i}/cancel")

    res = client.get("/api/approvals/history?limit=3").json()
    assert len(res) == 3
