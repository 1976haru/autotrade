"""KIS-PAPER-P0-SAFETY-FIX-V1 (2026-10-24) — 킬스위치 단계 정책 · EOD 독립 실행 · 정상 종료.

P0: LEVEL_1 안내("신규 매수만 차단, 청산 허용")와 달리 RiskManager/EOD 가 LEVEL_1 에서도
청산 매도·장마감 강제청산·미체결 매수 취소를 전부 막았다. 또한 EOD 는 BackgroundTickDriver
안에서만 돌아 실행기 OFF(EXE 기본) 시 프로그램 보유 청산 관리가 멈췄다.

가짜 브로커 + 주입 시계만 사용 — 실제 KIS 호출 0건, 실제 프로세스 신호 0건.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auto_paper.background_driver import BackgroundTickDriver, EodGuardian
from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode
from app.db.models import EmergencyStopEvent, EodLiquidationState, OrderAuditLog
from app.execution.order_router import route_cancel, route_order
from app.kis_paper.eod_liquidation import (
    EodState,
    acquire_lease,
    get_state_row,
    release_owner_leases,
    run_eod_step,
)
from app.risk.emergency_stop import (
    KillSwitchLevel,
    apply_kill_switch_to_risk,
    restore_kill_switch_from_db,
)
from app.risk.kill_switch_policy import (
    KILL_SWITCH_POLICY_VERSION,
    emergency_order_policy,
    exits_allowed,
)
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from tests.test_kis_paper_intraday_flat_by_close import (
    FakeKisPaperBroker,
    kst,
    make_settings,
    open_buy_row,
    seed_bot_position,
    seed_today_program_buy,
    step,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


def rm_at(level: KillSwitchLevel, *, version: int = KILL_SWITCH_POLICY_VERSION) -> RiskManager:
    rm = RiskManager(RiskPolicy())
    apply_kill_switch_to_risk(rm, level, policy_version=version)
    return rm


def legacy_rm() -> RiskManager:
    """이전 정책으로 켜진 LEVEL_1 (예: EXE 의 2026-05-26 정지) — strict 유지 대상."""
    return rm_at(KillSwitchLevel.LEVEL_1, version=1)


def evaluate(rm: RiskManager, side: str, qty: int, held: int, *, sellable=None,
             mode=OperationMode.PAPER):
    positions = ([Position(symbol="005930", quantity=held, avg_price=10_000,
                           market_price=10_000, sellable_quantity=sellable)] if held else [])
    return rm.evaluate_order(
        order=OrderRequest(symbol="005930", side=OrderSide(side), quantity=qty),
        mode=mode, balance=Balance(cash=10_000_000, equity=10_000_000, buying_power=10_000_000),
        positions=positions, latest_price=10_000)


# ── 1. LEVEL_1: 신규 BUY 차단 / 보유 이내 SELL 허용 ─────────────────────────────


def test_L1_new_buy_rejected():
    res = evaluate(rm_at(KillSwitchLevel.LEVEL_1), "BUY", 1, held=0)
    assert res.decision == RiskDecision.REJECTED
    assert res.reasons[0] == "emergency stop is enabled"          # 기존 사유 호환
    assert "new BUY blocked" in res.reasons[1]


def test_L1_buy_blocked_as_hard_block_in_check_order():
    from app.risk.risk_manager import RiskContext
    rm = rm_at(KillSwitchLevel.LEVEL_1)
    res = rm.check_order(
        OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
        RiskContext(mode=OperationMode.PAPER,
                    balance=Balance(cash=10**7, equity=10**7, buying_power=10**7),
                    positions=[], latest_price=10_000))
    assert res.decision == RiskDecision.BLOCKED and res.blocked_by == "emergency_stop"


def test_L1_sell_one_held_share_allowed():
    res = evaluate(rm_at(KillSwitchLevel.LEVEL_1), "SELL", 1, held=1)
    assert res.decision == RiskDecision.APPROVED, res.reasons


def test_L1_sell_zero_holding_rejected():
    res = evaluate(rm_at(KillSwitchLevel.LEVEL_1), "SELL", 1, held=0)
    assert res.decision == RiskDecision.REJECTED
    assert "broker-confirmed holding" in res.reasons[1]


def test_L1_sell_more_than_held_or_sellable_rejected_no_short():
    rm = rm_at(KillSwitchLevel.LEVEL_1)
    assert evaluate(rm, "SELL", 3, held=2).decision == RiskDecision.REJECTED
    # 주문가능수량(미결제 등) 초과도 거부.
    assert evaluate(rm, "SELL", 2, held=2, sellable=1).decision == RiskDecision.REJECTED
    assert evaluate(rm, "SELL", 1, held=2, sellable=1).decision == RiskDecision.APPROVED


def test_L1_exit_refused_outside_paper_or_simulation():
    res = evaluate(rm_at(KillSwitchLevel.LEVEL_1), "SELL", 1, held=1,
                   mode=OperationMode.LIVE_MANUAL_APPROVAL)
    assert res.decision == RiskDecision.REJECTED
    assert "PAPER/SIMULATION" in res.reasons[1]


# ── 2. LEVEL_2/3 · legacy: 강한 정지 유지 ───────────────────────────────────────


@pytest.mark.parametrize("level", [KillSwitchLevel.LEVEL_2, KillSwitchLevel.LEVEL_3])
def test_L2_L3_block_every_order(level):
    rm = rm_at(level)
    assert evaluate(rm, "SELL", 1, held=1).reasons == ["emergency stop is enabled"]
    assert evaluate(rm, "BUY", 1, held=0).decision == RiskDecision.REJECTED
    pol = emergency_order_policy(rm)
    assert not pol.allows_eod_forced_exit and not pol.legacy_strict
    assert "MTS/HTS" in pol.operator_action_ko


def test_boolean_only_stop_is_strict():
    rm = RiskManager(RiskPolicy())
    rm.set_emergency_stop(True)          # 자동정지·level 미상 → strict
    assert evaluate(rm, "SELL", 1, held=1).decision == RiskDecision.REJECTED
    assert emergency_order_policy(rm).legacy_strict is True


def test_legacy_L1_event_restored_strict_not_relaxed(db):
    db.add(EmergencyStopEvent(enabled=True, level="LEVEL_1"))      # policy_version NULL
    db.commit()
    rm = RiskManager(RiskPolicy())
    assert restore_kill_switch_from_db(rm, db) == KillSwitchLevel.LEVEL_1
    assert rm.emergency_stop is True
    assert evaluate(rm, "SELL", 1, held=1).decision == RiskDecision.REJECTED
    pol = emergency_order_policy(rm)
    assert pol.legacy_strict and "다시 설정" in pol.operator_action_ko


def test_new_policy_L1_event_restored_relaxed(db):
    db.add(EmergencyStopEvent(enabled=True, level="LEVEL_1",
                              policy_version=KILL_SWITCH_POLICY_VERSION))
    db.commit()
    rm = RiskManager(RiskPolicy())
    restore_kill_switch_from_db(rm, db)
    assert exits_allowed(rm) and evaluate(rm, "SELL", 1, held=1).decision == RiskDecision.APPROVED


def test_api_reapplying_L1_converts_legacy_stop_and_records_version(client):
    risk = client.test_risk_manager      # client fixture 가 주입한 RiskManager
    apply_kill_switch_to_risk(risk, KillSwitchLevel.LEVEL_1, policy_version=1)   # legacy 상태
    try:
        st = client.get("/api/risk/emergency-stop/status").json()
        assert st["order_policy"]["legacy_strict"] is True
        r = client.post("/api/risk/emergency-stop",
                        json={"enabled": True, "level": "LEVEL_1", "decided_by": "op"}).json()
        assert r["order_policy"]["legacy_strict"] is False
        assert r["order_policy"]["allows_eod_forced_exit"] is True
        with client.test_db_factory() as s:
            ev = s.query(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc()).first()
            assert ev.level == "LEVEL_1" and ev.policy_version == KILL_SWITCH_POLICY_VERSION
    finally:
        apply_kill_switch_to_risk(risk, KillSwitchLevel.OFF)


def test_kill_switch_never_auto_released_on_restore(db):
    db.add(EmergencyStopEvent(enabled=True, level="LEVEL_2", policy_version=2))
    db.commit()
    rm = RiskManager(RiskPolicy())
    restore_kill_switch_from_db(rm, db)
    assert rm.emergency_stop is True and str(rm.kill_switch_level) == "LEVEL_2"


# ── 3. EOD coordinator × 단계 ──────────────────────────────────────────────────


def test_L1_eod_forced_exit_sells_program_holding(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    out = step(session_factory, broker, kst(15, 16), risk=rm_at(KillSwitchLevel.LEVEL_1))
    assert [(o.symbol, o.side.value, o.quantity) for o in broker.place_calls] == [("005930", "SELL", 1)]
    assert out["summary"]["kill_switch"]["level"] == "LEVEL_1"
    out2 = step(session_factory, broker, kst(15, 31), risk=rm_at(KillSwitchLevel.LEVEL_1))
    assert out2["state"] == EodState.FLAT_CONFIRMED.value


def test_L1_eod_cancels_open_program_buy(session_factory, db):
    seed_today_program_buy(db, "005930", 10, "0000000777")
    broker = FakeKisPaperBroker({})
    broker.rows.append(open_buy_row("0000000777", "005930", 10))
    step(session_factory, broker, kst(14, 30, 5), risk=rm_at(KillSwitchLevel.LEVEL_1))
    assert broker.cancel_calls == [("0000000777", "06010")]
    cx = db.query(OrderAuditLog).filter(OrderAuditLog.order_type == "CANCEL").one()
    assert cx.decision == "APPROVED"


@pytest.mark.parametrize("make_rm", [lambda: rm_at(KillSwitchLevel.LEVEL_2),
                                     lambda: rm_at(KillSwitchLevel.LEVEL_3), legacy_rm])
def test_strict_stops_block_eod_with_critical_and_incomplete(session_factory, db, make_rm):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    events: list = []
    out = step(session_factory, broker, kst(15, 16), risk=make_rm(), events=events)
    assert out["state"] == EodState.BLOCKED.value and broker.place_calls == []
    assert any(e["level"] == "CRITICAL" and "MTS/HTS" in e["message"] for e in events)
    assert out["summary"]["manual_liquidation_required"] is True
    out2 = step(session_factory, broker, kst(15, 31), risk=make_rm())
    assert out2["state"] == EodState.FLATTEN_INCOMPLETE.value


def test_strict_stop_does_not_cancel_open_buy(session_factory, db):
    seed_today_program_buy(db, "005930", 10, "0000000777")
    broker = FakeKisPaperBroker({})
    broker.rows.append(open_buy_row("0000000777", "005930", 10))
    step(session_factory, broker, kst(14, 30, 5), risk=rm_at(KillSwitchLevel.LEVEL_2))
    assert broker.cancel_calls == []


def test_L1_eod_never_sells_manual_holding(session_factory, db):
    # 프로그램 원장에 없는(수동) 보유는 L1 강제청산 대상이 아니다.
    broker = FakeKisPaperBroker({"005930": 5})
    out = step(session_factory, broker, kst(15, 16), risk=rm_at(KillSwitchLevel.LEVEL_1))
    assert broker.place_calls == []
    assert out["summary"]["unmanaged_holdings"]


def test_L1_eod_mixed_program_and_manual_sells_only_program_qty(session_factory, db):
    seed_bot_position(db, "005930", 2)
    broker = FakeKisPaperBroker({"005930": 7})           # 2 프로그램 + 5 수동
    step(session_factory, broker, kst(15, 16), risk=rm_at(KillSwitchLevel.LEVEL_1))
    assert [(o.symbol, o.quantity) for o in broker.place_calls] == [("005930", 2)]


@pytest.mark.parametrize("attr", ["fail_positions", "fail_orders"])
def test_L1_broker_query_failure_sends_nothing(session_factory, db, attr):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    setattr(broker, attr, True)
    out = step(session_factory, broker, kst(15, 16), risk=rm_at(KillSwitchLevel.LEVEL_1))
    assert broker.place_calls == [] and broker.cancel_calls == []
    assert out["summary"]["reconcile_ok"] is False


def test_L1_restart_does_not_duplicate_eod_sell(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1}, fill_mode="none")
    step(session_factory, broker, kst(15, 16), risk=rm_at(KillSwitchLevel.LEVEL_1), owner="proc-A")
    # 강제 종료 후 재시작한 새 프로세스 — proc-A 의 lease 만료 뒤 이어받아도 재매도 0.
    step(session_factory, broker, kst(15, 18), risk=rm_at(KillSwitchLevel.LEVEL_1), owner="proc-B")
    assert len(broker.place_calls) == 1


# ── 4. route_cancel × 단계 ─────────────────────────────────────────────────────


def _cancel(db, rm, side, cid):
    broker = FakeKisPaperBroker({})
    broker.rows.append(open_buy_row("0000000900", "005930", 3))
    res = asyncio.run(route_cancel(
        original_order_id="0000000900", org_no="06010", symbol="005930", side=side,
        reason="test", mode=OperationMode.PAPER, broker=broker, risk=rm, db=db,
        client_order_id=cid))
    return res, broker


def test_route_cancel_L1_allows_buy_cancel_only(db):
    ok, b1 = _cancel(db, rm_at(KillSwitchLevel.LEVEL_1), OrderSide.BUY, "cxl-t-1")
    assert ok.decision == RiskDecision.APPROVED and len(b1.cancel_calls) == 1
    no, b2 = _cancel(db, rm_at(KillSwitchLevel.LEVEL_1), OrderSide.SELL, "cxl-t-2")
    assert no.decision == RiskDecision.REJECTED and b2.cancel_calls == []


@pytest.mark.parametrize("make_rm", [lambda: rm_at(KillSwitchLevel.LEVEL_2), legacy_rm])
def test_route_cancel_strict_refuses(db, make_rm):
    res, broker = _cancel(db, make_rm(), OrderSide.BUY, "cxl-t-3")
    assert res.decision == RiskDecision.REJECTED and broker.cancel_calls == []


# ── 5. route_order 전체 경로 (L1) ─────────────────────────────────────────────


def _route(db, broker, rm, side, qty, trade_reason=None):
    return asyncio.run(route_order(
        order=OrderRequest(symbol="005930", side=side, quantity=qty, trade_reason=trade_reason),
        requested_by_ai=False, mode=OperationMode.PAPER, broker=broker, risk=rm, db=db))


def test_route_order_L1_buy_rejected_program_sell_executed(db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    rm = rm_at(KillSwitchLevel.LEVEL_1)
    buy = _route(db, broker, rm, OrderSide.BUY, 1, "kis_paper_auto")
    assert buy.decision != RiskDecision.APPROVED and broker.place_calls == []
    sell = _route(db, broker, rm, OrderSide.SELL, 1, "kis_paper_auto")
    assert sell.decision == RiskDecision.APPROVED, sell.reasons
    assert [(o.side.value, o.quantity) for o in broker.place_calls] == [("SELL", 1)]
    audits = db.query(OrderAuditLog).filter(OrderAuditLog.created_at.isnot(None))         .order_by(OrderAuditLog.id).all()
    assert [a.side for a in audits][-2:] == ["BUY", "SELL"]    # 거부도 감사로그 기록


def test_route_order_L1_manual_or_other_owned_sell_rejected(db):
    broker = FakeKisPaperBroker({"005930": 5})                 # 수동 보유 5주, 원장 0
    rm = rm_at(KillSwitchLevel.LEVEL_1)
    manual = _route(db, broker, rm, OrderSide.SELL, 1, "manual")
    assert manual.decision == RiskDecision.REJECTED
    assert "manual/other-owned" in " ".join(manual.reasons)
    no_owner = _route(db, broker, rm, OrderSide.SELL, 1, "kis_paper_auto")
    assert no_owner.decision == RiskDecision.REJECTED
    assert "owned=0" in " ".join(no_owner.reasons)
    assert broker.place_calls == []


def test_route_order_L1_program_sell_capped_to_program_qty(db):
    seed_bot_position(db, "005930", 2)
    broker = FakeKisPaperBroker({"005930": 7})                 # 2 프로그램 + 5 수동
    res = _route(db, broker, rm_at(KillSwitchLevel.LEVEL_1), OrderSide.SELL, 3, "kis_paper_auto")
    assert res.decision == RiskDecision.REJECTED and broker.place_calls == []
    assert "owned=2" in " ".join(res.reasons)


def test_ownership_block_only_applies_to_new_policy_L1(db):
    from app.execution.order_router import emergency_sell_ownership_block as blk
    kw = dict(side=OrderSide.SELL, symbol="005930", quantity=1, trade_reason="manual")
    assert blk(db, RiskManager(RiskPolicy()), **kw) is None              # OFF: 기존 경로
    assert blk(db, rm_at(KillSwitchLevel.LEVEL_2), **kw) is None         # strict 는 RM 이 이미 거부
    assert blk(db, rm_at(KillSwitchLevel.LEVEL_1), **kw) is not None
    assert blk(db, rm_at(KillSwitchLevel.LEVEL_1), side=OrderSide.BUY, symbol="005930",
               quantity=1, trade_reason="manual") is None              # BUY 는 RM 이 거부


def test_permission_gate_uses_same_ownership_helper():
    src = (ROOT / "backend" / "app" / "permission" / "gate.py").read_text(encoding="utf-8")
    body = src.split("async def approve(", 1)[1]
    assert body.index("emergency_sell_ownership_block(") < body.index(
        "_approve_re_eval_blocks_execution(re_eval)")


# ── 6. EOD guardian — 실행기 OFF 에서도 청산 관리 ──────────────────────────────


def _driver(settings, session_factory, broker, rm, *, loop_state="PAUSED"):
    async def eod_fn(*, now, loop_state):
        return await run_eod_step(
            now=now, settings=settings, session_factory=session_factory, broker=broker,
            risk=rm, route_order_fn=route_order, loop_state=loop_state, owner_id="guardian",
            event_sink=lambda **kw: None, credentials_present=True)
    loop = SimpleNamespace(status=lambda now=None: SimpleNamespace(state=loop_state,
                                                                   cycle_count=0))
    return BackgroundTickDriver(settings_provider=lambda: settings, loop_provider=lambda: loop,
                                eod_step_fn=eod_fn)


def test_guardian_flattens_program_holding_with_driver_off(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    settings = make_settings(enable_ai_paper_background_tick=False)   # 실행기 OFF
    drv = _driver(settings, session_factory, broker, RiskManager(RiskPolicy()))
    g = EodGuardian(drv)
    assert drv.is_running is False and g._approved() is True
    asyncio.run(g.run_once(kst(15, 16)))
    assert [(o.side.value, o.quantity) for o in broker.place_calls] == [("SELL", 1)]


def test_guardian_never_buys_or_scans_and_idles_before_cutoff(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    drv = _driver(make_settings(), session_factory, broker, RiskManager(RiskPolicy()))
    res = asyncio.run(EodGuardian(drv).run_once(kst(10, 0)))
    assert res["state"] == EodState.IDLE.value and broker.place_calls == []
    src = (ROOT / "backend" / "app" / "auto_paper" / "background_driver.py").read_text(encoding="utf-8")
    guardian_src = src.split("class EodGuardian", 1)[1].split("def get_eod_guardian", 1)[0]
    code = guardian_src.split('"""', 2)[2]            # docstring 제외한 코드
    for forbidden in ("kis_paper_tick(", "tick_once(", "route_order", "place_order(",
                      "execute_kis_paper_auto_order"):
        assert forbidden not in code


