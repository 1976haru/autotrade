from sqlalchemy import select

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval


def _enable_manual_approval(monkeypatch, client=None):
    """Sets DEFAULT_MODE=LIVE_MANUAL_APPROVAL and (since 061) flips
    enable_live_trading on the risk manager — the queue gate added by 061
    rejects submissions when the global flag is off, so any test that
    expects a PENDING row needs the operator to have opted in."""
    monkeypatch.setattr(get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL)
    if client is not None:
        client.test_risk_manager.policy.enable_live_trading = True


def _submit_buy(client, symbol="005930", qty=1):
    return client.post(
        "/api/broker/orders",
        json={"symbol": symbol, "side": "BUY", "quantity": qty},
    )


def test_order_in_manual_mode_returns_202_with_pending(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    res = _submit_buy(client)
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert isinstance(body["approval_id"], int)
    assert any("manual approval" in r for r in body["reasons"])


def test_list_pending_returns_submitted_approvals(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve")
    res = client.post(f"/api/approvals/{approval_id}/approve")
    assert res.status_code == 409


def test_reject_after_approve_returns_409(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve")
    res = client.post(f"/api/approvals/{approval_id}/reject")
    assert res.status_code == 409


def test_approve_without_body_uses_defaults(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    client.post(f"/api/approvals/{submit['approval_id']}/cancel")
    assert client.get("/api/approvals").json() == []


def test_cancel_unknown_id_returns_404(client):
    res = client.post("/api/approvals/9999/cancel")
    assert res.status_code == 404


def test_cancel_after_approve_returns_409(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve")
    res = client.post(f"/api/approvals/{approval_id}/cancel")
    assert res.status_code == 409


def test_approve_after_cancel_returns_409(client, monkeypatch):
    """Cancelled approvals are settled — cannot be reopened by approve."""
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]
    client.post(f"/api/approvals/{approval_id}/cancel")
    res = client.post(f"/api/approvals/{approval_id}/approve")
    assert res.status_code == 409


def test_cancel_without_body_uses_defaults(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    res = client.post(f"/api/approvals/{submit['approval_id']}/cancel")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "CANCELLED"
    assert body["decided_by"] is None
    assert body["note"] is None


# ---------- history ----------

def test_history_excludes_pending_and_returns_only_decided(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
    a1 = _submit_buy(client, "005930").json()
    a2 = _submit_buy(client, "000660").json()

    client.post(f"/api/approvals/{a1['approval_id']}/reject")
    client.post(f"/api/approvals/{a2['approval_id']}/cancel")

    history = client.get("/api/approvals/history").json()
    # decided_at desc → a2 (cancelled last) comes first
    assert history[0]["status"] == "CANCELLED"
    assert history[1]["status"] == "REJECTED"


def test_history_status_filter_narrows_results(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
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
    _enable_manual_approval(monkeypatch, client)
    _submit_buy(client)  # PENDING but never decided
    assert client.get("/api/approvals/history").json() == []


def test_history_rejects_invalid_status_filter(client):
    res = client.get("/api/approvals/history?status=PENDING")
    assert res.status_code == 422  # FastAPI's Literal validation rejects PENDING


def test_history_limit_caps_results(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    ids = []
    for _ in range(5):
        ids.append(_submit_buy(client).json()["approval_id"])
    for i in ids:
        client.post(f"/api/approvals/{i}/cancel")

    res = client.get("/api/approvals/history?limit=3").json()
    assert len(res) == 3


# ---------- reasons (056) ----------

def test_pending_list_includes_reasons_from_audit_join(client, monkeypatch):
    """결재 행에 RiskManager가 NEEDS_APPROVAL로 분류한 사유가 함께 노출돼야
    운영자가 모달을 열기 전부터 컨텍스트를 본다."""
    _enable_manual_approval(monkeypatch, client)
    _submit_buy(client)
    pending = client.get("/api/approvals").json()
    assert len(pending) == 1
    assert isinstance(pending[0]["reasons"], list)
    assert any("manual approval" in r for r in pending[0]["reasons"])


def test_get_single_approval_includes_reasons(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    res = client.get(f"/api/approvals/{submit['approval_id']}").json()
    assert isinstance(res["reasons"], list)
    assert any("manual approval" in r for r in res["reasons"])


def test_history_includes_reasons(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    client.post(f"/api/approvals/{submit['approval_id']}/cancel")
    history = client.get("/api/approvals/history").json()
    assert len(history) == 1
    assert any("manual approval" in r for r in history[0]["reasons"])


def test_approve_response_includes_reasons(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    res = client.post(f"/api/approvals/{submit['approval_id']}/approve").json()
    assert isinstance(res["approval"]["reasons"], list)
    assert any("manual approval" in r for r in res["approval"]["reasons"])


# ---------- AI provenance (190) ----------

def test_pending_default_provenance_is_manual(client, monkeypatch):
    """수동 주문 → requested_by_ai=False, ai_decision_meta=None."""
    _enable_manual_approval(monkeypatch, client)
    _submit_buy(client)
    row = client.get("/api/approvals").json()[0]
    assert row["requested_by_ai"]    is False
    assert row["strategy"]           is None
    assert row["signal_strength"]    is None
    assert row["signal_confidence"]  is None
    assert row["ai_decision_meta"]   is None


def _patch_audit_as_ai(client, approval_id, **fields):
    """결재 행의 audit_id를 조회 → audit row를 AI 출처로 패치."""
    pending = client.get(f"/api/approvals/{approval_id}").json()
    audit_id = pending["audit_id"]
    with client.test_db_factory() as db:
        row = db.execute(select(OrderAuditLog).where(OrderAuditLog.id == audit_id)).scalar_one()
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def test_pending_surfaces_ai_provenance_from_audit(client, monkeypatch):
    """audit row에 AI 메타가 있으면 결재 endpoint가 그대로 surface."""
    _enable_manual_approval(monkeypatch, client)
    submit = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "strategy": "ai_orb", "signal_strength": 80, "signal_confidence": 65,
    }).json()
    _patch_audit_as_ai(
        client, submit["approval_id"],
        requested_by_ai=True,
        ai_decision_meta={"confidence": 65, "reasons": ["entry+news"]},
    )
    pending = client.get("/api/approvals").json()
    target = next(p for p in pending if p["id"] == submit["approval_id"])
    assert target["requested_by_ai"]   is True
    assert target["strategy"]          == "ai_orb"
    assert target["signal_strength"]   == 80
    assert target["signal_confidence"] == 65
    assert target["ai_decision_meta"]  == {"confidence": 65, "reasons": ["entry+news"]}


def test_history_surfaces_ai_provenance(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1, "strategy": "ai_rsi",
    }).json()
    _patch_audit_as_ai(client, submit["approval_id"], requested_by_ai=True)
    client.post(f"/api/approvals/{submit['approval_id']}/cancel")
    history = client.get("/api/approvals/history").json()
    assert history[0]["requested_by_ai"] is True
    assert history[0]["strategy"]        == "ai_rsi"


def test_get_single_approval_surfaces_ai_provenance(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    _patch_audit_as_ai(
        client, submit["approval_id"],
        requested_by_ai=True,
        ai_decision_meta={"confidence": 50},
    )
    res = client.get(f"/api/approvals/{submit['approval_id']}").json()
    assert res["requested_by_ai"] is True
    assert res["ai_decision_meta"] == {"confidence": 50}


# ---------- 070: re-eval at approve time (route layer) ----------

def test_approve_returns_409_when_emergency_stop_toggled_after_submit(client, monkeypatch):
    """Operator pulls emergency_stop after queueing; the route must surface
    409 with reasons rather than executing."""
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()

    client.test_risk_manager.set_emergency_stop(True)

    res = client.post(f"/api/approvals/{submit['approval_id']}/approve")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "risk_check_failed_at_approve"
    assert any("emergency stop" in r for r in detail["reasons"])

    # Approval still PENDING for retry
    pending = client.get("/api/approvals").json()
    assert any(p["id"] == submit["approval_id"] for p in pending)


def test_approve_returns_409_when_live_trading_flag_toggled_off(client, monkeypatch):
    """ENABLE_LIVE_TRADING was on at submit (queue gate let it through);
    flip off before approve and re-eval blocks at the same gate."""
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()

    client.test_risk_manager.policy.enable_live_trading = False

    res = client.post(f"/api/approvals/{submit['approval_id']}/approve")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert any("live trading" in r for r in detail["reasons"])


# ---------- 076: approval attempts in response payload ----------

def test_approval_attempts_persist_across_pending_listing(client, monkeypatch):
    """A failed approve appends to attempts; a subsequent GET /api/approvals
    returns the row with that history attached, surviving the modal-close /
    page-refresh cycle that 075's session memory couldn't."""
    _enable_manual_approval(monkeypatch, client)
    submit = _submit_buy(client).json()
    approval_id = submit["approval_id"]

    # Trigger a re-eval failure (emergency stop)
    client.test_risk_manager.set_emergency_stop(True)
    res = client.post(f"/api/approvals/{approval_id}/approve",
                      json={"decided_by": "ops-x"})
    assert res.status_code == 409

    # GET pending — the row should now carry one attempts entry
    pending = client.get("/api/approvals").json()
    row = next(p for p in pending if p["id"] == approval_id)
    assert len(row["attempts"]) == 1
    entry = row["attempts"][0]
    assert entry["decided_by"] == "ops-x"
    assert any("emergency stop" in r for r in entry["reasons"])


def test_approval_attempts_default_empty_for_fresh_pending(client, monkeypatch):
    _enable_manual_approval(monkeypatch, client)
    _submit_buy(client)
    pending = client.get("/api/approvals").json()
    assert pending[0]["attempts"] == []
