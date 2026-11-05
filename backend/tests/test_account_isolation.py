"""P0 ACCOUNT-ISOLATION — 계좌별 원장 분리 + 프로그램 보유 재계산 검증.

실제 KIS 호출 0건: FakeKisPaperBroker(KisBrokerAdapter 상속, 네트워크만 메모리) + 메모리 SQLite.
시나리오: 구계좌/새 계좌 분리 · LEGACY 보존 · 수동매도 반영 · 부분체결/취소 · 중복 체결 ·
다른 계좌 미체결 배제 · 잔고 조회 실패 · 14:30 신규매수 금지 · 15:00~15:30 EOD · 긴급정지 ·
일일 보고서 계좌 구분 · 일일 손익 · 마이그레이션(기존 행 무변경).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
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
from app.db.models import OrderAuditLog
from app.execution.order_router import emergency_sell_ownership_block, route_order
from app.kis_paper.eod_liquidation import EodState
from app.risk.emergency_stop import KillSwitchLevel
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from tests.test_kis_paper_intraday_flat_by_close import FakeKisPaperBroker, kst, step
from tests.test_kis_paper_p0_safety_fix import rm_at

ROOT = Path(__file__).resolve().parents[2]
NEW_ACCT = "1234567801"                       # FakeKisPaperBroker 의 계좌
NEW = acs.account_id_for(NEW_ACCT)
OTHER = acs.account_id_for("5555555501")


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


@pytest.fixture
def scoped(monkeypatch):
    """운영 계좌 = NEW (설정 기준 스코프). 모든 원장 조회가 이 스코프를 쓴다."""
    monkeypatch.setattr(acs, "current_account_scope", lambda settings=None: NEW)
    return NEW


_T0 = datetime(2026, 10, 21, 1, 0)


def add(db, symbol, side, qty, *, filled=None, reason="kis_paper_auto", account=None, odno=None,
        status="FILLED", at=None, decision="APPROVED", executed=True):
    row = OrderAuditLog(
        created_at=at or _T0, mode="PAPER", requested_by_ai=False, symbol=symbol, side=side,
        quantity=qty, order_type="MARKET", latest_price=10_000, decision=decision, reasons=[],
        trade_reason=reason, executed=executed, broker_order_id=odno, broker_status=status,
        filled_quantity=qty if filled is None else filled, account_id=account,
    )
    db.add(row)
    db.commit()
    return row


# ── 스코프 값 ────────────────────────────────────────────────────────────────


def test_account_id_is_hashed_and_normalized():
    a = acs.account_id_for("12345678-01")
    assert a == acs.account_id_for("1234567801") and a.startswith("kis-paper-")
    assert "12345678" not in a and "1234567801" not in a
    assert acs.account_id_for("1234567") is None and acs.account_id_for("") is None
    assert acs.account_id_for("1234567801", is_paper=False).startswith("kis-live-")


def test_scope_required_only_in_kis_modes():
    paper = SimpleNamespace(default_mode="PAPER", kis_account_no="", kis_is_paper=True)
    assert acs.account_scope_block_reason(paper) == acs.ACCOUNT_SCOPE_UNKNOWN
    ok = SimpleNamespace(default_mode="PAPER", kis_account_no=NEW_ACCT, kis_is_paper=True)
    assert acs.account_scope_block_reason(ok) is None and acs.current_account_scope(ok) == NEW
    sim = SimpleNamespace(default_mode="SIMULATION", kis_account_no="", kis_is_paper=True)
    assert acs.account_scope_block_reason(sim) is None and acs.current_account_scope(sim) is None


# ── 순보유 계산 ──────────────────────────────────────────────────────────────


def test_manual_sell_all_reduces_program_net(db):
    # 2026-10-27 원장 착시 재현: 자동 BUY 70 → 수동 일괄매도 70 → 프로그램 보유 0.
    add(db, "003490", "BUY", 70, odno="B1")
    add(db, "003490", "SELL", 70, reason="manual_sell_all", odno="S1", at=_T0 + timedelta(hours=1))
    assert acs.program_holdings(db, None) == {}


def test_manual_sell_never_makes_program_net_negative_or_creates_credit(db):
    # 수동 보유 매도(프로그램 보유 0 상태)는 0 에서 멈추고, 이후 프로그램 BUY 는 그대로 보유.
    add(db, "005930", "SELL", 5, reason="manual_sell", odno="S0")
    add(db, "005930", "BUY", 3, odno="B1", at=_T0 + timedelta(hours=1))
    assert acs.program_holdings(db, None) == {"005930": 3}


def test_manual_buy_is_not_program_owned(db):
    add(db, "005930", "BUY", 10, reason="manual_buy", odno="M1")
    assert acs.program_holdings(db, None) == {}


def test_partial_fill_and_unfilled_sell_not_deducted(db):
    add(db, "005930", "BUY", 10, filled=4, odno="B1", status="PARTIALLY_FILLED")
    add(db, "005930", "SELL", 4, filled=0, odno="S1", status="RECEIVED", at=_T0 + timedelta(minutes=5))
    assert acs.program_holdings(db, None) == {"005930": 4}     # 접수만으로 차감 X


def test_canceled_after_partial_counts_only_filled(db):
    add(db, "005930", "BUY", 10, filled=3, odno="B1", status="CANCELED")
    assert acs.program_holdings(db, None) == {"005930": 3}


def test_rejected_or_unapproved_rows_ignored(db):
    add(db, "005930", "BUY", 10, filled=0, status="REJECTED")
    add(db, "005930", "BUY", 10, decision="REJECTED", executed=False)
    assert acs.program_holdings(db, None) == {}


def test_duplicate_fill_rows_counted_once(db):
    add(db, "005930", "BUY", 5, odno="DUP")
    add(db, "005930", "BUY", 5, odno="DUP", at=_T0 + timedelta(seconds=1))   # 재조회/중복 행
    assert acs.program_holdings(db, None) == {"005930": 5}


def test_order_number_reused_on_another_day_is_a_different_order(db):
    # KIS ODNO 는 영업일마다 재사용 — 다른 날 같은 번호의 SELL 을 '중복'으로 버리면 안 된다
    #   (실DB 사본 검증에서 30종목 오탐으로 발견된 회귀).
    add(db, "069500", "BUY", 10, odno="0000001234", at=datetime(2026, 7, 1, 1, 0))
    add(db, "069500", "SELL", 10, odno="0000001234", at=datetime(2026, 7, 2, 1, 0))
    assert acs.program_holdings(db, None) == {}


def test_ccld_correction_uses_kis_filled_quantity():
    rows = [SimpleNamespace(id=1, symbol="005930", side="BUY", trade_reason="kis_paper_auto",
                            filled_quantity=0, broker_order_id="X1", created_at=_T0)]
    assert acs.program_net_quantities(rows, {"X1": 7}) == {"005930": 7}


# ── 계좌 분리 ────────────────────────────────────────────────────────────────


def test_accounts_fully_separated(db, scoped):
    add(db, "028300", "BUY", 40, odno="L1")                      # LEGACY (account NULL)
    add(db, "005930", "BUY", 3, odno="N1", account=NEW)          # 새 계좌
    add(db, "000660", "BUY", 9, odno="O1", account=OTHER)        # 다른 계좌
    add(db, "005930", "SELL", 3, odno="O2", account=OTHER, reason="manual_sell")  # 다른 계좌의 매도
    assert acs.program_holdings(db, NEW) == {"005930": 3}        # 다른 계좌 매도 미반영
    assert acs.program_holdings(db, OTHER) == {"000660": 9}
    legacy = acs.legacy_unverified_holdings(db)
    assert legacy == [{"symbol": "028300", "quantity": 40, "account": "LEGACY_ACCOUNT", "status": "UNVERIFIED"}]


def test_legacy_rows_are_preserved_unmodified(db, session_factory, scoped):
    add(db, "161890", "BUY", 14, odno="L1", status="RECEIVED", filled=14)
    before = [(r.id, r.symbol, r.filled_quantity, r.broker_status, r.account_id)
              for r in db.query(OrderAuditLog).all()]
    broker = FakeKisPaperBroker({})
    step(session_factory, broker, kst(15, 16))
    step(session_factory, broker, kst(15, 30, 1))
    after = [(r.id, r.symbol, r.filled_quantity, r.broker_status, r.account_id)
             for r in session_factory().query(OrderAuditLog).filter(OrderAuditLog.id == before[0][0]).all()]
    assert after == before and broker.place_calls == []


# ── route_order 기록 / 차단 ──────────────────────────────────────────────────


def _route(db, broker, side, qty, *, reason="kis_paper_auto", now=None, rm=None):
    return asyncio.run(route_order(
        order=OrderRequest(symbol="005930", side=side, quantity=qty, trade_reason=reason),
        requested_by_ai=False, mode=OperationMode.PAPER, broker=broker,
        risk=rm or RiskManager(RiskPolicy()), db=db, now=now))


def test_route_order_stamps_account_scope_on_every_row(db):
    broker = FakeKisPaperBroker({})
    ok = _route(db, broker, OrderSide.BUY, 1, now=kst(10, 0))
    cut = _route(db, broker, OrderSide.BUY, 1, now=kst(14, 30))      # 14:30 컷오프 거부 행
    assert ok.audit.account_id == NEW and cut.audit.account_id == NEW
    assert cut.decision == RiskDecision.REJECTED and len(broker.place_calls) == 1
    assert all(r.account_id == NEW for r in db.query(OrderAuditLog).all())


def test_route_order_blocks_buy_when_kis_account_unknown(db):
    broker = FakeKisPaperBroker({})
    broker.account_no = ""                                          # 계좌 불명확
    res = _route(db, broker, OrderSide.BUY, 1, now=kst(10, 0))
    assert res.decision == RiskDecision.REJECTED and broker.place_calls == []
    assert any(acs.ACCOUNT_SCOPE_UNKNOWN in r for r in res.reasons)


def test_kill_switch_L1_does_not_treat_legacy_holding_as_owned(db, scoped):
    add(db, "005930", "BUY", 5, odno="L1")                           # LEGACY
    msg = emergency_sell_ownership_block(db, rm_at(KillSwitchLevel.LEVEL_1), side=OrderSide.SELL,
                                         symbol="005930", quantity=1, trade_reason="kis_paper_auto")
    assert msg is not None and "owned=0" in msg
    add(db, "005930", "BUY", 2, odno="N1", account=NEW)
    assert emergency_sell_ownership_block(db, rm_at(KillSwitchLevel.LEVEL_1), side=OrderSide.SELL,
                                          symbol="005930", quantity=2, trade_reason="kis_paper_auto") is None


# ── EOD (15:00~15:30) ────────────────────────────────────────────────────────


def test_eod_new_account_ignores_legacy_holding_on_same_symbol(session_factory, db, scoped):
    # 구계좌 원장 028300 40 + 새 계좌 KIS 에 028300 40 (수동/무관 보유) → 매도 0.
    add(db, "028300", "BUY", 40, odno="L1")
    broker = FakeKisPaperBroker({"028300": 40})
    out = step(session_factory, broker, kst(15, 16))
    assert broker.place_calls == []
    assert {u["symbol"]: u["quantity"] for u in out["summary"]["unmanaged_holdings"]} == {"028300": 40}


def test_eod_sells_only_current_account_program_quantity(session_factory, db, scoped):
    add(db, "005930", "BUY", 5, odno="L1")                           # LEGACY 5
    add(db, "005930", "BUY", 3, odno="N1", account=NEW)              # 새 계좌 프로그램 3
    broker = FakeKisPaperBroker({"005930": 8})
    step(session_factory, broker, kst(15, 16))
    assert [(o.symbol, o.quantity) for o in broker.place_calls] == [("005930", 3)]
    final = step(session_factory, broker, kst(15, 30, 1))
    assert final["state"] == EodState.FLAT_CONFIRMED.value and broker.holdings == {"005930": 5}


def test_eod_other_account_open_buy_excluded(session_factory, db, scoped):
    # 다른 계좌/LEGACY 의 '오늘 접수·미체결' BUY 는 새 계좌 미체결로 보지 않는다.
    add(db, "000660", "BUY", 5, filled=0, odno="ZZ1", status="RECEIVED", at=kst(10, 0).replace(tzinfo=None),
        account=OTHER)
    broker = FakeKisPaperBroker({})
    out = step(session_factory, broker, kst(15, 30, 1))
    assert out["state"] == EodState.FLAT_CONFIRMED.value
    assert not (out["summary"].get("program_open_buys") or [])


def test_eod_balance_failure_never_flat(session_factory, db, scoped):
    add(db, "005930", "BUY", 3, odno="N1", account=NEW)
    broker = FakeKisPaperBroker({"005930": 3})
    broker.fail_positions = True
    out = step(session_factory, broker, kst(15, 30, 1))
    assert out["state"] != EodState.FLAT_CONFIRMED.value and broker.place_calls == []


def test_eod_status_reports_legacy_holdings_separately(db, scoped):
    from app.kis_paper.eod_liquidation import shutdown_check
    from tests.test_kis_paper_intraday_flat_by_close import make_settings
    add(db, "483650", "BUY", 8, odno="L1")
    chk = shutdown_check(db, settings=make_settings(), now=kst(16, 0))
    assert chk["program_holdings"] == []
    assert [h["symbol"] for h in chk["legacy_unverified_holdings"]] == ["483650"]
    assert chk["account_scope"] == NEW


# ── 손익 / 보고서 ────────────────────────────────────────────────────────────


def test_daily_pnl_and_order_count_exclude_other_accounts(db, scoped):
    from app.risk.daily_pnl import compute_today_realized_pnl, count_orders_today_kst
    day = kst(10, 0)
    for acct, (b, s) in ((None, (10_000, 5_000)), (NEW, (10_000, 11_000))):
        r1 = add(db, "005930", "BUY", 1, odno=f"B{acct}", account=acct, at=day.replace(tzinfo=None))
        r2 = add(db, "005930", "SELL", 1, odno=f"S{acct}", account=acct,
                 at=(day + timedelta(minutes=5)).replace(tzinfo=None))
        r1.avg_fill_price, r2.avg_fill_price = b, s
    db.commit()
    assert compute_today_realized_pnl(db, today=day.astimezone(timezone(timedelta(hours=9))).date()) == 1_000
    assert count_orders_today_kst(db, today=day.astimezone(timezone(timedelta(hours=9))).date()) == 2


def test_daily_report_is_account_scoped(db, scoped):
    from app.reports.daily_operation_report import build_daily_operation_report, render_markdown
    day = kst(10, 0)
    add(db, "028300", "BUY", 40, odno="L1", at=day.replace(tzinfo=None))       # LEGACY 같은 날
    add(db, "005930", "BUY", 2, odno="N1", account=NEW, at=day.replace(tzinfo=None))
    rep = asyncio.run(build_daily_operation_report(db, day.astimezone(timezone(timedelta(hours=9))).date(),
                                                   now=kst(16, 0)))
    assert rep["orders"]["BUY"]["filled_qty"] == 2
    assert rep["account_scope"] == NEW
    assert [h["symbol"] for h in rep["legacy_unverified_holdings"]] == ["028300"]
    md = render_markdown(rep)
    assert "LEGACY_ACCOUNT" in md and NEW in md and "1234567801" not in md


# ── 마이그레이션 ─────────────────────────────────────────────────────────────


def _row_digest(con) -> str:
    cols = [c[1] for c in con.execute("pragma table_info(order_audit_log)") if c[1] != "account_id"]
    rows = con.execute(f"select {','.join(cols)} from order_audit_log order by id").fetchall()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def test_migration_0030_adds_nullable_column_without_touching_rows(tmp_path):
    from alembic import command
    from alembic.config import Config
    dbp = tmp_path / "legacy.db"
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{dbp.as_posix()}")
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{dbp.as_posix()}"
    try:
        command.upgrade(cfg, "0029")
        con = sqlite3.connect(dbp)
        for i in range(25):
            con.execute("insert into order_audit_log (created_at, mode, requested_by_ai, symbol, side, quantity,"
                        " order_type, latest_price, decision, reasons, archived, executed, filled_quantity, message,"
                        " trade_reason, broker_order_id) values (?, 'PAPER', 0, ?, ?, 1, 'MARKET', 1, 'APPROVED',"
                        " '[]', 0, 1, 1, 'kept', 'kis_paper_auto', ?)",
                        (f"2026-08-{(i % 20) + 1:02d}", f"{i:06d}", "BUY" if i % 2 else "SELL", f"O{i}"))
        con.commit()
        before, n = _row_digest(con), con.execute("select count(*) from order_audit_log").fetchone()[0]
        con.close()
        command.upgrade(cfg, "head")
        con = sqlite3.connect(dbp)
        assert "account_id" in [c[1] for c in con.execute("pragma table_info(order_audit_log)")]
        assert con.execute("select count(*) from order_audit_log").fetchone()[0] == n
        assert con.execute("select count(*) from order_audit_log where account_id is not null").fetchone()[0] == 0
        assert _row_digest(con) == before
        assert "ix_order_audit_log_account_id" in [r[1] for r in con.execute("pragma index_list(order_audit_log)")]
        con.close()
        command.downgrade(cfg, "0029")                                   # 원복 가능
        con = sqlite3.connect(dbp)
        assert "account_id" not in [c[1] for c in con.execute("pragma table_info(order_audit_log)")]
        assert _row_digest(con) == before
        con.close()
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def test_every_audit_construction_in_router_carries_account_id():
    src = (ROOT / "backend" / "app" / "execution" / "order_router.py").read_text(encoding="utf-8")
    blocks = src.split("OrderAuditLog(")[1:]
    assert len(blocks) == 5
    for b in blocks:
        assert "account_id=" in b.split("db.add(", 1)[0]


def test_account_scoped_idempotency_unique_index_blocks_concurrent_duplicate(session_factory):
    from sqlalchemy.exc import IntegrityError
    from app.db.models import OrderAuditLog

    first, second = session_factory(), session_factory()
    try:
        first.add(OrderAuditLog(
            mode="PAPER", symbol="005930", side="BUY", quantity=1, order_type="MARKET",
            latest_price=10_000, decision="APPROVED", reasons=[], account_id=NEW,
            client_order_id="same-key", trade_reason="kis_paper_auto",
        ))
        first.commit()
        second.add(OrderAuditLog(
            mode="PAPER", symbol="005930", side="BUY", quantity=1, order_type="MARKET",
            latest_price=10_000, decision="APPROVED", reasons=[], account_id=NEW,
            client_order_id="same-key", trade_reason="kis_paper_auto",
        ))
        with pytest.raises(IntegrityError):
            second.flush()
        second.rollback()
        second.add(OrderAuditLog(
            mode="PAPER", symbol="005930", side="BUY", quantity=1, order_type="MARKET",
            latest_price=10_000, decision="APPROVED", reasons=[], account_id=OTHER,
            client_order_id="same-key", trade_reason="kis_paper_auto",
        ))
        second.commit()
    finally:
        first.close()
        second.close()


def test_cancel_for_other_account_order_is_refused(db, scoped):
    from app.core.modes import OperationMode
    from app.brokers.base import OrderSide
    from app.execution.order_router import route_cancel
    from app.risk.risk_manager import RiskManager, RiskPolicy

    add(db, "005930", "BUY", 1, odno="A-ORDER", account=NEW)
    broker = FakeKisPaperBroker({})
    broker.account_no = "5555555501"
    result = asyncio.run(route_cancel(
        original_order_id="A-ORDER", org_no=None, symbol="005930", side=OrderSide.BUY,
        reason="test scope", mode=OperationMode.PAPER, broker=broker,
        risk=RiskManager(RiskPolicy()), db=db, client_order_id="cancel-B",
    ))
    assert result.decision == RiskDecision.REJECTED
    assert broker.cancel_calls == []


# ── 로그 마스킹 (P1) ─────────────────────────────────────────────────────────


def test_httpx_request_log_masks_account_number():
    import io
    import logging

    import app.brokers.kis_client  # noqa: F401 — 필터 설치
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    lg = logging.getLogger("httpx")
    prev = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        lg.info('HTTP Request: %s %s "%s"', "GET",
                "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/"
                "inquire-balance?CANO=12345678&ACNT_PRDT_CD=01&AFHR_FLPR_YN=N", "HTTP/1.1 200 OK")
    finally:
        lg.removeHandler(h)
        lg.setLevel(prev)
    out = buf.getvalue()
    assert "12345678" not in out and "CANO=****" in out and "ACNT_PRDT_CD=01" in out


def test_account_split_error_does_not_echo_account():
    b = FakeKisPaperBroker({})
    b.account_no = "123456"
    with pytest.raises(RuntimeError) as ei:
        b._split_account()
    assert "123456" not in str(ei.value)
