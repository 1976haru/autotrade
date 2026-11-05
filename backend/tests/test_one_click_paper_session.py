from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import EmergencyStopEvent
from app.brokers.base import Balance, Position
from app.kis_paper.one_click_session import (
    KST, authorize_order, cancel_permission, create_approval, load_approval,
    public_status,
)


class Broker:
    def __init__(self, *, orders=None, positions=None, orderable_cash=500_000_000, fail=False):
        self.orders = orders or []
        self.positions = positions or []
        self.orderable_cash = orderable_cash
        self.fail = fail
        self.reads = 0

    async def get_positions(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("kis down")
        return self.positions

    async def get_balance(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("kis down")
        return Balance(cash=500_000_000, equity=500_000_000,
                       buying_power=500_000_000)

    async def get_orderable_cash(self, *, symbol, price):
        self.reads += 1
        if self.fail:
            raise RuntimeError("kis down")
        return self.orderable_cash

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
    return SimpleNamespace(emergency_stop=False, kill_switch_level="OFF",
                           kill_switch_policy_version=2)


def _at(day, hh, mm):
    return datetime.combine(day, time(hh, mm), KST).astimezone(timezone.utc)


def test_past_date_approval_is_not_reported_as_active():
    from datetime import timedelta
    yesterday = (datetime.now(KST).date() - timedelta(days=1))
    record = {
        "status": "ACTIVE", "target_date": yesterday.isoformat(),
        "expires_at": datetime.combine(yesterday + timedelta(days=1), time(0), KST)
        .astimezone(timezone.utc).isoformat(),
    }
    status = public_status(record)
    assert status["status"] == "EXPIRED"
    assert status["authorized"] is False
    assert status["block_reason"] == "APPROVAL_DATE_EXPIRED"


def test_future_date_approval_waits_until_approved_session():
    from datetime import timedelta
    tomorrow = datetime.now(KST).date() + timedelta(days=1)
    record = {
        "status": "SCHEDULED", "target_date": tomorrow.isoformat(),
        "expires_at": datetime.combine(tomorrow + timedelta(days=1), time(0), KST)
        .astimezone(timezone.utc).isoformat(),
    }
    status = public_status(record)
    assert status["status"] == "SCHEDULED"
    assert status["authorized"] is False
    assert status["block_reason"] == "WAITING_FOR_APPROVED_DATE"


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
    assert latest is None
    assert runtime_risk.emergency_stop is False
    assert cancel_permission(bot_owned=True, is_paper=True,
                             account_fingerprint_value=r["account_fingerprint"])[0] is False


def test_before_0905_is_zero_write_wait(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    broker = Broker()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker, risk=SimpleNamespace(emergency_stop=False),
        side="BUY", symbol="005930", now=_at(day, 9, 4),
    ))
    assert (ok, reason, broker.reads) == (False, "WAITING_FOR_0905_KST", 0)


def test_0905_final_gate_passes_only_after_read_checks(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    broker = Broker()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker, risk=SimpleNamespace(emergency_stop=False),
        side="BUY", symbol="005930", now=_at(day, 9, 5),
    ))
    assert ok and reason == "ONE_CLICK_PAPER_AUTHORIZED" and broker.reads == 4
    assert load_approval()["status"] == "ACTIVE"


def test_new_emergency_invalidates_and_restart_stays_blocked(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    db.add(EmergencyStopEvent(enabled=True, level="LEVEL_1", policy_version=2,
                              decided_by="safety", note="new incident"))
    db.add(EmergencyStopEvent(enabled=False, level="OFF", policy_version=2,
                              decided_by="operator", note="explicitly cleared later"))
    db.commit()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=Broker(), risk=SimpleNamespace(emergency_stop=False),
        side="BUY", symbol="005930", now=_at(day, 9, 5),
    ))
    assert not ok and reason == "NEW_EMERGENCY_STOP_AFTER_APPROVAL"
    assert load_approval()["status"] == "BLOCKED"


def test_account_poller_and_open_order_fail_closed(db, tmp_path, monkeypatch):
    day = datetime.now(KST).date()
    for name, broker, current, expected in (
        ("account", Broker(), settings("9999999901"), "PAPER_ACCOUNT_CHANGED"),
        ("poller", Broker(fail=True), settings(), "KIS_PREFLIGHT_FAILED:RuntimeError"),
        (
            "open", Broker(orders=[{"side": "BUY", "remaining": 1}]),
            settings(), "OPEN_ORDER_PRESENT_CANCEL_NOT_AUTHORIZED",
        ),
    ):
        monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / f"{name}.json"))
        create_approval(
            db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
            actor="operator", explicit_ack=True,
        )
        ok, reason = asyncio.run(authorize_order(
            db=db, settings=current, broker=broker, risk=SimpleNamespace(emergency_stop=False),
            side="BUY", symbol="005930", now=_at(day, 9, 5),
        ))
        assert not ok and reason == expected


