"""Point-in-time regime attribution 테스트 (CHECKLIST-05, research-only, 실주문/자동적용 0).

키워드 universe_regime / point_in_time / market_regime 로 -k 매칭.
"""

from __future__ import annotations

from pathlib import Path

from app.backtest import point_in_time_regime as pit


# ─────────── PIT 분류 (진입 시점 정보만) ───────────


def test_pit_regimes_labels():
    assert "PIT_STRONG_UPTREND" in pit.PIT_REGIMES
    assert "PIT_UNKNOWN" in pit.PIT_REGIMES
    assert len(pit.PIT_REGIMES) == 6


def test_proxy_pit_uses_only_prior_days():
    closes = [100, 101, 102, 103, 104, 105, 110]   # idx 6 = 진입일
    ranges = [0.01] * 7
    f = pit.proxy_pit_daily_features(closes, ranges, 6)
    # prev5 = (closes[5]-closes[0])/closes[0] = (105-100)/100 = 0.05 (당일 closes[6]=110 미사용)
    assert abs(f["previous_5d_return"] - 0.05) < 1e-9
    assert f["previous_day_range"] == 0.01
    # idx 0 → 정보 없음.
    assert pit.proxy_pit_daily_features(closes, ranges, 0)["previous_5d_return"] is None


def test_classify_pit_strong_uptrend():
    feats = {"previous_5d_return": 0.06, "previous_day_range": 0.02,
             "first_5m_return": 0.01, "opening_gap": 0.005}
    label, conf = pit.classify_pit_regime(feats)
    assert label == "PIT_STRONG_UPTREND"
    assert conf >= 0.7   # 당일 장초반 상승 일치 → confidence 상향


def test_classify_pit_downtrend_and_highvol():
    assert pit.classify_pit_regime({"previous_5d_return": -0.05, "previous_day_range": 0.02})[0] == "PIT_DOWNTREND"
    assert pit.classify_pit_regime({"previous_5d_return": 0.06, "previous_day_range": 0.08})[0] == "PIT_HIGH_VOLATILITY"
    assert pit.classify_pit_regime({})[0] == "PIT_UNKNOWN"


def test_pit_confidence_downgraded_on_disagreement():
    # 전일 강세인데 당일 장초반 하락 → confidence 하향.
    feats = {"previous_5d_return": 0.06, "previous_day_range": 0.02,
             "first_5m_return": -0.02, "opening_gap": -0.01}
    _label, conf = pit.classify_pit_regime(feats)
    assert conf <= 0.3


def test_regime_direction_mapping():
    assert pit.regime_direction("STRONG_UPTREND") == "UP"
    assert pit.regime_direction("PIT_STRONG_UPTREND") == "UP"
    assert pit.regime_direction("PIT_DOWNTREND") == "DOWN"
    assert pit.regime_direction("HIGH_VOLATILITY") == "VOL"


def test_lookahead_flags_mark_posthoc_not_tradeable():
    f = pit.lookahead_flags()
    assert f["posthoc_regime_is_lookahead"] is True
    assert f["posthoc_not_tradeable_signal"] is True
    assert f["posthoc_uses_same_day_close"] is True
    assert f["posthoc_uses_same_day_range"] is True
    assert f["point_in_time_features_available_at_entry"] is True


def test_forbidden_features_not_in_allowed():
    assert not (set(pit.ALLOWED_FEATURES) & set(pit.FORBIDDEN_FEATURES))
    for bad in ("same_day_close", "same_day_full_range", "post_entry_high"):
        assert bad in pit.FORBIDDEN_FEATURES
        assert bad not in pit.ALLOWED_FEATURES


# ─────────── 모듈 안전 (no look-ahead feature names / no order infra) ───────────


def test_pit_module_no_order_imports_and_no_forbidden_feature_use():
    txt = (Path(__file__).resolve().parents[1]
           / "app/backtest/point_in_time_regime.py").read_text(encoding="utf-8")
    for bad in (".place_order(", "route_order(", "OrderExecutor(", "import httpx",
                "import requests", "register_strategy(", "STRATEGY_REGISTRY["):
        assert bad not in txt
    # 금지 feature 를 *분류 로직* 에서 키로 읽지 않는다(FORBIDDEN/문자열 정의는 허용).
    assert 'feats.get("same_day_close")' not in txt
    assert 'feats.get("same_day_full_range")' not in txt