def test_guardian_requires_kis_paper_auto_approval(session_factory, db):
    drv = _driver(make_settings(enable_kis_paper_auto_trading=False), session_factory,
                  FakeKisPaperBroker({}), RiskManager(RiskPolicy()))
    assert EodGuardian(drv)._approved() is False
    drv2 = _driver(make_settings(enable_live_trading=True), session_factory,
                   FakeKisPaperBroker({}), RiskManager(RiskPolicy()))
    assert EodGuardian(drv2)._approved() is False


def test_guardian_skips_when_driver_running(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    drv = _driver(make_settings(), session_factory, broker, RiskManager(RiskPolicy()))
    drv._task = SimpleNamespace(done=lambda: False)      # driver 실행 중으로 간주
    assert asyncio.run(EodGuardian(drv).run_once(kst(15, 16))) is None
    assert broker.place_calls == []


def test_guardian_honors_loop_emergency_stop(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    drv = _driver(make_settings(), session_factory, broker, RiskManager(RiskPolicy()),
                  loop_state="EMERGENCY_STOP")
    out = asyncio.run(EodGuardian(drv).run_once(kst(15, 16)))
    assert out["state"] == EodState.BLOCKED.value and broker.place_calls == []


def test_guardian_wired_in_lifespan_and_status():
    main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "get_eod_guardian()" in main_src and "await eod_guardian.stop()" in main_src
    assert main_src.index("await eod_guardian.stop()") < main_src.index("await bg_driver.stop()")


def test_eod_status_reports_manager(client):
    st = client.get("/api/kis-paper/eod/status").json()
    assert st["eod_manager"] in ("BACKGROUND_DRIVER", "EOD_GUARDIAN", "NONE")
    assert "eod_guardian_running" in st and "kill_switch" in st


# ── 7. 정상 종료 ───────────────────────────────────────────────────────────────


@pytest.fixture
def shutdown_env(monkeypatch):
    from app.system import graceful_shutdown as gs
    calls: list = []
    monkeypatch.setattr(gs, "request_process_exit", lambda: calls.append("exit"))
    monkeypatch.setattr(gs, "_LOOPBACK", gs._LOOPBACK | {"testclient"})
    monkeypatch.setattr(gs, "schedule_exit", lambda delay_seconds=0.3: gs.request_process_exit())
    from app.main import _INSTANCE_ID
    return SimpleNamespace(calls=calls, header={gs.INSTANCE_HEADER: _INSTANCE_ID}, gs=gs)


def test_shutdown_requires_loopback_and_instance(client, monkeypatch):
    from app.system import graceful_shutdown as gs
    calls: list = []
    monkeypatch.setattr(gs, "request_process_exit", lambda: calls.append("exit"))
    r = client.post("/api/system/shutdown", json={"confirm": True})
    assert r.status_code == 403 and r.json()["reason_code"] == "SHUTDOWN_LOOPBACK_ONLY"
    monkeypatch.setattr(gs, "_LOOPBACK", gs._LOOPBACK | {"testclient"})
    r = client.post("/api/system/shutdown", json={"confirm": True},
                    headers={gs.INSTANCE_HEADER: "someone-else"})
    assert r.status_code == 403 and r.json()["reason_code"] == "SHUTDOWN_INSTANCE_MISMATCH"
    assert calls == []


def test_shutdown_safe_state_accepted(client, shutdown_env):
    r = client.post("/api/system/shutdown", json={}, headers=shutdown_env.header)
    assert r.status_code == 200 and r.json()["accepted"] is True
    assert shutdown_env.calls == ["exit"]


def test_shutdown_with_risk_needs_confirmation(client, shutdown_env, monkeypatch):
    monkeypatch.setattr(shutdown_env.gs, "shutdown_preflight", lambda db: {
        "safe_to_quit": False, "eod_step_in_progress": False,
        "safe_without_confirmation": False, "program_holdings": [{"symbol": "005930"}]})
    r = client.post("/api/system/shutdown", json={}, headers=shutdown_env.header)
    assert r.status_code == 409 and r.json()["reason_code"] == "SHUTDOWN_CONFIRMATION_REQUIRED"
    assert shutdown_env.calls == []
    r = client.post("/api/system/shutdown", json={"confirm": True}, headers=shutdown_env.header)
    assert r.status_code == 200 and shutdown_env.calls == ["exit"]


def test_shutdown_preflight_flags_eod_step_in_progress(db, monkeypatch):
    from app.kis_paper import eod_liquidation as eod
    from app.system import graceful_shutdown as gs
    monkeypatch.setattr(eod, "step_in_progress", lambda: True)
    pre = gs.shutdown_preflight(db)
    assert pre["eod_step_in_progress"] is True and pre["safe_without_confirmation"] is False


def test_graceful_stop_waits_for_in_flight_eod_step():
    finished: list = []

    class SlowDriver:
        is_running = False

        def _settings(self):
            return make_settings(ai_paper_tick_interval_seconds=60)

        async def maybe_eod_step(self, now):
            await asyncio.sleep(0.3)          # broker 호출 + audit commit 사이라고 가정
            finished.append("done")
            return {}

    async def scenario():
        g = EodGuardian(SlowDriver())
        assert g.start() is True
        await asyncio.sleep(0.05)             # 단계 진행 중
        await g.stop(grace_seconds=5)
        return g

    g = asyncio.run(scenario())
    assert finished == ["done"] and g.is_running is False


def test_graceful_stop_cancels_only_after_grace():
    async def scenario():
        class Hung:
            is_running = False

            def _settings(self):
                return make_settings()

            async def maybe_eod_step(self, now):
                await asyncio.sleep(30)

        g = EodGuardian(Hung())
        g.start()
        await asyncio.sleep(0.05)
        t0 = asyncio.get_running_loop().time()
        await g.stop(grace_seconds=0.2)
        return asyncio.get_running_loop().time() - t0, g

    elapsed, g = asyncio.run(scenario())
    assert elapsed < 5 and g.is_running is False


def test_release_owner_leases_only_own(db):
    now = kst(15, 16)
    assert acquire_lease(db, "2026-10-22", "proc-A", now, ttl_seconds=60)
    db.add(EodLiquidationState(trading_date="2026-10-21", state="IDLE",
                               lease_owner="proc-B", lease_expires_at=now.replace(tzinfo=None)))
    db.commit()
    assert release_owner_leases(db, "proc-A") == 1
    assert get_state_row(db, "2026-10-22").lease_owner is None
    assert get_state_row(db, "2026-10-21").lease_owner == "proc-B"


def test_stale_lease_from_killed_process_expires(db):
    now = kst(15, 16)
    assert acquire_lease(db, "2026-10-22", "dead-proc", now, ttl_seconds=45)
    assert not acquire_lease(db, "2026-10-22", "new-proc", now + timedelta(seconds=10), ttl_seconds=45)
    assert acquire_lease(db, "2026-10-22", "new-proc", now + timedelta(seconds=50), ttl_seconds=45)


def test_hard_kill_mid_transaction_keeps_sqlite_consistent(tmp_path):
    """강제 종료(TerminateProcess) 중 트랜잭션 → 커밋분 보존, 미커밋분 롤백, 무결성 OK."""
    dbp = tmp_path / "kill.db"
    code = (
        "import sqlite3,time,sys\n"
        f"c=sqlite3.connect(r'{dbp}')\n"
        "c.execute('create table t(i integer)')\n"
        "c.execute('insert into t values(1)'); c.commit()\n"
        "c.execute('begin')\n"
        "[c.execute('insert into t values(?)',(i,)) for i in range(2,5000)]\n"
        "print('ready',flush=True); time.sleep(30)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    p.kill()
    p.wait(timeout=10)
    import sqlite3
    c = sqlite3.connect(dbp)
    assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert c.execute("select count(*) from t").fetchone()[0] == 1
    c.close()


# ── 8. 데스크톱(Rust) 정상 종료 경로 정적 검사 ─────────────────────────────────


def _lib() -> str:
    return (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")


def test_rust_safe_quit_is_graceful_first_force_last():
    src = _lib()
    body = src.split("fn confirm_quit(", 1)[1].split("\n}\n", 1)[0]
    assert body.index("graceful_stop_backend(") < body.index("kill_sidecar(")
    assert "std::thread::spawn" in body                     # UI 스레드 차단 0
    g = src.split("fn graceful_stop_backend(", 1)[1].split("\n}\n", 1)[0]
    assert "post_graceful_shutdown(" in g and "SIDECAR_EXITED" in g
    assert "SIDECAR_EXITED.store(true" in src               # Terminated 이벤트로 종료 확인
    post = src.split("fn post_graceful_shutdown(", 1)[1].split("\n}\n", 1)[0]
    assert "/api/system/shutdown" in post and "X-Autotrade-Instance" in post
    assert "127, 0, 0, 1" in post                           # loopback 고정


def test_rust_instance_id_sanitized_before_header():
    src = _lib()
    g = src.split("fn graceful_stop_backend(", 1)[1].split("\n}\n", 1)[0]
    assert "sanitize_instance_id" in g
    san = src.split("fn sanitize_instance_id(", 1)[1].split("\n}\n", 1)[0]
    assert "is_ascii_alphanumeric" in san
