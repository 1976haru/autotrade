"""Point-in-time(PIT) 시장국면 분류 — 진입 시점 정보만 사용 (CHECKLIST-05, research-only).

기존 `market_regime_diversification.classify_regime` 는 *당일 종가/당일 range* 를 사용해
사후(look-ahead) 라벨이므로 매매 규칙으로 쓸 수 없다. 본 모듈은 **진입 시점에 이미 알 수
있는 정보만**(전일 정보 + 당일 장초반 entry_time 이전 정보)으로 regime 을 분류한다.

**금지 정보(절대 미사용)**: 당일 종가 / 당일 전체 range / entry_time 이후 고가·저가·거래량 /
사후 통계. → `point_in_time_features_available_at_entry=True` 불변.

순수 함수 — broker / 주문 API / route_order / 외부 HTTP import 0건. research-only,
어떤 진입 신호도 자동 생성하지 않는다(attribution 전용).
"""

from __future__ import annotations

from typing import Any

PIT_REGIMES = ("PIT_STRONG_UPTREND", "PIT_NORMAL_UPTREND", "PIT_SIDEWAYS",
               "PIT_DOWNTREND", "PIT_HIGH_VOLATILITY", "PIT_UNKNOWN")

# 진입 시점 이전 정보만 (금지 목록은 _FORBIDDEN, 테스트로 lock).
ALLOWED_FEATURES = (
    "previous_day_return", "previous_3d_return", "previous_5d_return",
    "previous_day_range", "previous_day_volatility",
    "opening_gap", "first_5m_return", "price_vs_open", "price_vs_vwap_so_far",
    "opening_range_width_so_far", "cumulative_volume_ratio_so_far",
)
FORBIDDEN_FEATURES = (
    "same_day_close", "same_day_full_range", "post_entry_high", "post_entry_low",
    "post_entry_volume", "posthoc_count",
)


def proxy_pit_daily_features(daily_closes: list[float], daily_ranges: list[float],
                             idx: int) -> dict[str, float | None]:
    """proxy 일봉 시계열에서 *idx 일 진입 시점*(= idx-1 일까지) 알 수 있는 모멘텀/변동성.

    당일(idx) 종가/range 는 사용하지 않는다 — 전일(idx-1) 까지만.
    """
    if idx <= 0:
        return {"previous_day_return": None, "previous_3d_return": None,
                "previous_5d_return": None, "previous_day_range": None}

    def ret(n: int) -> float | None:
        j = idx - 1 - n
        if j < 0 or daily_closes[idx - 1] in (None, 0):
            return None
        base = daily_closes[j]
        return (daily_closes[idx - 1] - base) / base if base else None

    return {
        "previous_day_return": ret(1),
        "previous_3d_return": ret(3),
        "previous_5d_return": ret(5),
        "previous_day_range": daily_ranges[idx - 1] if idx - 1 < len(daily_ranges) else None,
    }


def classify_pit_regime(feats: dict[str, Any], *, strong_mom=0.04, normal_mom=0.01,
                        down_mom=-0.02, high_vol_range=0.04) -> tuple[str, float]:
    """진입 시점 정보 dict → (PIT regime 라벨, confidence 0~1).

    1차: 전일까지의 5일 모멘텀 + 전일 range. 2차(confidence): 당일 장초반 early 정보 일치.
    """
    prev5 = feats.get("previous_5d_return")
    prev_range = feats.get("previous_day_range")
    early = feats.get("first_5m_return")
    gap = feats.get("opening_gap")

    if prev5 is None and prev_range is None:
        return "PIT_UNKNOWN", 0.0

    if prev_range is not None and prev_range > high_vol_range:
        base = "PIT_HIGH_VOLATILITY"
    elif prev5 is not None and prev5 > strong_mom:
        base = "PIT_STRONG_UPTREND"
    elif prev5 is not None and prev5 > normal_mom:
        base = "PIT_NORMAL_UPTREND"
    elif prev5 is not None and prev5 < down_mom:
        base = "PIT_DOWNTREND"
    else:
        base = "PIT_SIDEWAYS"

    # confidence: 당일 장초반(진입 이전) early/gap 이 base 방향과 일치하면 상향.
    conf = 0.5
    signals = [s for s in (early, gap) if s is not None]
    if signals:
        avg = sum(signals) / len(signals)
        up = base in ("PIT_STRONG_UPTREND", "PIT_NORMAL_UPTREND")
        if up and avg > 0:
            conf = 0.7
        elif base == "PIT_DOWNTREND" and avg < 0:
            conf = 0.7
        elif (up and avg < 0) or (base == "PIT_DOWNTREND" and avg > 0):
            conf = 0.3
    return base, round(conf, 2)


# posthoc regime ↔ PIT regime 방향 매핑 (agreement rate 산출용).
_DIRECTION = {
    "STRONG_UPTREND": "UP", "NORMAL_UPTREND": "UP", "UPTREND": "UP",
    "SIDEWAYS": "FLAT", "DOWNTREND": "DOWN", "HIGH_VOLATILITY": "VOL",
    "PIT_STRONG_UPTREND": "UP", "PIT_NORMAL_UPTREND": "UP", "PIT_SIDEWAYS": "FLAT",
    "PIT_DOWNTREND": "DOWN", "PIT_HIGH_VOLATILITY": "VOL", "PIT_UNKNOWN": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
}


def regime_direction(label: str) -> str:
    return _DIRECTION.get(label, "UNKNOWN")


def lookahead_flags() -> dict[str, Any]:
    """posthoc regime 의 look-ahead 속성 명시 (report 표시용)."""
    return {
        "posthoc_regime_is_lookahead": True,
        "posthoc_attribution_only": True,
        "posthoc_not_tradeable_signal": True,
        "posthoc_uses_same_day_close": True,
        "posthoc_uses_same_day_range": True,
        "point_in_time_features_available_at_entry": True,
        "point_in_time_attribution_only": True,
        "allowed_features": list(ALLOWED_FEATURES),
        "forbidden_features": list(FORBIDDEN_FEATURES),
    }
