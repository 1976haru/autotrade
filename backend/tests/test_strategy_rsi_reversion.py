"""RsiReversionStrategy 신호 로직 단위 테스트 (142, MUST).

cross-back 트리거를 fixture-driven으로 검증.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar, Signal
from app.strategies.concrete.rsi_reversion import RsiReversionStrategy


_BASE = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)


def _bars(closes: list[int], symbol: str = "X") -> list[Bar]:
    return [
        Bar(symbol=symbol,
            timestamp=_BASE + timedelta(minutes=i),
            open=c, high=c + 1, low=c - 1, close=c, volume=10)
        for i, c in enumerate(closes)
    ]


def _feed(strat: RsiReversionStrategy, bars: list[Bar]) -> list[Signal]:
    """봉을 누적해가며 on_bar를 호출, 매번의 신호를 모은다 (engine 호출 패턴)."""
    return [strat.on_bar(bars[:i + 1]) for i in range(len(bars))]


# ---------- 입력 검증 ----------

def test_rejects_too_short_period():
    with pytest.raises(ValueError):
        RsiReversionStrategy(period=1)


def test_rejects_invalid_thresholds():
    with pytest.raises(ValueError):
        RsiReversionStrategy(oversold=70, overbought=30)
    with pytest.raises(ValueError):
        RsiReversionStrategy(oversold=0, overbought=70)
    with pytest.raises(ValueError):
        RsiReversionStrategy(oversold=30, overbought=100)


# ---------- 워밍업 ----------

def test_holds_during_warmup_period():
    """period+1 봉이 모일 때까지는 무조건 HOLD."""
    strat = RsiReversionStrategy(period=14)
    closes = list(range(100, 114))  # 14 bars — 1 short of period+1
    sigs = _feed(strat, _bars(closes))
    assert all(s == Signal.HOLD for s in sigs)


# ---------- BUY trigger ----------

def test_buy_on_first_bar_above_oversold_after_oversold_period():
    """장기 하락으로 RSI를 oversold로 끌어내린 뒤, 강한 회복 봉에서 RSI가
    임계 위로 돌아오면 BUY 트리거."""
    strat = RsiReversionStrategy(period=5, oversold=30, overbought=70)
    # 5봉 연속 하락 → RSI = 0 (avg_gain=0). 이후 강한 회복 → RSI > 30.
    descending = [100, 95, 90, 85, 80, 75]   # 6 bars: warmup + first RSI=0
    bars = _bars(descending)
    sigs = _feed(strat, bars)
    # 6번째 봉에서 첫 RSI 산출, _prev_rsi=None이므로 신호 X.
    assert sigs[-1] == Signal.HOLD

    # 이어서 강한 회복 → RSI 상승. 회복 폭이 충분해야 RSI > 30.
    # 단순 RSI: avg_gain / avg_loss 비율로 결정.
    bars2 = _bars(descending + [85, 95, 100])
    sigs = _feed(RsiReversionStrategy(period=5, oversold=30, overbought=70), bars2)
    # 어딘가에서 BUY가 나타나야 한다 — 회복이 커서 RSI > 30 cross.
    assert Signal.BUY in sigs, sigs


def test_no_buy_when_rsi_never_left_oversold():
    """계속 하락하는 동안은 RSI가 oversold에 갇혀 cross-back이 발생 안 함."""
    strat = RsiReversionStrategy(period=5, oversold=30, overbought=70)
    closes = list(range(100, 80, -1))  # 20봉 단조 하락
    sigs = _feed(strat, _bars(closes))
    assert Signal.BUY  not in sigs
    assert Signal.SELL not in sigs


# ---------- SELL trigger ----------

def test_sell_on_first_bar_below_overbought_after_overbought_period():
    """RSI를 overbought로 끌어올린 뒤, 첫 하락 봉에서 RSI가 임계 아래로
    내려오면 SELL 트리거."""
    strat = RsiReversionStrategy(period=5, oversold=30, overbought=70)
    ascending = [100, 105, 110, 115, 120, 125]  # warmup + RSI=100
    # 이어서 하락 → RSI 빠르게 떨어짐.
    closes = ascending + [115, 105, 100]
    sigs = _feed(strat, _bars(closes))
    assert Signal.SELL in sigs, sigs


# ---------- HOLD on flat / mild oscillation ----------

def test_holds_on_mild_oscillation_within_band():
    """RSI가 oversold와 overbought 사이에서 진동하면 신호 X."""
    strat = RsiReversionStrategy(period=5, oversold=30, overbought=70)
    closes = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101]
    sigs = _feed(strat, _bars(closes))
    assert Signal.BUY  not in sigs
    assert Signal.SELL not in sigs


# ---------- RSI math sanity ----------

def test_rsi_handles_zero_loss_as_100():
    """모두 상승만 있으면 avg_loss=0 → RSI=100 (overbought 진입)."""
    strat = RsiReversionStrategy(period=5, oversold=30, overbought=70)
    closes = list(range(100, 110))  # 10봉 연속 상승
    bars = _bars(closes)
    # 전부 상승만 있으니 RSI=100이 유지 → cross-back SELL 발생 X.
    for i in range(len(bars)):
        s = strat.on_bar(bars[:i + 1])
        # RSI는 항상 100 → never crosses below 70 → never SELL.
        assert s != Signal.SELL


# ---------- contract / metadata ----------

def test_metadata_is_complete():
    """142 이후로 stub 표기가 사라지고 contract metadata만 남는다."""
    s = RsiReversionStrategy()
    assert s.entry
    assert s.exit
    assert s.invalidation
    assert s.required_regime == "ranging"
    assert s.risk_profile["position_size_pct"] == 3
