"""P1-B — market_clock 은 KRX 휴장일 달력을 반영한다 (휴장일 = WEEKEND 와 동일 휴장).

같은 사실(오늘 장이 열리는가)을 market_clock 과 intraday_session 이 따로 판단해
휴장일에 루프가 RUNNING 으로 표시되던 문제의 회귀 가드.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.auto_paper.loop import AutoPaperLoop, AutoPaperState
from app.scheduler.intraday_session import KrxCalendar, load_krx_calendar
from app.scheduler.market_clock import MarketPhase, current_market_phase

KST = timezone(timedelta(hours=9))


def kst(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), KST)


HOLIDAYS = [date(2026, 12, 25), date(2026, 12, 31), date(2027, 1, 1),
            date(2027, 2, 8), date(2027, 2, 9)]


@pytest.mark.parametrize("d", HOLIDAYS)
def test_holiday_is_closed_all_day(d):
    for hh in (8, 10, 14, 16):
        assert current_market_phase(kst(d, hh)) == MarketPhase.WEEKEND


@pytest.mark.parametrize("d", HOLIDAYS)
def test_loop_start_on_holiday_is_market_closed(d):
    loop = AutoPaperLoop()
    assert loop.start(now=kst(d, 10)).state == AutoPaperState.MARKET_CLOSED.value


def test_normal_weekday_runs():
    d = date(2026, 11, 23)   # 월
    assert current_market_phase(kst(d, 10)) == MarketPhase.OPEN
    assert AutoPaperLoop().start(now=kst(d, 10)).state == AutoPaperState.RUNNING.value


def test_running_loop_demotes_when_status_polled_on_holiday():
    loop = AutoPaperLoop()
    assert loop.start(now=kst(date(2026, 12, 24), 10)).state == "RUNNING"
    assert loop.status(now=kst(date(2026, 12, 25), 10)).state == "MARKET_CLOSED"


def test_delayed_open_special_session_waits_until_open():
    d = date(2026, 1, 2)   # 새해 첫 거래일 10:00 개장 (확정 특수 세션)
    assert current_market_phase(kst(d, 9, 30)) == MarketPhase.PRE_OPEN
    loop = AutoPaperLoop()
    assert loop.start(now=kst(d, 9, 30)).state == AutoPaperState.WAITING_MARKET.value
    assert loop.status(now=kst(d, 10, 1)).state == AutoPaperState.RUNNING.value


def test_special_session_close_follows_calendar():
    cal = KrxCalendar(frozenset(), {"2026-11-25": ("10:00", "16:30")},
                      frozenset({2026}))
    d = date(2026, 11, 25)
    assert current_market_phase(kst(d, 9, 30), calendar=cal) == MarketPhase.PRE_OPEN
    assert current_market_phase(kst(d, 16, 0), calendar=cal) == MarketPhase.OPEN
    assert current_market_phase(kst(d, 16, 30), calendar=cal) == MarketPhase.CLOSED


def test_unknown_year_falls_back_to_weekday_rule():
    d = date(2031, 12, 25)   # 달력 미커버 연도 (목) — 요일 기준
    assert load_krx_calendar().status(d).value == "UNKNOWN"
    assert current_market_phase(kst(d, 10)) == MarketPhase.OPEN


def test_injected_calendar_holiday():
    cal = KrxCalendar(frozenset({"2026-11-24"}), {}, frozenset({2026}))
    assert current_market_phase(kst(date(2026, 11, 24), 10), calendar=cal) == MarketPhase.WEEKEND
    assert current_market_phase(kst(date(2026, 11, 23), 10), calendar=cal) == MarketPhase.OPEN


def test_run_readiness_market_session_closed_on_holiday(client, monkeypatch):
    import app.api.routes_auto_paper as routes
    real = routes.datetime
    frozen = kst(date(2026, 12, 25), 10)

    class _Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    monkeypatch.setattr(routes, "datetime", _Frozen)
    body = client.get("/api/auto-paper/run-readiness").json()
    assert body["market_session"]["is_open"] is False
    assert body["market_session"]["phase"] == "WEEKEND"
