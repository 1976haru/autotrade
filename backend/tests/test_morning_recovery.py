from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import EodLiquidationState
from app.kis_paper.eod_liquidation import Reconciliation
from app.kis_paper import morning_recovery as mr

KST = timezone(timedelta(hours=9))
MONDAY = datetime(2026, 11, 30, 9, 5, tzinfo=KST)
ACCOUNT = "paper-account-fingerprint"


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def make_settings(**overrides):
    values = dict(kis_is_paper=True, enable_live_trading=False,
                  ai_paper_tick_interval_seconds=30,
                  kis_paper_eod_max_attempts_per_symbol=3)
    values.update(overrides)
    return SimpleNamespace(**values)


def record_incident(factory):
    db = factory()
    db.add(EodLiquidationState(
        trading_date=mr.INCIDENT_DATE, account_key=ACCOUNT,
        state="FLATTEN_INCOMPLETE", reason_code="EOD_POSITIONS_REMAIN",
        is_live_authorization=False, summary={
            "totals": {"managed_qty": 172},
            "symbols": {s: {"managed_qty": q, "kis_hldg": q, "bot_net": q}
                        for s, q in mr.INCIDENT_HOLDINGS.items()},
        },
    ))
    db.commit()
    db.close()


def reconciliation(quantities, *, open_orders=0, filled=0, eod_fills=None, ok=True, error=None):
    eod_fills = eod_fills or {}
    symbols = {s: {"symbol": s, "managed_qty": q, "bot_net": q,
                   "kis_hldg": q, "ord_psbl": q, "pnl_bp": 0,
                   "open_sell_qty": 0, "unconfirmed_eod_qty": 0,
                   "eod_attempts": 0,
                   "eod_orders": ([{"filled": int(eod_fills.get(s, 0))}]
                                  if eod_fills.get(s, 0) else [])}
               for s, q in quantities.items()}
    total = sum(quantities.values())
    return Reconciliation(ok, error, symbols, [], [], {
        "managed_qty": total, "ledger_qty": total,
        "account_open_order_qty": open_orders, "program_open_buy_qty": 0,
        "open_sell_qty": 0, "unconfirmed_eod_qty": 0,
        "eod_filled_qty": filled,
    })


def install_fakes(monkeypatch, factory, recs, *, approval_status="SCHEDULED",
                  allowed_sides=("BUY", "SELL"), mode_dry=False, submit_result=1):
    from app.kis_paper import account_scope
    from app.kis_paper import one_click_session as ocs
    from app.auto_paper import paper_auto_mode

    monkeypatch.setattr(account_scope, "current_account_scope", lambda settings=None: ACCOUNT)
    monkeypatch.setattr(mr, "reconcile", lambda *a, **k: asyncio.sleep(0, result=recs.pop(0)))
    submitted = []

    async def fake_submit(*args, **kwargs):
        submitted.append(kwargs.get("max_symbols"))
        return submit_result

    monkeypatch.setattr(mr, "_submit_sells", fake_submit)
    monkeypatch.setattr(ocs, "load_approval", lambda db=None: {
        "status": approval_status, "target_date": mr.RECOVERY_DATE,
        "allowed_sides": list(allowed_sides), "account_fingerprint": "test",
    })
    monkeypatch.setattr(ocs, "public_status", lambda approval: {
        "status": approval_status, "target_date": mr.RECOVERY_DATE,
        "allowed_sides": list(allowed_sides),
    })
    monkeypatch.setattr(ocs, "session_exit_authorized", lambda *a, **k: True)
    monkeypatch.setattr(ocs, "effective_exit_settings", lambda s, **k: s)
    monkeypatch.setattr(paper_auto_mode, "resolve_paper_auto_mode", lambda s: SimpleNamespace(
        mode="KIS_REALTIME_PAPER_AUTO", kis_realtime=True, dry_run=mode_dry,
    ))
    return submitted


