"""Tests for feat/step2-market-waiting-mode.

Auto Paper Loop 가 한국장 시간(KST = UTC+9) 을 기준으로 *시작 시점* 에
다음 분기를 따른다:
- 평일 09:00 KST 이전 → WAITING_MARKET (장 시작 대기)
- 평일 09:00 ~ 15:30 KST → RUNNING (정상)
- 평일 15:30 KST 이후 → MARKET_CLOSED (당일 종료)
- 토/일 → MARKET_CLOSED

WAITING_MARKET 상태:
- tick() 호출 시 LoopNotRunningError → handler 호출 0건 (신규 후보 0건)
- status() 호출 시 lazy 로 phase 재확인 → OPEN 이면 자동 RUNNING 으로
  promote
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    LoopNotRunningError,
)
from app.scheduler.market_clock import MarketPhase, current_market_phase


# ─────────────────────────────────────────────────────────────────────────────
# Helper — 특정 KST 시각을 UTC datetime 으로 변환.
# KST = UTC + 9, DST 없음. 평일 09:00 KST = 00:00 UTC.
# ─────────────────────────────────────────────────────────────────────────────

def _kst_to_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """KST naive 시각 → UTC tz-aware datetime."""
    # 2026-05-18 (월) ~ 2026-05-22 (금) 가 평일.
    # KST hour - 9 시 전이면 전날로 wrap (e.g. 08:50 KST → 23:50 UTC 전날)
    utc_hour_unwrapped = hour - 9
    day_adjust = 0
    if utc_hour_unwrapped < 0:
        utc_hour_unwrapped += 24
        day_adjust = -1
    return datetime(
        year, month, day + day_adjust,
        utc_hour_unwrapped, minute,
        tzinfo=timezone.utc,
    )


# ─────────────────────────────────────────────────────────────────────────────
# market_clock 자체 시점 검증.
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketClockPhases:
    """current_market_phase() 가 KST 시점 별로 정확한 phase 를 반환."""

    def test_weekday_before_open_is_pre_open(self):
        # 2026-05-18 (월) 08:50 KST = 2026-05-17 23:50 UTC
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        assert current_market_phase(kst_08_50) == MarketPhase.PRE_OPEN

    def test_weekday_at_open_is_open(self):
        # 2026-05-18 (월) 09:00 KST = 2026-05-18 00:00 UTC
        kst_09_00 = _kst_to_utc(2026, 5, 18, 9, 0)
        assert current_market_phase(kst_09_00) == MarketPhase.OPEN

    def test_weekday_mid_session_is_open(self):
        kst_12_30 = _kst_to_utc(2026, 5, 18, 12, 30)
        assert current_market_phase(kst_12_30) == MarketPhase.OPEN

    def test_weekday_one_minute_before_close_is_open(self):
        kst_15_29 = _kst_to_utc(2026, 5, 18, 15, 29)
        assert current_market_phase(kst_15_29) == MarketPhase.OPEN

    def test_weekday_at_close_is_closed(self):
        kst_15_30 = _kst_to_utc(2026, 5, 18, 15, 30)
        assert current_market_phase(kst_15_30) == MarketPhase.CLOSED

    def test_weekday_after_close_is_closed(self):
        kst_15_31 = _kst_to_utc(2026, 5, 18, 15, 31)
        assert current_market_phase(kst_15_31) == MarketPhase.CLOSED

    def test_saturday_is_weekend(self):
        # 2026-05-23 = 토요일.
        sat_10_00 = _kst_to_utc(2026, 5, 23, 10, 0)
        assert current_market_phase(sat_10_00) == MarketPhase.WEEKEND

    def test_sunday_is_weekend(self):
        # 2026-05-24 = 일요일.
        sun_10_00 = _kst_to_utc(2026, 5, 24, 10, 0)
        assert current_market_phase(sun_10_00) == MarketPhase.WEEKEND


# ─────────────────────────────────────────────────────────────────────────────
# AutoPaperState 신규 멤버 검증.
# ─────────────────────────────────────────────────────────────────────────────

class TestNewAutoPaperStates:
    def test_waiting_market_state_exists(self):
        assert AutoPaperState.WAITING_MARKET.value == "WAITING_MARKET"

    def test_market_closed_state_exists(self):
        assert AutoPaperState.MARKET_CLOSED.value == "MARKET_CLOSED"

    def test_legacy_aliases_preserved(self):
        # 기존 PR 의 alias 호환 — IDLE / EMERGENCY 가 PAUSED / EMERGENCY_STOP
        # 의 alias 로 남아 있어야 한다.
        assert AutoPaperState.IDLE is AutoPaperState.PAUSED
        assert AutoPaperState.EMERGENCY is AutoPaperState.EMERGENCY_STOP

    def test_canonical_state_values_unchanged(self):
        # 외부 API 호환을 위해 기존 canonical 값들은 그대로 보존되어야 한다.
        assert AutoPaperState.PAUSED.value == "PAUSED"
        assert AutoPaperState.RUNNING.value == "RUNNING"
        assert AutoPaperState.STOPPED.value == "STOPPED"
        assert AutoPaperState.EMERGENCY_STOP.value == "EMERGENCY_STOP"


# ─────────────────────────────────────────────────────────────────────────────
# start() 의 market phase 분기.
# ─────────────────────────────────────────────────────────────────────────────

class TestStartMarketPhaseRouting:
    def test_pre_open_start_enters_waiting_market(self):
        # 평일 08:50 KST → WAITING_MARKET
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        status = loop.start(now=kst_08_50)
        assert status.state == "WAITING_MARKET"

    def test_open_start_enters_running(self):
        # 평일 09:00 KST → RUNNING
        loop = AutoPaperLoop()
        kst_09_00 = _kst_to_utc(2026, 5, 18, 9, 0)
        status = loop.start(now=kst_09_00)
        assert status.state == "RUNNING"

    def test_mid_session_start_enters_running(self):
        loop = AutoPaperLoop()
        kst_11_00 = _kst_to_utc(2026, 5, 18, 11, 0)
        status = loop.start(now=kst_11_00)
        assert status.state == "RUNNING"

    def test_after_close_start_enters_market_closed(self):
        # 평일 15:31 KST → MARKET_CLOSED
        loop = AutoPaperLoop()
        kst_15_31 = _kst_to_utc(2026, 5, 18, 15, 31)
        status = loop.start(now=kst_15_31)
        assert status.state == "MARKET_CLOSED"

    def test_at_close_start_enters_market_closed(self):
        # 15:30 KST 정각도 CLOSED 로 간주 (장 종료 시점).
        loop = AutoPaperLoop()
        kst_15_30 = _kst_to_utc(2026, 5, 18, 15, 30)
        status = loop.start(now=kst_15_30)
        assert status.state == "MARKET_CLOSED"

    def test_saturday_start_enters_market_closed(self):
        loop = AutoPaperLoop()
        sat_10_00 = _kst_to_utc(2026, 5, 23, 10, 0)
        status = loop.start(now=sat_10_00)
        assert status.state == "MARKET_CLOSED"

    def test_sunday_start_enters_market_closed(self):
        loop = AutoPaperLoop()
        sun_10_00 = _kst_to_utc(2026, 5, 24, 10, 0)
        status = loop.start(now=sun_10_00)
        assert status.state == "MARKET_CLOSED"

    def test_started_at_set_in_waiting_market(self):
        # WAITING_MARKET 진입 시에도 started_at 은 기록되어야 한다 (감사용).
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        status = loop.start(now=kst_08_50)
        assert status.started_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# WAITING_MARKET / MARKET_CLOSED 상태에서 tick() 차단.
# ─────────────────────────────────────────────────────────────────────────────

class TestWaitingMarketBlocksTick:
    def test_tick_raises_in_waiting_market(self):
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        loop.start(now=kst_08_50)
        with pytest.raises(LoopNotRunningError):
            loop.tick()

    def test_tick_raises_in_market_closed(self):
        loop = AutoPaperLoop()
        kst_15_31 = _kst_to_utc(2026, 5, 18, 15, 31)
        loop.start(now=kst_15_31)
        with pytest.raises(LoopNotRunningError):
            loop.tick()

    def test_waiting_market_does_not_call_paper_handler(self):
        """WAITING_MARKET 에서 tick 시도 → handler 절대 호출 안 됨.

        feat/step2-06-paper-broker-wiring 의 paper_tick_handler 가 등록되어
        있어도 RUNNING 이 아니면 handler 호출 0건 (신규 가상 후보 0건).
        """
        called_count = {"n": 0}

        def handler(ctx):
            called_count["n"] += 1

        loop = AutoPaperLoop(paper_tick_handler=handler)
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        loop.start(now=kst_08_50)

        # tick 호출 3회 모두 차단되어야 한다.
        for _ in range(3):
            with pytest.raises(LoopNotRunningError):
                loop.tick()

        assert called_count["n"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Lazy promotion: WAITING_MARKET → RUNNING via status() at 09:00 KST.
# ─────────────────────────────────────────────────────────────────────────────

class TestWaitingMarketAutoPromote:
    def test_status_promotes_waiting_to_running_at_open(self):
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        loop.start(now=kst_08_50)
        assert loop.status(now=kst_08_50).state == "WAITING_MARKET"

        # 시간이 흘러 09:00 KST 도달.
        kst_09_00 = _kst_to_utc(2026, 5, 18, 9, 0)
        status = loop.status(now=kst_09_00)
        assert status.state == "RUNNING"

    def test_status_keeps_waiting_market_before_open(self):
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        loop.start(now=kst_08_50)

        # 여전히 PRE_OPEN — 그대로 WAITING_MARKET.
        kst_08_55 = _kst_to_utc(2026, 5, 18, 8, 55)
        assert loop.status(now=kst_08_55).state == "WAITING_MARKET"

    def test_tick_works_after_lazy_promotion(self):
        """lazy promote 후 tick 정상 동작 — handler 호출 가능."""
        called_count = {"n": 0}

        def handler(ctx):
            called_count["n"] += 1

        loop = AutoPaperLoop(paper_tick_handler=handler)
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        loop.start(now=kst_08_50)

        # 09:00 KST 에 status() 호출하면 RUNNING 으로 promote.
        kst_09_00 = _kst_to_utc(2026, 5, 18, 9, 0)
        assert loop.status(now=kst_09_00).state == "RUNNING"

        # 이제 tick() 정상 동작.
        loop.tick()
        assert called_count["n"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 회귀 방지 — 기존 동작 보존.
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingBehaviorPreserved:
    def test_status_during_running_does_not_alter_state(self):
        loop = AutoPaperLoop()
        kst_10_00 = _kst_to_utc(2026, 5, 18, 10, 0)
        loop.start(now=kst_10_00)
        # RUNNING 상태에서 status() 가 어떤 phase 입력이든 상태를 바꾸지 않음.
        sat = _kst_to_utc(2026, 5, 23, 10, 0)
        status = loop.status(now=sat)
        assert status.state == "RUNNING"

    def test_status_during_paused_does_not_alter_state(self):
        # 초기 PAUSED 상태에서 status() 가 phase 영향 받지 않음.
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        assert loop.status(now=kst_08_50).state == "PAUSED"

    def test_status_during_emergency_stop_does_not_alter_state(self):
        loop = AutoPaperLoop()
        kst_10_00 = _kst_to_utc(2026, 5, 18, 10, 0)
        loop.start(now=kst_10_00)
        loop.emergency_stop()
        # 09:00 KST 에 status() 호출해도 EMERGENCY_STOP 유지.
        kst_09_00 = _kst_to_utc(2026, 5, 18, 9, 0)
        assert loop.status(now=kst_09_00).state == "EMERGENCY_STOP"

    def test_status_during_market_closed_does_not_promote(self):
        # MARKET_CLOSED 상태는 lazy promote 대상이 아님 (start() 재호출 필요).
        loop = AutoPaperLoop()
        kst_15_31 = _kst_to_utc(2026, 5, 18, 15, 31)
        loop.start(now=kst_15_31)
        # 다음날 09:00 KST 라도 자동 promote 하지 않음.
        next_day_09_00 = _kst_to_utc(2026, 5, 19, 9, 0)
        assert loop.status(now=next_day_09_00).state == "MARKET_CLOSED"

    def test_invariants_carry_in_waiting_market(self):
        """AutoPaperStatus invariants 가 WAITING_MARKET 에서도 유지."""
        loop = AutoPaperLoop()
        kst_08_50 = _kst_to_utc(2026, 5, 18, 8, 50)
        status = loop.start(now=kst_08_50)
        assert status.is_order_signal is False
        assert status.auto_apply_allowed is False
        assert status.forced_paper is True

    def test_invariants_carry_in_market_closed(self):
        loop = AutoPaperLoop()
        kst_15_31 = _kst_to_utc(2026, 5, 18, 15, 31)
        status = loop.start(now=kst_15_31)
        assert status.is_order_signal is False
        assert status.auto_apply_allowed is False
        assert status.forced_paper is True
