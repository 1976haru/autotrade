"""긴급정지 → 승인 세션 차단의 영속성 (PR #199 보완).

- 긴급정지는 승인 *파일* 과 운영 *DB* 기록 둘 다를 BLOCKED + emergency_blocked 로 저장.
- 파일/DB 가 충돌하면 더 제한적인 상태가 이긴다.
- 저장 실패는 숨기지 않는다: 한쪽만 성공해도 재시작 후 차단, 둘 다 실패하면
  예외 + 프로세스 내 fail-closed.
- 차단 해제는 새 *명시적* 승인으로만 가능. LIVE/PAPER 및 기존 주문 게이트 유지.

실제 KIS 호출 0건 — broker 는 호출되면 실패하는 가짜. 승인 파일은 tmp 경로, DB 는 in-memory.
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
    """KIS 호출이 일어나면 테스트 실패 — 차단은 broker 조회 *전* 에 끝나야 한다."""

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
    """db 인자 없이 호출되는 경로(main.py lifespan 복원 등)가 쓰는 '운영 DB' 대역."""
    monkeypatch.setattr(ocs, "_open_default_db", lambda: db_factory())


def approve(db, target: date, **over):
    return ocs.create_approval(db=db, settings=settings(**over),
                               risk=SimpleNamespace(emergency_stop=False),
                               target_date=target.isoformat(), actor="op",
                               explicit_ack=True)


def restart():
    """새 프로세스 — 메모리 상태(프로세스 차단 표식) 초기화."""
    ocs._PROCESS_EMERGENCY_BLOCK = False


def authorize(db, at: datetime, s=None):
    return asyncio.run(ocs.authorize_order(
        db=db, settings=s or settings(), broker=_NoCallBroker(),
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=1, notional_krw=10_000, now=at))


def db_row(db):
    return db.execute(select(PaperSessionApproval)).scalars().one()


def lifespan_restore(at: datetime):
    """main.py lifespan 복원 경로와 같은 호출 (db 인자 없음)."""
    cfg = settings()
    if not ocs.session_trading_authorized(cfg):
        return None
    return AutoPaperLoop().start(now=at, armed_for=ocs.armed_session_date(cfg, now=at))


# ── 1. 예약 승인 → 긴급정지 → 재시작 → 주문 차단 ─────────────────────────────
def test_scheduled_then_emergency_then_restart_blocks_orders(db, freeze, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    result = ocs.block_session_for_emergency(db)
    assert result == {"blocked": True, "reason": "AUTO_PAPER_EMERGENCY_STOP",
                      "file": True, "db": True, "errors": []}
    row = db_row(db)
    assert row.status == "BLOCKED" and row.record["emergency_blocked"] is True

    restart()
    assert lifespan_restore(kst(SAT, 13)) is None          # 대기 상태로 복원 안 됨
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))                                   # 승인 대상일 장중이어도
    assert authorize(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_http_emergency_stop_persists_to_db(client, freeze):
    from app.auto_paper.loop import get_auto_paper_loop
    freeze(kst(SAT, 12))
    s = client.test_db_factory()
    approve(s, MON)
    try:
        r = client.post("/api/auto-paper/emergency-stop")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "EMERGENCY_STOP"
        assert body["session_block_persisted"] is True
        assert body["session_block"]["file"] is True and body["session_block"]["db"] is True
        s.expire_all()
        assert db_row(s).status == "BLOCKED"
    finally:
        get_auto_paper_loop().reset()
        s.close()


# ── 2. 긴급정지 후 승인 파일 삭제·손상 → DB 복구해도 차단 ─────────────────────
@pytest.mark.parametrize("damage", ["delete", "corrupt", "not_a_dict"])
def test_file_lost_after_emergency_db_restores_blocked(db, freeze, approval_file, damage):
    freeze(kst(SAT, 12))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    if damage == "delete":
        approval_file.unlink()
    elif damage == "corrupt":
        approval_file.write_text("{not json", encoding="utf-8")
    else:
        approval_file.write_text("[1, 2, 3]", encoding="utf-8")

    restart()
    rec = ocs.load_approval(db)
    assert rec["status"] == "BLOCKED" and rec["emergency_blocked"] is True
    assert json.loads(approval_file.read_text(encoding="utf-8"))["status"] == "BLOCKED"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert authorize(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


# ── 3. 파일/DB 불일치 → 더 제한적인 상태 우선 ─────────────────────────────────
def test_file_blocked_db_scheduled_blocks(db, freeze, approval_file, default_db):
    freeze(kst(SAT, 12))
    approve(db, MON)
    rec = json.loads(approval_file.read_text(encoding="utf-8"))
    rec.update(status="BLOCKED", block_reason="AUTO_PAPER_EMERGENCY_STOP", emergency_blocked=True)
    approval_file.write_text(json.dumps(rec), encoding="utf-8")
    assert db_row(db).status == "SCHEDULED"                # DB 는 아직 SCHEDULED

    restart()
    assert ocs.load_approval(db)["status"] == "BLOCKED"
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert lifespan_restore(kst(SAT, 13)) is None
    freeze(kst(MON, 10))
    assert authorize(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_file_generic_blocked_db_scheduled_not_authorized(db, freeze, approval_file):
    freeze(kst(SAT, 12))
    approve(db, MON)
    rec = json.loads(approval_file.read_text(encoding="utf-8"))
    rec.update(status="BLOCKED", block_reason="SOMETHING")
    approval_file.write_text(json.dumps(rec), encoding="utf-8")
    assert ocs.load_approval(db)["status"] == "BLOCKED"     # 파일(제한적) 우선
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert ocs.armed_session_date(settings(), now=kst(SAT, 13), db=db) is None


def test_file_scheduled_db_blocked_blocks_and_resyncs_file(db, freeze, approval_file,
                                                           monkeypatch, default_db):
    """파일 쓰기 실패로 파일은 SCHEDULED, DB 만 BLOCKED 인 경우."""
    freeze(kst(SAT, 12))
    approve(db, MON)
    real_write = ocs._write_atomic

    def _fail(_data):
        raise OSError("disk full")
    monkeypatch.setattr(ocs, "_write_atomic", _fail)
    result = ocs.block_session_for_emergency(db)
    assert result["file"] is False and result["db"] is True
    assert result["errors"] == ["file:OSError"]
    monkeypatch.setattr(ocs, "_write_atomic", real_write)
    assert json.loads(approval_file.read_text(encoding="utf-8"))["status"] == "SCHEDULED"

    restart()
    assert lifespan_restore(kst(SAT, 13)) is None           # db 인자 없는 경로도 DB 를 봄
    assert ocs.load_approval(db)["status"] == "BLOCKED"
    assert json.loads(approval_file.read_text(encoding="utf-8"))["emergency_blocked"] is True
    freeze(kst(MON, 10))
    assert authorize(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_db_persist_failure_file_still_blocks(db, freeze, monkeypatch):
    freeze(kst(SAT, 12))
    approve(db, MON)

    def _fail(_db, _record):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ocs, "_persist_approval", _fail)
    result = ocs.block_session_for_emergency(db)
    assert result["file"] is True and result["db"] is False
    assert result["errors"] == ["db:RuntimeError"]
    restart()
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert authorize(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


def test_both_persist_failures_raise_and_fail_closed_in_process(db, freeze, monkeypatch):
    freeze(kst(SAT, 12))
    approve(db, MON)

    def _fail_write(_data):
        raise OSError("disk full")

    def _fail_db(_db, _record):
        raise RuntimeError("db locked")
    monkeypatch.setattr(ocs, "_write_atomic", _fail_write)
    monkeypatch.setattr(ocs, "_persist_approval", _fail_db)
    with pytest.raises(ocs.EmergencyBlockPersistError, match="file:OSError,db:RuntimeError"):
        ocs.block_session_for_emergency(db)
    # 같은 프로세스: 저장은 실패했어도 즉시 차단.
    assert ocs._PROCESS_EMERGENCY_BLOCK is True
    assert ocs.session_trading_authorized(settings(), db=db) is False
    freeze(kst(MON, 10))
    assert authorize(db, kst(MON, 10)) == (False, "EMERGENCY_STOP_SESSION_BLOCKED")


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
        assert r.status_code == 200                         # 루프 정지는 성공
        body = r.json()
        assert body["state"] == "EMERGENCY_STOP"
        assert body["session_block_persisted"] is False     # 숨기지 않고 보고
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
    restart()
    # 명시 승인 없는 재요청은 거부되고 차단 유지.
    with pytest.raises(ValueError, match="EXPLICIT_APPROVAL_REQUIRED"):
        ocs.create_approval(db=db, settings=settings(), risk=SimpleNamespace(emergency_stop=False),
                            target_date=MON.isoformat(), actor="op", explicit_ack=False)
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert lifespan_restore(kst(SAT, 13)) is None

    # 새 명시적 승인 → 긴급정지 표식 없는 *새* 기록 (이전 기록 재사용 아님).
    fresh = approve(db, MON)
    assert fresh["status"] == "SCHEDULED" and not fresh.get("emergency_blocked")
    assert db_row(db).status == "SCHEDULED"
    assert ocs.session_trading_authorized(settings(), db=db) is True
    s = lifespan_restore(kst(SAT, 13))
    assert s is not None and s.state == "WAITING_MARKET"


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


# ── 5. LIVE=false 및 기존 주문 안전 게이트 유지 ───────────────────────────────
def test_live_flag_and_paper_flag_still_enforced(db, freeze):
    freeze(kst(SAT, 12))
    with pytest.raises(ValueError, match="LIVE_TRADING_MUST_BE_OFF"):
        approve(db, MON, enable_live_trading=True)
    with pytest.raises(ValueError, match="PAPER_ACCOUNT_REQUIRED"):
        approve(db, MON, kis_is_paper=False)
    approve(db, MON)
    assert ocs.session_trading_authorized(settings(enable_live_trading=True), db=db) is False
    assert ocs.session_trading_authorized(settings(kis_is_paper=False), db=db) is False


def test_existing_order_gates_unchanged_after_reapproval(db, freeze):
    freeze(kst(MON, 8, 0))
    approve(db, MON)
    ocs.block_session_for_emergency(db)
    restart()
    approve(db, MON)                                        # 새 명시 승인
    # 기존 게이트 그대로: 09:05 전 대기, 날짜 불일치, LIVE/PAPER 불일치.
    assert authorize(db, kst(MON, 9, 0)) == (False, "WAITING_FOR_0905_KST")
    approve(db, MON)
    ok, reason = authorize(db, kst(MON, 10), s=settings(enable_live_trading=True))
    assert (ok, reason) == (False, "PAPER_LIVE_MODE_MISMATCH")


def test_emergency_without_any_approval_is_noop_but_fail_closed(db):
    result = ocs.block_session_for_emergency(db)
    assert result["blocked"] is False and result["reason"] == "NO_APPROVAL"
    assert ocs._PROCESS_EMERGENCY_BLOCK is True