def test_monday_0905_recovery_records_separate_proof_and_keeps_incident(factory, monkeypatch):
    record_incident(factory)
    before = reconciliation(mr.INCIDENT_HOLDINGS)
    after = reconciliation({s: 0 for s in mr.INCIDENT_HOLDINGS}, filled=172,
                           eod_fills=mr.INCIDENT_HOLDINGS)
    submitted = install_fakes(monkeypatch, factory, [before, after])
    result = asyncio.run(mr.run_morning_recovery_step(
        now=MONDAY, settings=make_settings(), session_factory=factory,
        broker=object(), broker_is_kis_paper=True,
    ))
    assert result["status"] == mr.VERIFIED
    assert result["orders_submitted_this_step"] == 1
    assert submitted == [1]
    db = factory()
    incident = db.query(EodLiquidationState).one()
    proof = mr.get_verification(db, account_key=ACCOUNT)
    assert incident.state == "FLATTEN_INCOMPLETE"
    assert proof.status == mr.VERIFIED and proof.verified_at is not None
    assert mr.recovery_buy_block_reason(db, now=MONDAY, account_key=ACCOUNT) is None
    from app.kis_paper.intraday_buy_gate import eod_incident_block
    assert eod_incident_block(db, MONDAY, account_scope=ACCOUNT) is None
    db.close()


def test_open_or_partial_sell_waits_and_never_submits_duplicate(factory, monkeypatch):
    record_incident(factory)
    partial = reconciliation(mr.INCIDENT_HOLDINGS, open_orders=3, filled=20)
    submitted = install_fakes(monkeypatch, factory, [partial])
    result = asyncio.run(mr.run_morning_recovery_step(
        now=MONDAY, settings=make_settings(), session_factory=factory,
        broker=object(), broker_is_kis_paper=True,
    ))
    assert result["status"] == "WAITING"
    assert result["reason_code"] == "RECOVERY_ACCOUNT_OPEN_ORDERS_PRESENT"
    assert submitted == []
    db = factory()
    assert mr.get_verification(db, account_key=ACCOUNT).status == "WAITING"
    assert mr.recovery_buy_block_reason(db, now=MONDAY, account_key=ACCOUNT) == mr.INCIDENT_DATE
    from app.kis_paper.intraday_buy_gate import eod_incident_block
    assert eod_incident_block(db, MONDAY, account_scope=ACCOUNT) == mr.INCIDENT_DATE
    db.close()


def test_position_query_failure_fails_closed_without_sell(factory, monkeypatch):
    record_incident(factory)
    failed = reconciliation({}, ok=False, error="EOD_POSITIONS_QUERY_FAILED")
    submitted = install_fakes(monkeypatch, factory, [failed])
    result = asyncio.run(mr.run_morning_recovery_step(
        now=MONDAY, settings=make_settings(), session_factory=factory,
        broker=object(), broker_is_kis_paper=True,
    ))
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "EOD_POSITIONS_QUERY_FAILED"
    assert submitted == []


def test_exit_only_approval_allows_recovery_sell_but_not_buy_authority(factory, monkeypatch):
    record_incident(factory)
    submitted = install_fakes(
        monkeypatch, factory,
        [reconciliation(mr.INCIDENT_HOLDINGS),
         reconciliation(mr.INCIDENT_HOLDINGS, open_orders=1)],
        approval_status="EXIT_ONLY", allowed_sides=("SELL",),
    )
    result = asyncio.run(mr.run_morning_recovery_step(
        now=MONDAY, settings=make_settings(), session_factory=factory,
        broker=object(), broker_is_kis_paper=True,
    ))
    assert result["status"] == "WAITING"
    assert submitted == [1]


def test_live_or_virtual_mode_never_sends_recovery_order(factory, monkeypatch):
    record_incident(factory)
    submitted = install_fakes(monkeypatch, factory, [], mode_dry=True)
    live = asyncio.run(mr.run_morning_recovery_step(
        now=MONDAY, settings=make_settings(enable_live_trading=True),
        session_factory=factory, broker=object(), broker_is_kis_paper=True,
    ))
    assert live["reason_code"] == "RECOVERY_LIVE_ENABLED"
    virtual = asyncio.run(mr.run_morning_recovery_step(
        now=MONDAY, settings=make_settings(), session_factory=factory,
        broker=object(), broker_is_kis_paper=True,
    ))
    assert virtual["reason_code"] == "RECOVERY_PAPER_ORDER_PATH_NOT_ARMED"
    assert submitted == []
