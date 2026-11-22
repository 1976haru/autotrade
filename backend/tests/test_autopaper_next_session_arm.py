"""P2 — 장 마감 후/주말/휴장일에 시작하면 *다음 거래일* 승인으로 대기(arming).

대기는 주문 권한이 아니다: `authorize_order` 는 target_date == 오늘 을 그대로 검사.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.loop import AutoPaperLoop, AutoPaperState
from app.db.base import Base
from app.kis_paper import one_click_session as ocs
from app.scheduler.intraday_session import next_trading_day

KST = timezone(timedelta(hours=9))
SAT, SUN, MON = date(2026, 11, 28), date(2026, 11, 29), date(2026, 11, 30)


def kst(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), KST)


def settings():
    return SimpleNamespace(
        kis_is_paper=True, enable_live_trading=False, kis_account_no="1234567801",
        kis_product_code="01", kis_app_key="K", kis_app_secret="S",
        market_data_provider="kis", enable_ai_paper_background_tick=True,
        kis_paper_fill_polling=True, kis_paper_smoke_mode=False,
        krx_calendar_override_path="",
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def approval_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    return tmp_path / "approval.json"


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


def approve(db, target: date):
    return ocs.create_approval(db=db, settings=settings(),
                               risk=SimpleNamespace(emergency_stop=False),
                               target_date=target.isoformat(), actor="op",
                               explicit_ack=True)


# ── next_trading_day ────────────────────────────────────────────────────────
@pytest.mark.parametrize("at,expected", [
    (kst(date(2026, 11, 23), 8, 50), date(2026, 11, 23)),    # 월 개장 전 → 당일
    (kst(date(2026, 11, 23), 15, 29), date(2026, 11, 23)),   # 마감 전 → 당일
    (kst(date(2026, 11, 23), 16), date(2026, 11, 24)),       # 마감 후 → 다음날
    (kst(SAT, 12), MON),
    (kst(date(2026, 12, 24), 16), date(2026, 12, 28)),       # 12/25 휴장 건너뜀
    (kst(date(2026, 12, 30), 16), date(2027, 1, 4)),         # 12/31·1/1 휴장
])
def test_next_trading_day(at, expected):
    assert next_trading_day(at) == expected


# ── loop arming ─────────────────────────────────────────────────────────────
def test_saturday_start_with_monday_approval_waits_then_runs():
    loop = AutoPaperLoop()
    s = loop.start(now=kst(SAT, 12), armed_for=MON)
    assert s.state == AutoPaperState.WAITING_MARKET.value and s.armed_for == MON.isoformat()
    assert loop.status(now=kst(SUN, 12)).state == "WAITING_MARKET"
    assert loop.status(now=kst(MON, 8, 59)).state == "WAITING_MARKET"
    after = loop.status(now=kst(MON, 9, 0))
    assert after.state == "RUNNING" and after.armed_for is None


def test_weekday_evening_start_waits_for_next_day():
    tue, wed = date(2026, 11, 24), date(2026, 11, 25)
    loop = AutoPaperLoop()
    assert loop.start(now=kst(tue, 16), armed_for=wed).state == "WAITING_MARKET"
    assert loop.status(now=kst(tue, 23)).state == "WAITING_MARKET"
    assert loop.status(now=kst(wed, 9, 1)).state == "RUNNING"


def test_christmas_eve_skips_holiday():
    eve, mon = date(2026, 12, 24), date(2026, 12, 28)
    assert next_trading_day(kst(eve, 16)) == mon
    loop = AutoPaperLoop()
    assert loop.start(now=kst(eve, 16), armed_for=mon).state == "WAITING_MARKET"
    assert loop.status(now=kst(date(2026, 12, 25), 10)).state == "WAITING_MARKET"
    assert loop.status(now=kst(mon, 9, 30)).state == "RUNNING"


def test_missed_armed_day_becomes_market_closed():
    loop = AutoPaperLoop()
    loop.start(now=kst(SAT, 12), armed_for=MON)
    s = loop.status(now=kst(date(2026, 12, 1), 10))
    assert s.state == "MARKET_CLOSED" and s.armed_for is None


def test_without_arming_closed_start_is_market_closed():
    assert AutoPaperLoop().start(now=kst(SAT, 12)).state == "MARKET_CLOSED"


def test_arming_ignored_when_target_not_in_future():
    loop = AutoPaperLoop()
    s = loop.start(now=kst(date(2026, 11, 23), 16), armed_for=date(2026, 11, 23))
    assert s.state == "MARKET_CLOSED" and s.armed_for is None


@pytest.mark.parametrize("action", ["emergency_stop", "reset"])
def test_emergency_or_reset_clears_arming(action):
    loop = AutoPaperLoop()
    loop.start(now=kst(SAT, 12), armed_for=MON)
    getattr(loop, action)()
    s = loop.status(now=kst(MON, 10))
    assert s.armed_for is None and s.state != "RUNNING"


# ── approval date range ─────────────────────────────────────────────────────
def test_saturday_approval_for_monday_allowed_and_arms(db, freeze):
    freeze(kst(SAT, 12))
    rec = approve(db, MON)
    assert rec["target_date"] == MON.isoformat()
    assert ocs.armed_session_date(settings(), now=kst(SAT, 12)) == MON


def test_approval_beyond_next_trading_day_rejected(db, freeze):
    freeze(kst(SAT, 12))
    with pytest.raises(ValueError, match="TARGET_DATE_NOT_TODAY_OR_NEXT_DAY"):
        approve(db, date(2026, 12, 1))    # 화 = 2거래일 이후


def test_http_two_trading_days_ahead_is_409(client, monkeypatch, freeze):
    import app.api.routes_auto_paper as routes
    monkeypatch.setenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(routes, "get_settings", settings)
    freeze(kst(SAT, 12))
    r = client.post("/api/auto-paper/paper-session-approval",
                    headers={"X-Autotrade-Desktop-Auth": "tok"},
                    json={"target_date": "2026-12-01", "explicit_ack": True, "actor": "op"})
    assert r.status_code == 409
    assert r.json()["detail"] == "TARGET_DATE_NOT_TODAY_OR_NEXT_DAY"


def test_weekday_morning_rejects_tomorrow(db, freeze):
    # 당일이 거래일·장 마감 전이면 next_trading_day == 오늘 → 내일 승인 불가.
    freeze(kst(date(2026, 11, 23), 8, 50))
    with pytest.raises(ValueError, match="TARGET_DATE_NOT_TODAY_OR_NEXT_DAY"):
        approve(db, date(2026, 11, 24))


# ── restart restore (mirrors main.py lifespan) ──────────────────────────────
def test_restart_restores_waiting_state(db, freeze):
    freeze(kst(SAT, 12))
    approve(db, MON)
    assert ocs.session_trading_authorized(settings())
    restored = AutoPaperLoop()   # 새 프로세스
    s = restored.start(now=kst(SAT, 13),
                       armed_for=ocs.armed_session_date(settings(), now=kst(SAT, 13)))
    assert s.state == "WAITING_MARKET" and s.armed_for == MON.isoformat()


def test_emergency_while_waiting_blocks_restore(db, freeze):
    freeze(kst(SAT, 12))
    approve(db, MON)
    loop = AutoPaperLoop()
    loop.start(now=kst(SAT, 12), armed_for=MON)
    loop.emergency_stop()
    result = ocs.block_session_for_emergency(db)
    assert result["blocked"] and result["file"] and result["db"]
    ocs._PROCESS_EMERGENCY_BLOCK = False          # 재시작 = 새 프로세스
    assert ocs.session_trading_authorized(settings(), db=db) is False
    assert ocs.armed_session_date(settings(), now=kst(SAT, 13), db=db) is None


def test_http_emergency_stop_blocks_pending_session(client, db, freeze):
    from app.auto_paper.loop import get_auto_paper_loop
    freeze(kst(SAT, 12))
    approve(db, MON)
    try:
        assert client.post("/api/auto-paper/emergency-stop").status_code == 200
        assert ocs.load_approval()["status"] == "BLOCKED"
    finally:
        get_auto_paper_loop().reset()


def test_waiting_session_cannot_authorize_orders(db, freeze):
    freeze(kst(SAT, 12))
    approve(db, MON)
    ok, reason = asyncio.run(ocs.authorize_order(
        db=db, settings=settings(), broker=SimpleNamespace(),
        risk=SimpleNamespace(emergency_stop=False), side="BUY", symbol="005930",
        quantity=1, notional_krw=10_000, now=kst(SAT, 12)))
    assert ok is False and reason == "APPROVAL_DATE_MISMATCH"


def test_next_trading_day_endpoint(client):
    body = client.get("/api/auto-paper/next-trading-day").json()
    assert set(body) >= {"today", "target_date", "is_today", "weekday"}
    assert body["target_date"] >= body["today"]


def test_start_route_passes_armed_date_to_loop(client, monkeypatch):
    """회귀: post_start 의 armed 판단이 예외로 조용히 None 이 되지 않아야 한다."""
    import app.api.routes_auto_paper as routes
    seen = {}
    real_loop = AutoPaperLoop()

    def _start(**kw):
        seen.update(kw)
        return real_loop.start(now=kst(SAT, 12), armed_for=kw.get("armed_for"))

    monkeypatch.setattr(routes, "get_settings", settings)
    monkeypatch.setattr(ocs, "armed_session_date", lambda _s: MON)
    monkeypatch.setattr(routes, "get_auto_paper_loop",
                        lambda: SimpleNamespace(start=_start))
    body = client.post("/api/auto-paper/start").json()
    assert seen["armed_for"] == MON
    assert body["state"] == "WAITING_MARKET" and body["armed_for"] == MON.isoformat()
