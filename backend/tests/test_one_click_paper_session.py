from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import EmergencyStopEvent
from app.kis_paper.one_click_session import (
    KST, authorize_order, cancel_permission, create_approval, load_approval,
)


class Broker:
    def __init__(self, *, orders=None, fail=False):
        self.orders = orders or []
        self.fail = fail
        self.reads = 0

    async def get_positions(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("kis down")
        return []

    async def list_today_orders(self, *, kst_date):
        self.reads += 1
        if self.fail:
            raise RuntimeError("kis down")
        return self.orders


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def settings(account="1234567801"):
    return SimpleNamespace(
        kis_is_paper=True, enable_live_trading=False, kis_account_no=account,
        kis_product_code="01", kis_paper_fill_polling=True,
    )


def risk():
    return SimpleNamespace(emergency_stop=True, kill_switch_level="LEVEL_1",
                           kill_switch_policy_version=2)


def _at(day, hh, mm):
    return datetime.combine(day, time(hh, mm), KST).astimezone(timezone.utc)


def test_date_account_scoped_and_cancel_denied(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    runtime_risk = risk()
    r = create_approval(db=db, settings=settings(), risk=runtime_risk, target_date=day.isoformat(),
                        actor="DOMAIN\\operator", explicit_ack=True)
    assert r["target_date"] == day.isoformat()
    assert r["account_fingerprint"] and "12345678" not in str(r)
    assert r["cancel_authorized"] is False
    latest = db.query(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc()).first()
    assert latest.enabled is False and latest.decided_by == "DOMAIN\\operator"
    assert runtime_risk.emergency_stop is False
    assert cancel_permission(bot_owned=True, is_paper=True,
                             account_fingerprint_value=r["account_fingerprint"])[0] is False


def test_before_0905_is_zero_write_wait(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    create_approval(db=db, settings=settings(), risk=risk(), target_date=day.isoformat(), actor="operator", explicit_ack=True)
    broker = Broker()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker, risk=SimpleNamespace(emergency_stop=False),
        side="BUY", now=_at(day, 9, 4),
    ))
    assert (ok, reason, broker.reads) == (False, "WAITING_FOR_0905_KST", 0)


def test_0905_final_gate_passes_only_after_read_checks(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    create_approval(db=db, settings=settings(), risk=risk(), target_date=day.isoformat(), actor="operator", explicit_ack=True)
    broker = Broker()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker, risk=SimpleNamespace(emergency_stop=False),
        side="BUY", now=_at(day, 9, 5),
    ))
    assert ok and reason == "ONE_CLICK_PAPER_AUTHORIZED" and broker.reads == 2
    assert load_approval()["status"] == "ACTIVE"


def test_new_emergency_invalidates_and_restart_stays_blocked(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    create_approval(db=db, settings=settings(), risk=risk(), target_date=day.isoformat(), actor="operator", explicit_ack=True)
    db.add(EmergencyStopEvent(enabled=True, level="LEVEL_1", policy_version=2,
                              decided_by="safety", note="new incident"))
    db.commit()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=Broker(), risk=SimpleNamespace(emergency_stop=False),
        side="BUY", now=_at(day, 9, 5),
    ))
    assert not ok and reason == "NEW_EMERGENCY_STOP_AFTER_APPROVAL"
    assert load_approval()["status"] == "BLOCKED"


def test_account_poller_and_open_order_fail_closed(db, tmp_path, monkeypatch):
    day = datetime.now(KST).date()
    for name, broker, current, expected in (
        ("account", Broker(), settings("9999999901"), "PAPER_ACCOUNT_CHANGED"),
        ("poller", Broker(fail=True), settings(), "KIS_PREFLIGHT_FAILED:RuntimeError"),
        ("open", Broker(orders=[{"remaining": 1}]), settings(), "OPEN_ORDER_PRESENT_CANCEL_NOT_AUTHORIZED"),
    ):
        monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / f"{name}.json"))
        create_approval(db=db, settings=settings(), risk=risk(), target_date=day.isoformat(), actor="operator", explicit_ack=True)
        ok, reason = asyncio.run(authorize_order(
            db=db, settings=current, broker=broker, risk=SimpleNamespace(emergency_stop=False),
            side="BUY", now=_at(day, 9, 5),
        ))
        assert not ok and reason == expected


def test_explicit_ack_required(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    try:
        create_approval(db=db, settings=settings(), risk=risk(), target_date=day.isoformat(), actor="operator", explicit_ack=False)
    except ValueError as exc:
        assert str(exc) == "EXPLICIT_APPROVAL_REQUIRED"
    else:
        raise AssertionError("unauthenticated/implicit reservation was accepted")


def test_http_reservation_rejects_browser_without_desktop_token(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", "desktop-only-secret")
    response = client.post("/api/auto-paper/paper-session-approval", json={
        "target_date": datetime.now(KST).date().isoformat(),
        "explicit_ack": True, "actor": "forged-localStorage-name",
    })
    assert response.status_code == 401
    assert response.json()["detail"] == "DESKTOP_SESSION_AUTH_REQUIRED"


def test_authenticated_desktop_reservation_succeeds(client, tmp_path, monkeypatch):
    import app.api.routes_auto_paper as routes
    monkeypatch.setenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", "desktop-only-secret")
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    monkeypatch.setattr(routes, "get_settings", lambda: settings())
    response = client.post("/api/auto-paper/paper-session-approval", headers={
        "X-Autotrade-Desktop-Auth": "desktop-only-secret",
    }, json={
        "target_date": datetime.now(KST).date().isoformat(),
        "explicit_ack": True, "actor": "DOMAIN\\operator",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SCHEDULED" and body["cancel_authorized"] is False

