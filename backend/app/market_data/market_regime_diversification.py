"""시장 국면(regime) 분류 — 다변화 백테스트 attribution 용 (CHECKLIST-05, research-only).

시장 proxy(KODEX 200 등) 일봉 시계열을 5개 국면으로 분류한다:
STRONG_UPTREND / NORMAL_UPTREND / SIDEWAYS / DOWNTREND / HIGH_VOLATILITY.

**중요(look-ahead 구분)**: 본 regime 라벨은 *당일 종가 + 직전 N일* 을 사용하므로 *장중
진입 시점*에는 알 수 없는 정보를 포함한다. 따라서 regime 라벨은 **사후 attribution 전용**
이며, 어떤 trading rule(진입 신호)도 본 라벨을 사용하지 않는다(`regime_is_lookahead=True`).

read-only / 순수 함수 — broker / 주문 API / 외부 HTTP import 0건.
"""

from __future__ import annotations

from typing import Any

REGIMES = ("STRONG_UPTREND", "NORMAL_UPTREND", "SIDEWAYS", "DOWNTREND", "HIGH_VOLATILITY")


def _daily_from_5m(day_bars: list) -> dict[str, float] | None:
    """5분봉 하루 → 일봉 OHLC (객체/딕셔너리 모두 지원)."""
    if not day_bars:
        return None

    def g(b, k):
        return b.get(k) if isinstance(b, dict) else getattr(b, k, None)

    opens = [g(b, "open") for b in day_bars]
    closes = [g(b, "close") for b in day_bars]
    highs = [g(b, "high") for b in day_bars]
    lows = [g(b, "low") for b in day_bars]
    if not opens or opens[0] in (None, 0):
        return None
    return {"open": float(opens[0]), "close": float(closes[-1]),
            "high": float(max(highs)), "low": float(min(lows))}


def classify_regime(daily_series: list[dict], idx: int, *,
                    strong_mom=0.05, normal_mom=0.01, down_mom=-0.02,
                    high_vol_range=0.04, mom_window=5) -> str:
    """daily_series[idx] 의 국면 — *사후 attribution* (look-ahead 포함, 진입 신호 아님)."""
    d = daily_series[idx]
    o, c = d["open"], d["close"]
    if o <= 0:
        return "SIDEWAYS"
    rng = (d["high"] - d["low"]) / o
    r_day = (c - o) / o
    if idx >= mom_window:
        base = daily_series[idx - mom_window]["close"]
        mom = (c - base) / base if base > 0 else 0.0
    else:
        mom = r_day
    if rng > high_vol_range:
        return "HIGH_VOLATILITY"
    if mom > strong_mom and r_day > 0:
        return "STRONG_UPTREND"
    if mom > normal_mom:
        return "NORMAL_UPTREND"
    if mom < down_mom:
        return "DOWNTREND"
    return "SIDEWAYS"


def build_regime_map(proxy_days_by_date: dict[str, list]) -> dict[str, str]:
    """date(ISO) → 5분봉 리스트 dict 에서 date → regime 라벨 map 산출."""
    dates = sorted(proxy_days_by_date)
    daily = []
    valid_dates = []
    for dte in dates:
        d = _daily_from_5m(proxy_days_by_date[dte])
        if d:
            daily.append(d)
            valid_dates.append(dte)
    return {valid_dates[i]: classify_regime(daily, i) for i in range(len(daily))}


def build_regime_manifest(regime_map: dict[str, str], proxy_symbol: str) -> dict[str, Any]:
    counts: dict[str, int] = {r: 0 for r in REGIMES}
    for r in regime_map.values():
        counts[r] = counts.get(r, 0) + 1
    return {
        "is_research_only": True,
        "proxy_symbol": proxy_symbol,
        "regimes": list(REGIMES),
        "trading_days": len(regime_map),
        "regime_day_counts": counts,
        "regime_is_lookahead": True,
        "note": "regime 라벨은 사후 attribution 전용 — 당일 종가/직전 N일을 사용하므로 장중 "
                "진입 시점에는 알 수 없음. 어떤 진입 신호도 본 라벨을 사용하지 않음.",
        "auto_apply_allowed": False, "applied_to_runtime": False,
        "is_live_authorization": False, "contains_secret": False,
    }
