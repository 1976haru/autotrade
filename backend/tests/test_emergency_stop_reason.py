"""Emergency Stop Reason Taxonomy tests (153, MUST)."""

from sqlalchemy import select

from app.db.models import EmergencyStopEvent
from app.risk.emergency_reasons import (
    EMERGENCY_STOP_REASONS,
    is_valid_reason,
)


# ---------- enum ----------

def test_enum_includes_all_required_codes():
    """직무 명세 9개 코드 모두 enum에 포함."""
    required = {
        "manual_operator", "daily_loss_limit", "data_stale",
        "broker_error", "repeated_order_failure", "abnormal_slippage",
        "agent_warning", "margin_risk", "futures_liquidation_risk",
    }
    assert required.issubset(EMERGENCY_STOP_REASONS)


def test_is_valid_reason_accepts_known_codes():
    for code in EMERGENCY_STOP_REASONS:
        assert is_valid_reason(code) is True


def test_is_valid_reason_accepts_none():
    assert is_valid_reason(None) is True


def test_is_valid_reason_rejects_unknown_code():
    assert is_valid_reason("does_not_exist") is False


# ---------- API ----------

def test_emergency_stop_reasons_endpoint_lists_all_codes(client):
    res = client.get("/api/risk/emergency-stop/reasons")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == EMERGENCY_STOP_REASONS


def test_post_with_valid_reason_persists(client):
    res = client.post("/api/risk/emergency-stop", json={
        "enabled": True,
        "decided_by": "ops1",
        "reason_code": "daily_loss_limit",
    })
    assert res.status_code == 200
    history = client.get("/api/risk/emergency-stop/history").json()
    assert history[0]["reason_code"] == "daily_loss_limit"


def test_post_with_null_reason_is_allowed(client):
    """reason_code 미명시는 backwards compat 허용."""
    res = client.post("/api/risk/emergency-stop", json={"enabled": True})
    assert res.status_code == 200
    history = client.get("/api/risk/emergency-stop/history").json()
    assert history[0]["reason_code"] is None


def test_post_with_unknown_reason_returns_422(client):
    """등록 안 된 reason_code는 Pydantic validation에서 거부."""
    res = client.post("/api/risk/emergency-stop", json={
        "enabled": True,
        "reason_code": "totally_made_up",
    })
    assert res.status_code == 422


def test_history_endpoint_surfaces_reason_code(client):
    """기존 history 라우트가 reason_code를 응답에 포함."""
    client.post("/api/risk/emergency-stop", json={
        "enabled": True,
        "reason_code": "data_stale",
    })
    client.post("/api/risk/emergency-stop", json={
        "enabled": False,
        "reason_code": "manual_operator",
    })
    history = client.get("/api/risk/emergency-stop/history").json()
    # most recent first → manual_operator first.
    assert history[0]["reason_code"] == "manual_operator"
    assert history[1]["reason_code"] == "data_stale"


def test_no_op_toggle_does_not_create_history_row(client):
    """이미 ON인 상태에서 다시 ON 토글하면 audit row 추가 X — reason_code도 안 남김."""
    client.post("/api/risk/emergency-stop", json={
        "enabled": True, "reason_code": "data_stale",
    })
    # 같은 상태로 다시 호출.
    client.post("/api/risk/emergency-stop", json={
        "enabled": True, "reason_code": "agent_warning",
    })
    with client.test_db_factory() as db:
        rows = db.execute(select(EmergencyStopEvent)).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason_code == "data_stale"
