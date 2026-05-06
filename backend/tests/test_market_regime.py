"""Unit tests for market regime classifier (135, MUST)."""

from datetime import datetime, timedelta, timezone

from app.backtest.types import Bar
from app.market.regime import classify_regime, matches_required_regime


def _bars(closes):
    base = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    return [
        Bar(symbol="X", timestamp=base + timedelta(minutes=i),
            open=int(c), high=int(c)+1, low=int(c)-1, close=int(c), volume=10)
        for i, c in enumerate(closes)
    ]


def test_returns_any_when_too_few_bars():
    assert classify_regime([]) == "any"
    assert classify_regime(_bars([100] * 5)) == "any"
    assert classify_regime(_bars([100] * 19)) == "any"


def test_classifies_trending_up_when_short_sma_above_long():
    # 60봉 평탄 100, 20봉 평탄 102 → short SMA ≈ 102, long SMA ≈ 100.5,
    # gap ≈ 1.5% (임계 0.5% 초과). short window stdev=0이라 high_vol 아님.
    closes = [100] * 60 + [102] * 20
    assert classify_regime(_bars(closes)) == "trending_up"


def test_classifies_trending_down_when_short_sma_below_long():
    closes = [100] * 60 + [98] * 20
    assert classify_regime(_bars(closes)) == "trending_down"


def test_classifies_ranging_when_short_and_long_sma_close():
    # 평균이 100인 채로 작은 변동만.
    closes = [100, 101, 99, 100, 101, 99] * 10
    assert classify_regime(_bars(closes)) == "ranging"


def test_high_vol_dominates_trending_classification():
    # 큰 변동으로 stdev가 임계 초과 — trending보다 고변동이 우선.
    closes = [100, 110, 90, 115, 85, 120, 80, 125, 75, 130,
              70, 135, 65, 140, 60, 145, 55, 150, 50, 155,
              45, 160, 40, 165, 35]
    # mean ~ 100, std large → cv > 1.5%
    assert classify_regime(_bars(closes)) == "high_vol"


def test_classify_handles_zero_avg_gracefully():
    assert classify_regime(_bars([0] * 30)) == "any"


def test_matches_any_required_regime_always():
    assert matches_required_regime("trending_up", "any") is True
    assert matches_required_regime("ranging",     "any") is True
    assert matches_required_regime("high_vol",    "any") is True


def test_matches_required_regime_trending_aliases_both_directions():
    assert matches_required_regime("trending_up",   "trending") is True
    assert matches_required_regime("trending_down", "trending") is True
    assert matches_required_regime("ranging",       "trending") is False


def test_matches_required_regime_exact_match():
    assert matches_required_regime("ranging", "ranging") is True
    assert matches_required_regime("ranging", "trending_up") is False


def test_matches_empty_required_treated_as_any():
    """Strategy.base default required_regime은 "any". 빈 문자열 상속해 들어
    오는 경우도 동일하게 취급."""
    assert matches_required_regime("trending_up", "") is True
