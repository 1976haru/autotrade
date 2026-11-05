"""ACCOUNT ISOLATION FINAL SAFETY GATE — EOD 계좌 격리 · 체결 중복 처리 · 마이그레이션 0030/0031.

실제 KIS 호출 0건(FakeKisPaperBroker), 메모리/임시 SQLite 만 사용. 운영 DB 무접촉.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.kis_paper.account_scope as acs
from app.brokers.base import OrderRequest, OrderSide
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import EodLiquidationState
from app.execution.order_router import route_order
from app.kis_paper.eod_liquidation import EodState, acknowledge_incident, get_state_row
from app.kis_paper.intraday_buy_gate import BUY_BLOCKED_BY_EOD_INCIDENT, program_buy_block_reason
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from tests.test_kis_paper_intraday_flat_by_close import FakeKisPaperBroker, kst, make_settings, step

ROOT = Path(__file__).resolve().parents[2]
NEW = acs.account_id_for("1234567801")            # FakeKisPaperBroker 계좌
OTHER = acs.account_id_for("5555555501")
PREV_DAY = "2026-10-21"                            # TRADING_DAY(10-22) 직전 거래일
TODAY = "2026-10-22"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


def use_scope(monkeypatch, scope):
    monkeypatch.setattr(acs, "current_account_scope", lambda settings=None: scope)


def eod_row(db, date, key, state, *, final=True, acked=False):
    row = EodLiquidationState(trading_date=date, account_key=key, state=state,
                              reason_code="EOD_FINAL_RECONCILE_FAILED", summary={},
                              final_verdict_at=datetime(2026, 10, 21, 9, 0) if final else None,
                              incident_acknowledged=acked)
    db.add(row)
    db.commit()
    return row


def buy(db, broker, now):
    return asyncio.run(route_order(
        order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1, trade_reason="kis_paper_auto"),
        requested_by_ai=False, mode=OperationMode.PAPER, broker=broker,
        risk=RiskManager(RiskPolicy()), db=db, now=now))


# ══ 1. EOD 구계좌·신규계좌 격리 ══════════════════════════════════════════════


def test_legacy_incident_blocks_new_account_buy_until_acknowledged(db, monkeypatch):
    use_scope(monkeypatch, NEW)
    eod_row(db, PREV_DAY, "", EodState.FLATTEN_INCOMPLETE.value)          # 구계좌 미완료
    code, msg = program_buy_block_reason(db, settings=make_settings(), now=kst(10, 0))
    assert code == BUY_BLOCKED_BY_EOD_INCIDENT and "LEGACY_ACCOUNT" in msg
    broker = FakeKisPaperBroker({})
    assert buy(db, broker, kst(10, 0)).decision == RiskDecision.REJECTED and broker.place_calls == []


def test_ack_legacy_targets_only_legacy_row_audited_and_survives_restart(session_factory, db, monkeypatch):
    use_scope(monkeypatch, NEW)
    legacy = eod_row(db, PREV_DAY, "", EodState.FLATTEN_INCOMPLETE.value)
    mine = eod_row(db, PREV_DAY, NEW, EodState.FLAT_CONFIRMED.value)
    acknowledge_incident(db, trading_date=PREV_DAY, decided_by="operator-1",
                         note="구계좌 3종목 KIS 앱 확인", account_key="", now=kst(9, 0))
    fresh = session_factory()                                            # 재시작 = 새 세션
    lrow = fresh.get(EodLiquidationState, legacy.id)
    assert lrow.incident_acknowledged and lrow.acknowledged_by == "operator-1"
    assert lrow.ack_note and lrow.acknowledged_at is not None
    assert lrow.state == EodState.FLATTEN_INCOMPLETE.value              # 사고 기록 보존(완료 처리 아님)
    assert fresh.get(EodLiquidationState, mine.id).incident_acknowledged is False
    assert program_buy_block_reason(fresh, settings=make_settings(), now=kst(10, 0))[0] is None
    broker = FakeKisPaperBroker({})
    assert buy(fresh, broker, kst(10, 0)).decision == RiskDecision.APPROVED and len(broker.place_calls) == 1


def test_ack_without_account_never_touches_legacy(db, monkeypatch):
    use_scope(monkeypatch, NEW)
    legacy = eod_row(db, PREV_DAY, "", EodState.FLATTEN_INCOMPLETE.value)
    with pytest.raises(LookupError):
        acknowledge_incident(db, trading_date=PREV_DAY, decided_by="op")    # 현재 계좌 행 없음
    db.refresh(legacy)
    assert legacy.incident_acknowledged is False


def test_ack_api_requires_explicit_legacy_target(db, monkeypatch):
    from fastapi import HTTPException

    from app.api.routes_kis_paper import _EodAckBody, post_kis_paper_eod_acknowledge
    use_scope(monkeypatch, NEW)
    eod_row(db, PREV_DAY, "", EodState.FLATTEN_INCOMPLETE.value)
    with pytest.raises(HTTPException) as e404:
        post_kis_paper_eod_acknowledge(_EodAckBody(trading_date=PREV_DAY, decided_by="op"), db)
    assert e404.value.status_code == 404
    with pytest.raises(HTTPException) as e400:
        post_kis_paper_eod_acknowledge(_EodAckBody(trading_date=PREV_DAY, decided_by="op", account="5555"), db)
    assert e400.value.status_code == 400
    out = post_kis_paper_eod_acknowledge(
        _EodAckBody(trading_date=PREV_DAY, decided_by="op", account="LEGACY_ACCOUNT"), db)
    assert out["account"] == "LEGACY_ACCOUNT" and out["broker_order_sent"] is False


def test_same_day_switch_new_account_eod_runs_own_row_flat_with_zero(session_factory, db, monkeypatch):
    # 같은 거래일 구계좌 EOD 가 이미 FLATTEN_INCOMPLETE 로 확정 → 새 계좌는 자기 행으로 따로 판정.
    use_scope(monkeypatch, NEW)
    legacy = eod_row(db, TODAY, "", EodState.FLATTEN_INCOMPLETE.value)
    broker = FakeKisPaperBroker({})                                     # 새 계좌 보유 0 · 미체결 0
    step(session_factory, broker, kst(15, 16))
    out = step(session_factory, broker, kst(15, 30, 1))
    assert out["state"] == EodState.FLAT_CONFIRMED.value and broker.place_calls == []
    fresh = session_factory()
    assert get_state_row(fresh, TODAY, NEW).state == EodState.FLAT_CONFIRMED.value
    lrow = fresh.get(EodLiquidationState, legacy.id)
    assert lrow.state == EodState.FLATTEN_INCOMPLETE.value and lrow.incident_acknowledged is False


def test_new_account_balance_success_flat_vs_failure_incomplete(session_factory, db, monkeypatch):
    use_scope(monkeypatch, NEW)
    legacy = eod_row(db, PREV_DAY, "", EodState.FLATTEN_INCOMPLETE.value)
    ok = FakeKisPaperBroker({})
    assert step(session_factory, ok, kst(15, 30, 1))["state"] == EodState.FLAT_CONFIRMED.value

    use_scope(monkeypatch, OTHER)                                        # 다른 날 다른 계좌 — 조회 실패
    bad = FakeKisPaperBroker({})
    bad.fail_positions = True
    out = step(session_factory, bad, kst(15, 30, 1))
    assert out["state"] != EodState.FLAT_CONFIRMED.value and bad.place_calls == []
    fresh = session_factory()
    assert get_state_row(fresh, TODAY, NEW).state == EodState.FLAT_CONFIRMED.value   # 서로 간섭 없음
    assert fresh.get(EodLiquidationState, legacy.id).state == EodState.FLATTEN_INCOMPLETE.value


def test_account_incident_blocks_only_that_account(db, monkeypatch):
    eod_row(db, PREV_DAY, NEW, EodState.FLATTEN_INCOMPLETE.value)
    use_scope(monkeypatch, OTHER)
    assert program_buy_block_reason(db, settings=make_settings(), now=kst(10, 0))[0] is None
    use_scope(monkeypatch, NEW)
    assert program_buy_block_reason(db, settings=make_settings(), now=kst(10, 0))[0] == BUY_BLOCKED_BY_EOD_INCIDENT


def test_unscoped_mode_keeps_previous_single_row_behavior(db, monkeypatch):
    use_scope(monkeypatch, None)                                         # SIMULATION/Mock
    eod_row(db, PREV_DAY, "", EodState.FLATTEN_INCOMPLETE.value)
    assert program_buy_block_reason(db, settings=make_settings(), now=kst(10, 0))[0] == BUY_BLOCKED_BY_EOD_INCIDENT


# ══ 2. 체결 중복 처리 ══════════════════════════════════════════════════════


def r(i, sym, side, filled, *, odno=None, day=1, acct=None, reason="kis_paper_auto", minute=0):
    return SimpleNamespace(id=i, symbol=sym, side=side, filled_quantity=filled, broker_order_id=odno,
                           trade_reason=reason, account_id=acct,
                           created_at=datetime(2026, 7, day, 1, minute))


def net(rows):
    return {k: v for k, v in acs.program_net_quantities(rows).items() if v}


def test_order_number_reused_on_another_trading_day():
    assert net([r(1, "A", "BUY", 10, odno="0001", day=1), r(2, "A", "SELL", 10, odno="0001", day=2)]) == {}


def test_same_order_number_in_two_accounts_is_two_orders():
    assert net([r(1, "A", "BUY", 5, odno="0001", acct=NEW), r(2, "A", "BUY", 4, odno="0001", acct=OTHER)]) == {"A": 9}


def test_partial_fills_3_and_7_of_one_order_are_not_lost():
    # 한 주문의 부분체결은 *누적* 스냅샷(3 → 10)으로 기록된다 — 최대값 = 10.
    assert net([r(1, "A", "BUY", 3, odno="0001"), r(2, "A", "BUY", 10, odno="0001", minute=1)]) == {"A": 10}
    # 서로 다른 두 주문(3주, 7주)은 각각 반영.
    assert net([r(1, "A", "BUY", 3, odno="0001"), r(2, "A", "BUY", 7, odno="0002", minute=1)]) == {"A": 10}


def test_same_fill_event_delivered_twice_counted_once():
    assert net([r(1, "A", "BUY", 6, odno="0001"), r(2, "A", "BUY", 6, odno="0001")]) == {"A": 6}


def test_cumulative_snapshots_updated_many_times():
    rows = [r(i, "A", "BUY", q, odno="0001", minute=i) for i, q in enumerate((1, 4, 4, 9, 10))]
    assert net(rows) == {"A": 10}


def test_manual_sell_all_fill_after_cancel_and_unfilled_sell():
    rows = [r(1, "A", "BUY", 10, odno="0001"),
            r(2, "A", "SELL", 0, odno="0002", minute=1),                     # 미체결 매도 — 차감 X
            r(3, "B", "BUY", 3, odno="0003", minute=2),                       # 10주 주문 3주 체결 후 취소
            r(4, "A", "SELL", 10, odno="0004", reason="manual_sell_all", minute=3)]
    assert net(rows) == {"B": 3}


def test_rows_without_order_number_are_never_merged():
    assert net([r(1, "A", "BUY", 2), r(2, "A", "BUY", 2)]) == {"A": 4}


# ══ 3. 마이그레이션 0030/0031 ═════════════════════════════════════════════════


def _cfg(dbp: Path):
    from alembic.config import Config
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{dbp.as_posix()}")
    return cfg


@pytest.fixture
def mig_db(tmp_path, monkeypatch):
    dbp = tmp_path / "ops_copy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{dbp.as_posix()}")
    return dbp


def _digest(con, table, skip=()):
    cols = [c[1] for c in con.execute(f"pragma table_info({table})") if c[1] not in skip]
    rows = con.execute(f"select {','.join(cols)} from {table} order by id").fetchall()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest(), len(rows)


def _version(dbp):
    con = sqlite3.connect(dbp)
    try:
        return con.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        con.close()


def _seed_0029(dbp):
    from alembic import command
    command.upgrade(_cfg(dbp), "0029")
    con = sqlite3.connect(dbp)
    for i in range(40):
        con.execute("insert into order_audit_log (created_at, mode, requested_by_ai, symbol, side, quantity,"
                    " order_type, latest_price, decision, reasons, archived, executed, filled_quantity, message,"
                    " trade_reason, broker_order_id, broker_status) values (?, 'PAPER', 0, ?, ?, 10, 'MARKET', 1,"
                    " 'APPROVED', '[]', 0, 1, ?, 'kept', ?, ?, 'FILLED')",
                    (f"2026-08-{(i % 20) + 1:02d} 01:00:00", f"{i % 5:06d}", "BUY" if i % 2 else "SELL",
                     i % 11, "kis_paper_auto" if i % 3 else "manual_sell_all", f"{i:010d}"))
    con.execute("insert into eod_liquidation_state (trading_date, state, created_at, updated_at, last_reconcile_ok,"
                " reason_code, summary, incident_acknowledged, is_live_authorization) values ('2026-10-27',"
                " 'FLATTEN_INCOMPLETE', '2026-10-27 09:22:24', '2026-10-27 09:22:24', 0,"
                " 'EOD_FINAL_RECONCILE_FAILED', '{}', 0, 0)")
    con.commit()
    out = (_digest(con, "order_audit_log", ("account_id",)), _digest(con, "eod_liquidation_state", ("account_key",)))
    con.close()
    return out


def test_upgrade_preserves_rows_and_isolates_eod(mig_db):
    from alembic import command
    (od, on), (ed, en) = _seed_0029(mig_db)
    command.upgrade(_cfg(mig_db), "head")
    assert _version(mig_db) == "0032"
    con = sqlite3.connect(mig_db)
    assert _digest(con, "order_audit_log", ("account_id",)) == (od, on)
    assert _digest(con, "eod_liquidation_state", ("account_key",)) == (ed, en)
    assert con.execute("select count(*) from order_audit_log where account_id is not null").fetchone()[0] == 0
    assert con.execute("select account_key from eod_liquidation_state").fetchone()[0] == ""
    # (거래일, 계좌) 유일: 같은 날 다른 계좌 행 허용, 같은 계좌 중복 거부.
    con.execute("insert into eod_liquidation_state (trading_date, account_key, state, created_at, updated_at,"
                " last_reconcile_ok, summary, incident_acknowledged, is_live_authorization) values"
                f" ('2026-10-27', '{NEW}', 'IDLE', '2026-10-28', '2026-10-28', 0, '{{}}', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("insert into eod_liquidation_state (trading_date, account_key, state, created_at, updated_at,"
                    " last_reconcile_ok, summary, incident_acknowledged, is_live_authorization) values"
                    f" ('2026-10-27', '{NEW}', 'IDLE', '2026-10-28', '2026-10-28', 0, '{{}}', 0, 0)")
    con.rollback()
    con.close()


def test_new_rows_get_account_id_after_upgrade(mig_db):
    from alembic import command
    _seed_0029(mig_db)
    command.upgrade(_cfg(mig_db), "head")
    engine = create_engine(f"sqlite:///{mig_db.as_posix()}")
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    broker = FakeKisPaperBroker({})
    res = buy(s, broker, kst(10, 0))
    assert res.audit.account_id == NEW
    assert acs.legacy_unverified_holdings(s)                     # 구계좌 기록은 LEGACY 로 격리 보고
    assert acs.program_holdings(s, NEW) == {}                    # 접수만(체결 0) — 보유 아님
    res.audit.filled_quantity = 1                                # 체결조회가 누적 체결 기록
    s.commit()
    assert acs.program_holdings(s, NEW) == {"005930": 1}         # 새 계좌는 자기 체결만
    s.close()
    engine.dispose()


def test_rerunning_upgrade_is_a_noop(mig_db):
    from alembic import command
    _seed_0029(mig_db)
    command.upgrade(_cfg(mig_db), "head")
    con = sqlite3.connect(mig_db)
    before = (_digest(con, "order_audit_log"), _digest(con, "eod_liquidation_state"))
    con.close()
    command.upgrade(_cfg(mig_db), "head")
    con = sqlite3.connect(mig_db)
    assert (_digest(con, "order_audit_log"), _digest(con, "eod_liquidation_state")) == before
    con.close()


def test_downgrade_to_0029_preserves_all_existing_data(mig_db):
    from alembic import command
    (od, on), (ed, en) = _seed_0029(mig_db)
    command.upgrade(_cfg(mig_db), "head")
    command.downgrade(_cfg(mig_db), "0029")
    assert _version(mig_db) == "0029"
    con = sqlite3.connect(mig_db)
    assert "account_id" not in [c[1] for c in con.execute("pragma table_info(order_audit_log)")]
    assert "account_key" not in [c[1] for c in con.execute("pragma table_info(eod_liquidation_state)")]
    assert _digest(con, "order_audit_log") == (od, on)               # 실제 데이터 동일 (번호만 바뀐 게 아님)
    assert _digest(con, "eod_liquidation_state") == (ed, en)
    uniq = [r for r in con.execute("pragma index_list(eod_liquidation_state)") if r[2] == 1]
    assert any("trading_date" in [c[2] for c in con.execute(f"pragma index_info('{u[1]}')")] for u in uniq)
    con.close()


def test_downgrade_refuses_when_it_would_lose_account_rows(mig_db):
    from alembic import command
    _seed_0029(mig_db)
    command.upgrade(_cfg(mig_db), "head")
    con = sqlite3.connect(mig_db)
    con.execute("insert into eod_liquidation_state (trading_date, account_key, state, created_at, updated_at,"
                " last_reconcile_ok, summary, incident_acknowledged, is_live_authorization) values"
                f" ('2026-10-27', '{NEW}', 'FLAT_CONFIRMED', '2026-10-28', '2026-10-28', 0, '{{}}', 0, 0)")
    con.commit()
    before = _digest(con, "eod_liquidation_state")
    con.close()
    with pytest.raises(RuntimeError, match="downgrade 0031 refused"):
        command.downgrade(_cfg(mig_db), "0030")
    con = sqlite3.connect(mig_db)
    assert _digest(con, "eod_liquidation_state") == before and _version(mig_db) == "0031"
    con.close()


def test_failed_upgrade_is_recoverable_from_automatic_backup(mig_db):
    from alembic import command

    from app.db.session import backup_sqlite_before_upgrade
    _seed_0029(mig_db)
    con = sqlite3.connect(mig_db)
    pre = (_digest(con, "order_audit_log"), _digest(con, "eod_liquidation_state"))
    # 0031 이 중간 실패하도록 같은 이름 인덱스를 미리 만들어 둔다(부분 적용 상태 유발).
    con.execute("create index uq_eod_liquidation_state_date_account on order_audit_log(id)")
    con.commit()
    con.close()
    cfg = _cfg(mig_db)
    bak = backup_sqlite_before_upgrade(cfg, url=f"sqlite:///{mig_db.as_posix()}")
    assert bak is not None and bak.exists() and ".pre-0032-" in bak.name
    with pytest.raises(Exception):
        command.upgrade(cfg, "head")
    assert _version(mig_db) != "0032"                                # 실패 — head 아님
    shutil.copyfile(bak, mig_db)                                     # 운영자 복구 = 백업 복원
    con = sqlite3.connect(mig_db)
    assert (_digest(con, "order_audit_log"), _digest(con, "eod_liquidation_state")) == pre
    con.execute("drop index uq_eod_liquidation_state_date_account")  # 원인 제거 후 재실행
    con.commit()
    con.close()
    assert _version(mig_db) == "0029"
    command.upgrade(cfg, "head")
    assert _version(mig_db) == "0032"


def test_backup_skipped_when_already_at_head_or_not_sqlite_file(mig_db):
    from alembic import command

    from app.db.session import backup_sqlite_before_upgrade
    _seed_0029(mig_db)
    command.upgrade(_cfg(mig_db), "head")
    assert backup_sqlite_before_upgrade(_cfg(mig_db), url=f"sqlite:///{mig_db.as_posix()}") is None
    assert backup_sqlite_before_upgrade(_cfg(mig_db), url="sqlite://") is None
