"""OrbVwapStrategy 신호 로직 단위 테스트 (142, MUST).

ORB 형성 → 돌파 → BUY/SELL 트리거 + 일별 reset + 일중 1회 진입 invariant.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar, Signal
from app.strategies.concrete.orb_vwap import OrbVwapStrategy


_DAY1 = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
_DAY2 = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)


def _bar(ts: datetime, ohlc: tuple[int, int, int, int], volume: int = 100) -> Bar:
    op, hi, lo, cl = ohlc
    return Bar(symbol="X", timestamp=ts, open=op, high=hi, low=lo, close=cl, volume=volume)


def _feed(strat: OrbVwapStrategy, bars: list[Bar]) -> list[Signal]:
    """누적 호출 — strategy state가 일자/누적 카운터로 진화한다."""
    return [strat.on_bar(bars[:i + 1]) for i in range(len(bars))]


# ---------- 입력 검증 ----------

def test_rejects_zero_orb_bars():
    with pytest.raises(ValueError):
        OrbVwapStrategy(orb_bars=0)


# ---------- ORB phase: signal 차단 ----------

def test_holds_during_orb_phase():
    """ORB 형성 구간(첫 N봉)에서는 어떤 거래량/가격도 신호를 만들지 않아야 한다."""
    strat = OrbVwapStrategy(orb_bars=3)
    bars = [
        _bar(_DAY1 + timedelta(minutes=i),
             (100, 100 + i * 10, 100 - i, 100 + i * 5))
        for i in range(3)
    ]
    sigs = _feed(strat, bars)
    assert sigs == [Signal.HOLD, Signal.HOLD, Signal.HOLD]


# ---------- BUY breakout ----------

def test_buy_on_first_breakout_above_orb_high_and_vwap():
    """ORB 형성 후 첫 봉이 ORB 상단 + VWAP을 동시에 상향 돌파하면 BUY."""
    strat = OrbVwapStrategy(orb_bars=3)
    bars = [
        _bar(_DAY1 + timedelta(minutes=0), (100, 102, 98,  100), volume=100),
        _bar(_DAY1 + timedelta(minutes=1), (100, 103, 99,  101), volume=100),
        _bar(_DAY1 + timedelta(minutes=2), (101, 103, 100, 102), volume=100),
        # 4번째 봉 — ORB high=103 / VWAP ≈ 100.something. 110 마감은 둘 다 위.
        _bar(_DAY1 + timedelta(minutes=3), (102, 112, 101, 110), volume=200),
    ]
    sigs = _feed(strat, bars)
    assert sigs[-1] == Signal.BUY, sigs


def test_no_buy_when_close_above_orb_but_below_vwap():
    """ORB 상단 돌파해도 VWAP 아래에서 마감하면 신호 X — 두 조건 동시 충족 필수."""
    # 매우 큰 거래량으로 일찍 VWAP을 끌어올린 뒤, 살짝 ORB 위로 가지만 VWAP 아래
    # 머무르는 케이스를 만든다.
    strat = OrbVwapStrategy(orb_bars=3)
    bars = [
        _bar(_DAY1 + timedelta(minutes=0), (100, 110, 90, 100), volume=10_000),  # vwap pull-up
        _bar(_DAY1 + timedelta(minutes=1), (100, 105, 95, 100), volume=100),
        _bar(_DAY1 + timedelta(minutes=2), (100, 105, 95, 100), volume=100),
        # 4번째 봉: ORB high=110 — 105 마감은 ORB 안 (BUY 조건 미충족).
        _bar(_DAY1 + timedelta(minutes=3), (100, 108, 99, 105), volume=100),
    ]
    sigs = _feed(strat, bars)
    assert sigs[-1] == Signal.HOLD


# ---------- SELL breakdown ----------

def test_sell_on_first_breakdown_below_orb_low_and_vwap():
    """ORB 형성 후 첫 봉이 ORB 하단 + VWAP을 동시에 하향 이탈하면 SELL."""
    strat = OrbVwapStrategy(orb_bars=3)
    bars = [
        _bar(_DAY1 + timedelta(minutes=0), (100, 102, 98,  100), volume=100),
        _bar(_DAY1 + timedelta(minutes=1), (100, 102, 99,  100), volume=100),
        _bar(_DAY1 + timedelta(minutes=2), (100, 101, 99,  100), volume=100),
        # 4번째 봉 — ORB low=98 / VWAP ≈ 100. 90 마감은 둘 다 아래.
        _bar(_DAY1 + timedelta(minutes=3), (99, 100, 88, 90), volume=200),
    ]
    sigs = _feed(strat, bars)
    assert sigs[-1] == Signal.SELL


# ---------- 일중 한 번만 진입 ----------

def test_only_first_breakout_fires_signal_within_a_day():
    """일중 ORB 돌파가 두 번 일어나도 진입 신호는 한 번만 — 단타 자동매매에서는
    일중 재진입을 의도적으로 제한한다 (운영자가 별도로 결정)."""
    strat = OrbVwapStrategy(orb_bars=2)
    bars = [
        _bar(_DAY1 + timedelta(minutes=0), (100, 102, 98, 100), volume=100),
        _bar(_DAY1 + timedelta(minutes=1), (100, 103, 99, 101), volume=100),
        # 첫 돌파 — BUY.
        _bar(_DAY1 + timedelta(minutes=2), (101, 110, 101, 108), volume=200),
        # 잠시 ORB 안으로 복귀 (cross 깨짐).
        _bar(_DAY1 + timedelta(minutes=3), (108, 109, 100, 102), volume=100),
        # 다시 돌파 — 두 번째라 신호 X.
        _bar(_DAY1 + timedelta(minutes=4), (102, 112, 101, 110), volume=200),
    ]
    sigs = _feed(strat, bars)
    assert sigs[2] == Signal.BUY
    assert sigs[4] == Signal.HOLD, "second breakout same day must not re-fire"


# ---------- 일별 reset ----------

def test_resets_state_at_day_boundary():
    """다음 거래일이 되면 ORB / VWAP / fired_today 모두 reset — 새 ORB 형성 후
    돌파에서 다시 BUY 신호 가능."""
    strat = OrbVwapStrategy(orb_bars=2)
    day1 = [
        _bar(_DAY1 + timedelta(minutes=0), (100, 102, 98, 100), volume=100),
        _bar(_DAY1 + timedelta(minutes=1), (100, 103, 99, 101), volume=100),
        _bar(_DAY1 + timedelta(minutes=2), (101, 110, 101, 108), volume=200),
    ]
    day2 = [
        _bar(_DAY2 + timedelta(minutes=0), (200, 202, 198, 200), volume=100),
        _bar(_DAY2 + timedelta(minutes=1), (200, 203, 199, 201), volume=100),
        _bar(_DAY2 + timedelta(minutes=2), (201, 210, 201, 208), volume=200),
    ]
    sigs = _feed(strat, day1 + day2)
    assert sigs[2] == Signal.BUY  # day1 breakout
    assert sigs[3] == Signal.HOLD # day2 first bar resets state
    # day2 ORB takes 2 bars (bars 3,4); breakout at bar 5.
    assert sigs[5] == Signal.BUY


# ---------- VWAP edge: zero volume session ----------

def test_holds_when_session_volume_is_zero():
    """거래량 0 세션 — VWAP 정의 불가. 안전 측 HOLD."""
    strat = OrbVwapStrategy(orb_bars=2)
    bars = [
        _bar(_DAY1 + timedelta(minutes=0), (100, 102, 98, 100), volume=0),
        _bar(_DAY1 + timedelta(minutes=1), (100, 103, 99, 101), volume=0),
        _bar(_DAY1 + timedelta(minutes=2), (101, 110, 101, 108), volume=0),
    ]
    sigs = _feed(strat, bars)
    assert sigs[2] == Signal.HOLD


# ---------- contract / metadata ----------

def test_metadata_is_complete():
    s = OrbVwapStrategy()
    assert s.entry
    assert s.exit
    assert s.invalidation
    assert s.required_regime == "trending_up"
    assert s.risk_profile["position_size_pct"] == 5
