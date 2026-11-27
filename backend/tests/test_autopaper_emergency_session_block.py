"""긴급정지·재시작 시 승인 세션의 fail-closed 정책 (PR #199 보완).

- 긴급정지는 세션을 EXIT_ONLY(신규 BUY 차단, 청산 SELL·EOD 허용)로 승인 파일과 운영 DB
  둘 다에 저장한다. 파일/DB 충돌 시 더 제한적인 상태가 이긴다.
- 자동 복구는 *정상 종료* 가 남긴 일회용 허가(restore permit)가 현재 승인과 일치하고
  DB 대조가 성공할 때만. 그 외(크래시·강제 종료·긴급정지 저장 실패·DB 조회 실패)는
  복구 거부 + EXIT_ONLY + 이 프로세스 BUY 차단 → 새 명시적 승인 필요.
- 신규 BUY 경로는 DB 대조 실패 시 차단 (DB BLOCKED + 오래된 파일 SCHEDULED 대비).
- LIVE/PAPER 및 기존 주문 게이트, EOD 청산 경로 유지.

실제 KIS 호출 0건 — broker 는 호출되면 실패하는 가짜. 승인 파일은 tmp, DB 는 in-memory.
실제 프로세스 재시작 시나리오는 test_autopaper_restart_subprocess.py.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.loop import AutoPaperLoop
from app.db.base import Base
from app.db.models import PaperSessionApproval
from app.kis_paper import one_click_session as ocs

KST = timezone(timedelta(hours=9))
SAT, MON = date(2026, 11, 28), date(2026, 11, 30)


def kst(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), KST)


def settings(**over):
    base = dict(
        kis_is_paper=True, enable_live_trading=False, kis_account_no="1234567801",
        kis_product_code="01", kis_app_key="K", kis_app_secret="S",
        market_data_provider="kis", enable_ai_paper_background_tick=True,
        kis_paper_fill_polling=True, kis_paper_smoke_mode=False,
        krx_calendar_override_path="",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _NoCallBroker:
    """KIS 호출이 일어나면 테스트 실패 — BUY 차단은 broker 조회 *전* 에 끝나야 한다."""

    def __getattr__(self, name):
        raise AssertionError(f"broker.{name} must not be called")


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(db_factory):
    s = db_factory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def approval_file(tmp_path, monkeypatch):
    path = tmp_path / "approval.json"
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(path))
    return path


@pytest.fixture
def freeze(monkeypatch):
    real = ocs.datetime

    def _set(at: datetime):
        class _Frozen(real):
            @classmethod
            def now(cls, tz=None):
                return at.astimezone(tz) if tz else at.replace(tzinfo=None)
        monkeypatch.setattr(ocs, "datetime", _Frozen)
    return _set


@pytest.fixture
def default_db(monkeypatch, db_factory):
    """db 인자 없이 호출되는 경로(main.py lifespan 등)가 쓰는 '운영 DB' 대역."""
    monkeypatch.setattr(ocs, "_open_default_db", lambda: db_factory())


def approve(db, target: date, **over):
    return ocs.create_approval(db=db, settings=settings(**over),
                               risk=SimpleNamespace(emergency_stop=False),
                               target_date=target.isoformat(), actor="op",
                               explicit_ack=True)


def new_process():
    """새 프로세스 — 메모리 상태(프로세스 BUY 차단 표식) 초기화."""
    ocs._clear_process_buy_block()


def clean_shutdown(loop_state="WAITING_MARKET"):
    return ocs.write_restore_permit(settings(), loop_state=loop_state)


def startup(at: datetime, *, risk_emergency=False):
    """main.py lifespan 시작 경로와 같은 호출 (db 인자 없음)."""
    return ocs.evaluate_restart_restore(settings(), risk_emergency=risk_emergency, now=at)


def buy(db, at: datetime, s=None):
    return asyncio.run(ocs.authorize_order(
        db=db, settings=s or settings(), broker=_NoCallBroker(),
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=1, notional_krw=10_000, now=at))


def db_row(db):
    return db.execute(select(PaperSessionApproval)).scalars().one()


# ── 기본: 정상 종료 → 재시작 = 자동 복구 (기존 동작 유지) ──────────────────────
def test_explicit_next_day_approval_survives_restart_without_repeated_button(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    assert clean_shutdown()["written"] is True
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is True and d["armed_for"] == MON
    assert not ocs.restore_permit_path().exists()          # 일회용 — 소모됨
    # 같은 허가로 두 번째(=크래시 후 재시작) 복구는 불가.
    new_process()
    d2 = startup(kst(SAT, 14))
    assert d2["restore"] is True and d2["reason"] == "VALID_SCHEDULED_APPROVAL"


def test_explicit_scheduled_next_day_approval_arms_without_duplicate_button(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is True
    assert d["reason"] == "VALID_SCHEDULED_APPROVAL"
    assert d["armed_for"] == MON


def test_scheduled_next_day_does_not_arm_when_background_opt_in_off(db, freeze, default_db,
                                                                    monkeypatch):
    freeze(kst(SAT, 12))
    approve(db, MON)
    new_process()
    monkeypatch.setattr(ocs, "_open_default_db", lambda: db)
    out = ocs.evaluate_restart_restore(
        settings(enable_ai_paper_background_tick=False), risk_emergency=False,
        now=kst(SAT, 13), db=db,
    )
    assert out["restore"] is False
    assert out["reason"] == "NO_CLEAN_SHUTDOWN_PERMIT"


@pytest.mark.parametrize("state", ["PAUSED", "STOPPED", "MARKET_CLOSED", "EMERGENCY_STOP", None])
def test_permit_not_written_when_loop_not_active(db, freeze, default_db, state):
    freeze(kst(SAT, 12))
    approve(db, MON)
    assert clean_shutdown(state) == {"written": False, "reason": "LOOP_NOT_ACTIVE"}


# ── 1·2. 긴급정지 저장이 모두 실패 → 재시작 후에도 자동 복구 금지 ────────────────
def test_emergency_persist_total_failure_then_restart_never_restores(db, freeze, monkeypatch,
                                                                      default_db, approval_file):
    freeze(kst(SAT, 12))
    approve(db, MON)
    real_write, real_persist = ocs._write_atomic, ocs._persist_approval

    def _fail_write(_data):
        raise OSError("disk full")

    def _fail_db(_db, _record):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ocs, "_write_atomic", _fail_write)
    monkeypatch.setattr(ocs, "_persist_approval", _fail_db)
    with pytest.raises(ocs.EmergencyBlockPersistError):
        ocs.block_session_for_emergency(db)
    # 파일·DB 는 여전히 SCHEDULED (저장 실패). 정상 종료해도 허가는 기록되지 않는다.
    assert json.loads(approval_file.read_text(encoding="utf-8"))["status"] == "SCHEDULED"
    assert clean_shutdown() == {"written": False, "reason": "PROCESS_BUY_BLOCKED"}

    # 재시작 — 저장소가 여전히 고장이어도 복구 금지 + 프로세스 BUY 차단.
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is False and d["reason"] == "PERSISTED_EMERGENCY_BLOCK"
    assert ocs._PROCESS_EMERGENCY_BLOCK is True
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")

    # 저장소가 복구된 뒤 재시작 — 허가 없음 → EXIT_ONLY 로 저장, 계속 재승인 필요.
    monkeypatch.setattr(ocs, "_write_atomic", real_write)
    monkeypatch.setattr(ocs, "_persist_approval", real_persist)
    freeze(kst(SAT, 15))
    new_process()
    d = startup(kst(SAT, 15))
    assert d["restore"] is False and d["reason"] == "PERSISTED_EMERGENCY_BLOCK"
    assert db_row(db).status == "SCHEDULED"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_crash_without_shutdown_keeps_explicit_future_approval_armed(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)                                       # 허가 없이 프로세스 종료(크래시)
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is True and d["reason"] == "VALID_SCHEDULED_APPROVAL"
    rec = ocs.load_approval(db)
    assert rec["status"] == "SCHEDULED" and not rec.get("buy_blocked")
    assert ocs.session_trading_authorized(settings(), db=db) is True


@pytest.mark.parametrize("damage", ["corrupt", "mismatch"])
def test_bad_permit_is_rejected(db, freeze, default_db, damage):
    freeze(kst(SAT, 12))
    approve(db, MON)
    clean_shutdown()
    path = ocs.restore_permit_path()
    if damage == "corrupt":
        path.write_text("{oops", encoding="utf-8")
    else:
        permit = json.loads(path.read_text(encoding="utf-8"))
        permit["approved_at"] = "2026-01-01T00:00:00+00:00"   # 다른(이전) 승인의 허가
        path.write_text(json.dumps(permit), encoding="utf-8")
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is False
    assert d["reason"] == ("PERMIT_CORRUPT" if damage == "corrupt" else "PERMIT_MISMATCH")
    assert not path.exists()


def test_permit_not_consumable_is_rejected(db, freeze, default_db, monkeypatch):
    freeze(kst(SAT, 12))
    approve(db, MON)
    clean_shutdown()
    monkeypatch.setattr(ocs, "discard_restore_permit", lambda: False)
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is False and d["reason"] == "PERMIT_NOT_CONSUMABLE"


# ── 3. DB 에 차단이 있는데 DB 조회 실패 → 파일의 오래된 SCHEDULED 만 읽힘 ────────
def test_db_blocked_but_db_read_fails_stale_file_scheduled_blocks(db, freeze, default_db,
                                                                 approval_file, monkeypatch):
    freeze(kst(SAT, 12))
    approve(db, MON)
    clean_shutdown()                                       # 정상 종료 허가까지 있음
    row = db_row(db)                                        # DB 만 차단(파일 쓰기 실패 상황)
    rec = dict(row.record)
    rec.update(status="EXIT_ONLY", buy_blocked=True, buy_block_reason="EMERGENCY_STOP_SESSION_BLOCKED",
               emergency_blocked=True)
    row.status, row.record = "EXIT_ONLY", rec
    db.commit()
    assert json.loads(approval_file.read_text(encoding="utf-8"))["status"] == "SCHEDULED"

    def _db_down(*_a, **_k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(ocs, "_db_record", _db_down)
    # (a) 실행 중 프로세스: 파일의 오래된 SCHEDULED 만 보이지만 DB 대조 실패 → BUY 차단.
    new_process()
    assert ocs.load_approval(db)["status"] == "SCHEDULED"      # 파일만 보임 (오래된 값)
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10)) == (False, "APPROVAL_DB_UNVERIFIED")
    # (b) 재시작: 정상 종료 허가가 있어도 DB 대조 실패 → 복구 금지 + 재승인 필요.
    freeze(kst(SAT, 13))
    new_process()
    d = startup(kst(SAT, 13))
    assert d["restore"] is False and d["reason"] == "APPROVAL_DB_UNVERIFIED"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10))[0] is False


def test_db_more_restrictive_than_file_fails_verification(db, freeze):
    freeze(kst(SAT, 12))
    approve(db, MON)
    file_rec = ocs._read_file_record()
    row = db_row(db)
    rec = dict(row.record)
    rec.update(status="EXIT_ONLY", buy_blocked=True)
    row.status, row.record = "EXIT_ONLY", rec
    db.commit()
    assert ocs._db_verified(db, file_rec) is False


# ── 긴급정지 영속화 (cd44279 의 시나리오, EXIT_ONLY 모델) ───────────────────────
def test_emergency_persists_exit_only_to_file_and_db(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    result = ocs.block_session_for_emergency(db)
    assert result["file"] and result["db"] and result["status"] == "EXIT_ONLY"
    row = db_row(db)
    assert row.status == "EXIT_ONLY" and row.record["emergency_blocked"] is True
    new_process()
    assert startup(kst(SAT, 13))["restore"] is False
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert ocs.session_exit_authorized(settings(), db=db) is True   # 청산·EOD 는 계속
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_http_emergency_stop_persists_to_db(client, freeze):
    from app.auto_paper.loop import get_auto_paper_loop
    freeze(kst(SAT, 12))
    s = client.test_db_factory()
    approve(s, MON)
    try:
        body = client.post("/api/auto-paper/emergency-stop").json()
        assert body["state"] == "EMERGENCY_STOP"
        assert body["session_block_persisted"] is True
        assert body["session_block"]["file"] is True and body["session_block"]["db"] is True
        s.expire_all()
        assert db_row(s).status == "EXIT_ONLY"
    finally:
        get_auto_paper_loop().reset()
        s.close()


@pytest.mark.parametrize("damage", ["delete", "corrupt", "not_a_dict"])
def test_file_lost_after_emergency_db_restores_block(db, freeze, approval_file, damage):
    freeze(kst(SAT, 12))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    if damage == "delete":
        approval_file.unlink()
    elif damage == "corrupt":
        approval_file.write_text("{not json", encoding="utf-8")
    else:
        approval_file.write_text("[1, 2, 3]", encoding="utf-8")
    new_process()
    rec = ocs.load_approval(db)
    assert rec["status"] == "EXIT_ONLY" and rec["emergency_blocked"] is True
    assert json.loads(approval_file.read_text(encoding="utf-8"))["status"] == "EXIT_ONLY"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_file_blocked_db_scheduled_blocks(db, freeze, approval_file):
    freeze(kst(SAT, 12))
    approve(db, MON)
    rec = json.loads(approval_file.read_text(encoding="utf-8"))
    rec.update(status="EXIT_ONLY", buy_blocked=True, emergency_blocked=True,
               buy_block_reason="EMERGENCY_STOP_SESSION_BLOCKED")
    approval_file.write_text(json.dumps(rec), encoding="utf-8")
    assert db_row(db).status == "SCHEDULED"
    new_process()
    assert ocs.load_approval(db)["status"] == "EXIT_ONLY"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert buy(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_file_generic_blocked_db_scheduled_not_authorized(db, freeze, approval_file):
    freeze(kst(SAT, 12))
    approve(db, MON)
    rec = json.loads(approval_file.read_text(encoding="utf-8"))
    rec.update(status="BLOCKED", block_reason="SOMETHING")
    approval_file.write_text(json.dumps(rec), encoding="utf-8")
    assert ocs.load_approval(db)["status"] == "BLOCKED"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert ocs.armed_session_date(settings(), now=kst(SAT, 13), db=db) is None


def test_partial_persist_file_only_or_db_only_still_blocks(db, freeze, monkeypatch, approval_file):
    freeze(kst(SAT, 12))
    approve(db, MON)

    def _fail_write(_data):
        raise OSError("disk full")
    real_write = ocs._write_atomic
    monkeypatch.setattr(ocs, "_write_atomic", _fail_write)
    r = ocs.block_session_for_emergency(db)
    assert r["file"] is False and r["db"] is True and r["errors"] == ["file:OSError"]
    monkeypatch.setattr(ocs, "_write_atomic", real_write)
    new_process()
    assert ocs.load_approval(db)["status"] == "EXIT_ONLY"       # DB 가 이기고 파일 재동기화
    assert json.loads(approval_file.read_text(encoding="utf-8"))["status"] == "EXIT_ONLY"


def test_http_emergency_reports_persist_failure(client, freeze, monkeypatch):
    from app.auto_paper.loop import get_auto_paper_loop
    freeze(kst(SAT, 12))
    s = client.test_db_factory()
    approve(s, MON)

    def _fail_write(_data):
        raise OSError("disk full")

    def _fail_db(_db, _record):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ocs, "_write_atomic", _fail_write)
    monkeypatch.setattr(ocs, "_persist_approval", _fail_db)
    try:
        r = client.post("/api/auto-paper/emergency-stop")
        assert r.status_code == 200
        body = r.json()
        assert body["session_block_persisted"] is False
        assert body["session_block"]["errors"] == ["file:OSError", "db:RuntimeError"]
        assert ocs._PROCESS_EMERGENCY_BLOCK is True
    finally:
        get_auto_paper_loop().reset()
        s.close()


# ── 4. 새 명시적 승인 없이 예약 복구 금지 ─────────────────────────────────────
def test_no_restore_without_new_explicit_approval(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    new_process()
    with pytest.raises(ValueError, match="EXPLICIT_APPROVAL_REQUIRED"):
        ocs.create_approval(db=db, settings=settings(), risk=SimpleNamespace(emergency_stop=False),
                            target_date=MON.isoformat(), actor="op", explicit_ack=False)
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert startup(kst(SAT, 13))["restore"] is False
    # 새 명시적 승인 → 차단 표식 없는 새 기록, 그 프로세스에서 다시 시작 가능.
    fresh = approve(db, MON)
    assert fresh["status"] == "SCHEDULED" and not ocs._buy_blocked(fresh)
    assert db_row(db).status == "SCHEDULED"
    assert ocs.session_trading_authorized(settings(), db=db) is True
    loop = AutoPaperLoop()
    s = loop.start(now=kst(SAT, 13), armed_for=ocs.armed_session_date(settings(), now=kst(SAT, 13), db=db))
    assert s.state == "WAITING_MARKET"


def test_new_explicit_approval_clears_in_process_block(db, freeze, monkeypatch):
    freeze(kst(SAT, 12))
    approve(db, MON)
    monkeypatch.setattr(ocs, "_write_atomic", lambda _d: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(ocs, "_persist_approval",
                        lambda _db, _r: (_ for _ in ()).throw(RuntimeError("y")))
    with pytest.raises(ocs.EmergencyBlockPersistError):
        ocs.block_session_for_emergency(db)
    monkeypatch.undo()
    freeze(kst(SAT, 12))
    assert ocs.session_trading_authorized(settings(), db=db) is False
    approve(db, MON)
    assert ocs._PROCESS_EMERGENCY_BLOCK is False
    assert ocs.session_trading_authorized(settings(), db=db) is True


# ── 5. LIVE=false·기존 게이트·EOD 청산 경로 유지 ─────────────────────────────
def test_live_flag_and_paper_flag_still_enforced(db, freeze):
    freeze(kst(SAT, 12))
    with pytest.raises(ValueError, match="LIVE_TRADING_MUST_BE_OFF"):
        approve(db, MON, enable_live_trading=True)
    with pytest.raises(ValueError, match="PAPER_ACCOUNT_REQUIRED"):
        approve(db, MON, kis_is_paper=False)
    approve(db, MON)
    for over in ({"enable_live_trading": True}, {"kis_is_paper": False}):
        assert ocs.session_trading_authorized(settings(**over), db=db) is False
        assert ocs.session_exit_authorized(settings(**over), db=db) is False


def test_existing_order_gates_unchanged_after_reapproval(db, freeze):
    freeze(kst(MON, 8, 0))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    new_process()
    approve(db, MON)
    assert buy(db, kst(MON, 9, 0)) == (False, "WAITING_FOR_0905_KST")
    approve(db, MON)
    assert buy(db, kst(MON, 10), s=settings(enable_live_trading=True)) == (
        False, "PAPER_LIVE_MODE_MISMATCH")


class _SellBroker:
    """SELL 사전검증용 가짜 KIS — 읽기만, 주문 API 없음."""

    def __init__(self, qty):
        self.qty = qty

    async def get_positions(self):
        return [SimpleNamespace(symbol="005930", quantity=self.qty, sellable_quantity=self.qty)]

    async def list_today_orders(self, *, kst_date):
        return []


def test_exit_only_still_allows_verified_sell_and_keeps_status(db, freeze):
    freeze(kst(MON, 8, 0))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    new_process()
    freeze(kst(MON, 15, 16))
    ok, reason = asyncio.run(ocs.authorize_order(
        db=db, settings=settings(), broker=_SellBroker(2),
        risk=SimpleNamespace(emergency_stop=False), side="SELL", symbol="005930",
        quantity=2, notional_krw=100_000, now=kst(MON, 15, 16)))
    assert (ok, reason) == (True, "ONE_CLICK_PAPER_AUTHORIZED")
    rec = ocs.load_approval(db)
    assert rec["status"] == "EXIT_ONLY" and ocs._buy_blocked(rec)   # ACTIVE 로 풀리지 않음
    # 기존 SELL 검사 그대로: 보유 초과 매도는 거부, 그래도 EXIT_ONLY 유지(청산 경로 보존).
    ok, reason = asyncio.run(ocs.authorize_order(
        db=db, settings=settings(), broker=_SellBroker(1),
        risk=SimpleNamespace(emergency_stop=False), side="SELL", symbol="005930",
        quantity=2, notional_krw=100_000, now=kst(MON, 15, 16)))
    assert (ok, reason) == (False, "KIS_SELL_QUANTITY_UNVERIFIED")
    assert ocs.load_approval(db)["status"] == "EXIT_ONLY"
    assert ocs.session_exit_authorized(settings(), db=db) is True


def test_effective_exit_settings_opens_paper_path_for_eod_only(db, freeze):
    freeze(kst(MON, 8, 0))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    base = settings(enable_kis_paper_auto_trading=False, kis_paper_auto_order_dry_run=True)
    assert ocs.effective_session_settings(base, db=db) is base          # BUY 경로 닫힘
    eff = ocs.effective_exit_settings(base, db=db)                      # EOD 청산 경로 열림
    assert eff.enable_kis_paper_auto_trading is True and eff.enable_live_trading is False


def test_emergency_without_any_approval_is_noop_but_fail_closed(db):
    result = ocs.block_session_for_emergency(db)
    assert result["blocked"] is False and result["reason"] == "NO_APPROVAL"
    assert ocs._PROCESS_EMERGENCY_BLOCK is True


def test_startup_without_session_is_noop(db, default_db):
    d = startup(kst(SAT, 13))
    assert d == {"restore": False, "reason": "NO_ACTIVE_SESSION", "armed_for": None}
    assert ocs._PROCESS_EMERGENCY_BLOCK is False


def test_startup_with_risk_kill_switch_never_restores(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    clean_shutdown()
    new_process()
    d = startup(kst(SAT, 13), risk_emergency=True)
    assert d["restore"] is False and d["reason"] == "RISK_EMERGENCY_STOP"
