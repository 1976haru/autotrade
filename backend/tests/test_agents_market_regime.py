"""Market Regime Filter unit + API tests (225, MUST).

10 regime states + risk/size multipliers + permission. Deterministic.
"""

from __future__ import annotations

from app.agents.market_regime import classify_market_regime


def test_regime_risk_off_blocks_everything() -> None:
    out = classify_market_regime(risk_off_signal=True)
    assert out.regime == "RISK_OFF"
    assert out.trade_permission == "BLOCK"
    assert out.risk_multiplier == 0.0
    assert out.max_position_size_multiplier == 0.0
    assert out.allowed_strategies == []


def test_regime_opening_chaos_pauses() -> None:
    out = classify_market_regime(is_opening_30min=True, volatility_pct=4.0)
    assert out.regime == "OPENING_CHAOS"
    assert out.trade_permission == "PAUSE"
    assert out.risk_multiplier == 0.5


def test_regime_gap_day_watch() -> None:
    out = classify_market_regime(gap_pct=3.0)
    assert out.regime == "GAP_DAY"
    assert out.trade_permission == "WATCH"
    assert "orb_vwap" in out.allowed_strategies
    assert "rsi_reversion" in out.blocked_strategies


def test_regime_high_volatility_blocks_reversion() -> None:
    out = classify_market_regime(volatility_pct=6.0)
    assert out.regime == "HIGH_VOLATILITY"
    assert out.trade_permission == "WATCH"
    assert "rsi_reversion" in out.blocked_strategies
    assert out.max_position_size_multiplier <= 0.7


def test_regime_low_liquidity_blocks_all() -> None:
    out = classify_market_regime(volume_ratio=0.3)
    assert out.regime == "LOW_LIQUIDITY"
    assert out.allowed_strategies == []
    assert out.trade_permission == "WATCH"


def test_regime_news_driven_when_sentiment_extreme() -> None:
    out = classify_market_regime(news_sentiment=85)
    assert out.regime == "NEWS_DRIVEN"


def test_regime_late_day_fade_minimal_sizing() -> None:
    out = classify_market_regime(is_late_day_30min=True)
    assert out.regime == "LATE_DAY_FADE"
    assert out.max_position_size_multiplier <= 0.3


def test_regime_trend_up_full_size() -> None:
    out = classify_market_regime(trend_strength_pct=2.5)
    assert out.regime == "TREND_UP"
    assert out.trade_permission == "ALLOW"
    assert out.risk_multiplier == 1.0
    assert "rsi_reversion" in out.blocked_strategies


def test_regime_trend_down_reduced_size() -> None:
    out = classify_market_regime(trend_strength_pct=-2.5)
    assert out.regime == "TREND_DOWN"
    assert out.trade_permission == "WATCH"
    assert "orb_vwap" in out.blocked_strategies


def test_regime_choppy_fallback() -> None:
    out = classify_market_regime(trend_strength_pct=0.5, volatility_pct=1.0)
    assert out.regime == "CHOPPY"
    assert "rsi_reversion" in out.allowed_strategies
    assert "orb_vwap" in out.blocked_strategies


def test_regime_priority_risk_off_over_others() -> None:
    """risk_off는 다른 모든 조건보다 우선."""
    out = classify_market_regime(
        risk_off_signal=True, trend_strength_pct=3.0, volatility_pct=1.0,
    )
    assert out.regime == "RISK_OFF"


def test_regime_operator_summary_three_lines() -> None:
    out = classify_market_regime(trend_strength_pct=2.0)
    assert len(out.operator_summary) == 3
    assert any("추세 상승" in s for s in out.operator_summary)


def test_api_market_regime_round_trip(client) -> None:
    res = client.post("/api/agents/market-regime", json={
        "trend_strength_pct": 2.0,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["regime"] == "TREND_UP"
    assert body["risk_multiplier"] == 1.0


def test_api_market_regime_risk_off_blocks(client) -> None:
    res = client.post("/api/agents/market-regime", json={
        "risk_off_signal": True,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["regime"] == "RISK_OFF"
    assert body["trade_permission"] == "BLOCK"