def test_explicit_ack_required(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    day = datetime.now(KST).date()
    try:
        create_approval(
            db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
            actor="operator", explicit_ack=False,
        )
    except ValueError as exc:
        assert str(exc) == "EXPLICIT_APPROVAL_REQUIRED"
    else:
        raise AssertionError("unauthenticated/implicit reservation was accepted")


def test_reservation_never_clears_active_emergency_stop(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "blocked-stop.json"))
    db.add(EmergencyStopEvent(enabled=True, level="LEVEL_1", policy_version=2,
                              decided_by="safety", note="active stop"))
    db.commit()
    with pytest.raises(ValueError, match="EMERGENCY_STOP_ACTIVE"):
        create_approval(
            db=db, settings=settings(), risk=SimpleNamespace(emergency_stop=False),
            target_date=datetime.now(KST).date().isoformat(), actor="operator",
            explicit_ack=True,
        )


def test_two_million_entry_reserves_costs_inside_ten_million_budget(db, tmp_path, monkeypatch):
    from app.core import runtime_config

    monkeypatch.setattr(runtime_config, "effective_per_stock_budget", lambda: 2_000_000)
    monkeypatch.setattr(runtime_config, "effective_max_concurrent_positions", lambda: 5)
    monkeypatch.setattr(runtime_config, "effective_daily_buy_limit", lambda: 10_000_000)
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval-2m.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    broker = Broker(positions=[
        Position(symbol=f"00000{i}", quantity=1, avg_price=2_000_000,
                 market_price=2_000_000)
        for i in range(1, 5)
    ])
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker,
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=1, notional_krw=2_000_000, now=_at(day, 9, 5),
    ))
    assert not ok
    assert reason == "PAPER_SYMBOL_EXPOSURE_EXCEEDED"


def test_missing_orderable_cash_blocks_buy(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "no-orderable.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    broker = Broker(orderable_cash=None)
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker,
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=10, notional_krw=100_000, now=_at(day, 9, 5),
    ))
    assert not ok and reason == "KIS_ORDERABLE_CASH_UNAVAILABLE"


def test_kis_calculation_price_and_reserve_block_over_budget_market_buy(db, tmp_path, monkeypatch):
    from app.core import runtime_config

    monkeypatch.setattr(runtime_config, "effective_per_stock_budget", lambda: 1_000_000)
    monkeypatch.setattr(runtime_config, "effective_max_concurrent_positions", lambda: 5)
    monkeypatch.setattr(runtime_config, "effective_daily_buy_limit", lambda: 10_000_000)

    class CapacityBroker(Broker):
        async def get_orderable_buy_capacity(self, *, symbol, price):
            self.reads += 1
            return {
                "orderable_cash_krw": 497_500_000,
                "no_credit_buy_amount_krw": 499_929_000,
                "no_credit_buy_quantity": 100,
                "calculation_price_krw": 283_000,
            }

    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "calc-price.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    broker = CapacityBroker()
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=broker,
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=4, notional_krw=812_000, now=_at(day, 9, 5),
    ))
    assert not ok and reason == "APPROVED_ORDER_LIMIT_EXCEEDED_AT_KIS_CALC_PRICE"


def test_kis_calculation_price_accepts_safe_three_share_market_buy(db, tmp_path, monkeypatch):
    from app.core import runtime_config

    monkeypatch.setattr(runtime_config, "effective_per_stock_budget", lambda: 1_000_000)
    monkeypatch.setattr(runtime_config, "effective_max_concurrent_positions", lambda: 5)
    monkeypatch.setattr(runtime_config, "effective_daily_buy_limit", lambda: 10_000_000)

    class CapacityBroker(Broker):
        async def get_orderable_buy_capacity(self, *, symbol, price):
            self.reads += 1
            return {
                "orderable_cash_krw": 497_500_000,
                "no_credit_buy_amount_krw": 499_929_000,
                "no_credit_buy_quantity": 100,
                "calculation_price_krw": 283_000,
            }

    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "calc-price-safe.json"))
    day = datetime.now(KST).date()
    create_approval(
        db=db, settings=settings(), risk=risk(), target_date=day.isoformat(),
        actor="operator", explicit_ack=True,
    )
    ok, reason = asyncio.run(authorize_order(
        db=db, settings=settings(), broker=CapacityBroker(),
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=3, notional_krw=609_000, now=_at(day, 9, 5),
    ))
    assert ok and reason == "ONE_CLICK_PAPER_AUTHORIZED"


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

