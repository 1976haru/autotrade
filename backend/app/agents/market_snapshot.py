"""P-22: Decision Episode 시장 스냅샷 — 판단 당시 시장상태 기록.

AI 가 BUY/SELL/HOLD 를 판단한 *순간* 의 시장 데이터를 구조화해 episode 에
저장한다. 사후 "왜 맞았고 왜 틀렸는지" 분석에 판단 당시 시장상태가 필요하다.

저장 항목: 현재가/시가/고가/저가/전일종가 · 거래량/거래대금 · VWAP · RSI ·
MACD · 이동평균 · 갭률 · 장 시작 후 경과(min) · market_time_phase ·
market_regime · price_age_seconds · data_status(OK / NO_MARKET_DATA /
PRICE_STALE).

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *기록 전용* — broker / OrderExecutor / route_order / 외부 HTTP
  import 0건, 주문을 만들지 않는다.
- secret / API key / 계좌번호 carry 0건.
- `MarketSnapshot.is_live_authorization=False` / `contains_secret=False` 영구.
- 없는 지표는 null (계산 불가 시 추정/조작 0건).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any

# data_status 상수.
DATA_STATUS_OK              = "OK"
DATA_STATUS_NO_MARKET_DATA  = "NO_MARKET_DATA"
DATA_STATUS_PRICE_STALE     = "PRICE_STALE"

DEFAULT_MAX_AGE_SECONDS = 60

# 한국장 정규 시간 (KST). market_clock 과 동일 기준.
_OPEN  = time(9, 0)
_CLOSE = time(15, 30)


def _to_kst(now: datetime):
    from app.scheduler.market_clock import to_kst
    return to_kst(now)


def market_time_phase(now: datetime) -> str:
    """장 시점 단계 — PRE_MARKET/OPENING_RANGE/MORNING/MIDDAY/CLOSING/AFTER_MARKET.

    주말은 AFTER_MARKET 로 분류 (정규장 외).
    """
    kst = _to_kst(now)
    if kst.weekday() >= 5:
        return "AFTER_MARKET"
    t = kst.time()
    if t < _OPEN:
        return "PRE_MARKET"
    if t >= _CLOSE:
        return "AFTER_MARKET"
    # 정규장 내 세부 단계.
    if t < time(9, 30):
        return "OPENING_RANGE"
    if t < time(11, 30):
        return "MORNING"
    if t < time(14, 30):
        return "MIDDAY"
    return "CLOSING"


def minutes_since_open(now: datetime) -> int | None:
    """장 시작(09:00 KST) 후 경과 분. 장 시작 전이면 음수 가능, 주말이면 None."""
    kst = _to_kst(now)
    if kst.weekday() >= 5:
        return None
    open_dt = kst.replace(hour=9, minute=0, second=0, microsecond=0)
    return int((kst - open_dt).total_seconds() // 60)


# ─────────────────────────────────────────────────────────────────────────────
# 지표 계산 (없으면 None — 추정/조작 0건)
# ─────────────────────────────────────────────────────────────────────────────


def _sma(closes: list[float], n: int) -> float | None:
    if len(closes) < n or n <= 0:
        return None
    return round(sum(closes[-n:]) / n, 4)


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI. 표본 부족(< period+1)이면 None."""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _ema(values: list[float], n: int) -> float | None:
    if len(values) < n or n <= 0:
        return None
    k = 2.0 / (n + 1)
    ema = sum(values[:n]) / n
    for v in values[n:]:
        ema = v * k + ema * (1 - k)
    return ema


def compute_macd(closes: list[float], fast: int = 12, slow: int = 26,
                 signal: int = 9) -> dict[str, float] | None:
    """MACD(12,26,9). 표본 부족(< slow+signal)이면 None."""
    if len(closes) < slow + signal:
        return None
    macd_series: list[float] = []
    for end in range(slow, len(closes) + 1):
        window = closes[:end]
        ef = _ema(window, fast)
        es = _ema(window, slow)
        if ef is None or es is None:
            continue
        macd_series.append(ef - es)
    if len(macd_series) < signal:
        return None
    macd_line = macd_series[-1]
    signal_line = _ema(macd_series, signal)
    if signal_line is None:
        return None
    return {
        "macd":      round(macd_line, 4),
        "signal":    round(signal_line, 4),
        "histogram": round(macd_line - signal_line, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot dataclass + builder
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketSnapshot:
    """판단 당시 시장 스냅샷 — episode.market_snapshot 에 저장되는 안전 payload."""

    symbol:          str | None
    data_status:     str                       # OK / NO_MARKET_DATA / PRICE_STALE
    price:           float | None = None        # 현재가
    open:            float | None = None
    high:            float | None = None
    low:             float | None = None
    previous_close:  float | None = None
    volume:          float | None = None
    avg_volume:      float | None = None
    trading_value:   float | None = None
    vwap:            float | None = None
    rsi:             float | None = None
    macd:            dict[str, float] | None = None
    moving_averages: dict[str, float | None] = field(default_factory=dict)
    gap_pct:         float | None = None
    minutes_since_open: int | None = None
    market_time_phase:  str | None = None
    market_regime:   str | None = None
    price_age_seconds: float | None = None
    reason_code:     str | None = None          # PRICE_STALE / NO_MARKET_DATA 등

    contains_secret:       bool = False
    is_live_authorization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":             self.symbol,
            "data_status":        self.data_status,
            "price":              self.price,
            "open":               self.open,
            "high":               self.high,
            "low":                self.low,
            "previous_close":     self.previous_close,
            "volume":             self.volume,
            "avg_volume":         self.avg_volume,
            "trading_value":      self.trading_value,
            "vwap":               self.vwap,
            "rsi":                self.rsi,
            "macd":               self.macd,
            "moving_averages":    dict(self.moving_averages),
            "gap_pct":            self.gap_pct,
            "minutes_since_open": self.minutes_since_open,
            "market_time_phase":  self.market_time_phase,
            "market_regime":      self.market_regime,
            "price_age_seconds":  self.price_age_seconds,
            "reason_code":        self.reason_code,
            "contains_secret":       False,
            "is_live_authorization": False,
        }


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_market_snapshot(
    *,
    market_input: Any = None,
    now: datetime | None = None,
    market_regime: str | None = None,
    price_timestamp: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    symbol: str | None = None,
    price: float | None = None,
) -> MarketSnapshot:
    """StrategyMarketInput(있으면) 으로부터 시장 스냅샷 구성. 없으면 NO_MARKET_DATA.

    `market_input` 은 `app.agents.agent_council.StrategyMarketInput` (duck-typed).
    없거나 current_price 가 없으면 `data_status=NO_MARKET_DATA`. `price_timestamp`
    가 `max_age_seconds` 초과 oldness 면 `data_status=PRICE_STALE`.
    best-effort — 어떤 입력에서도 예외를 던지지 않는다.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    phase = market_time_phase(now)
    mso = minutes_since_open(now)

    # market_input 우선, 없으면 개별 인자.
    mi = market_input
    cur_price = _f(getattr(mi, "current_price", None)) if mi is not None else _f(price)
    sym = (getattr(mi, "symbol", None) if mi is not None else None) or symbol

    if mi is None and cur_price is None:
        return MarketSnapshot(
            symbol=sym, data_status=DATA_STATUS_NO_MARKET_DATA,
            market_regime=market_regime, minutes_since_open=mso,
            market_time_phase=phase, reason_code=DATA_STATUS_NO_MARKET_DATA,
        )

    # price_age / stale 판정.
    price_age = None
    reason_code = None
    data_status = DATA_STATUS_OK
    if price_timestamp is not None:
        ts = price_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        price_age = max(0.0, (now - ts).total_seconds())
        if int(max_age_seconds) > 0 and price_age > float(max_age_seconds):
            data_status = DATA_STATUS_PRICE_STALE
            reason_code = DATA_STATUS_PRICE_STALE

    if cur_price is None:
        return MarketSnapshot(
            symbol=sym, data_status=DATA_STATUS_NO_MARKET_DATA,
            market_regime=market_regime, minutes_since_open=mso,
            market_time_phase=phase, reason_code=DATA_STATUS_NO_MARKET_DATA,
            price_age_seconds=price_age,
        )

    open_p   = _f(getattr(mi, "open_price", None)) if mi is not None else None
    high_p   = _f(getattr(mi, "opening_range_high", None)) if mi is not None else None
    low_p    = _f(getattr(mi, "opening_range_low", None)) if mi is not None else None
    prev_c   = _f(getattr(mi, "prev_close", None)) if mi is not None else None
    volume   = _f(getattr(mi, "current_volume", None)) if mi is not None else None
    avg_vol  = _f(getattr(mi, "avg_volume", None)) if mi is not None else None
    vwap     = _f(getattr(mi, "vwap", None)) if mi is not None else None
    closes_raw = getattr(mi, "recent_closes", None) if mi is not None else None
    closes = [c for c in (
        [float(x) for x in closes_raw] if closes_raw else []
    )]

    gap_pct = None
    if open_p is not None and prev_c not in (None, 0):
        gap_pct = round((open_p - prev_c) / prev_c * 100.0, 4)

    trading_value = None
    if volume is not None and cur_price is not None:
        trading_value = round(cur_price * volume, 2)

    moving_averages = {
        "sma_5":  _sma(closes, 5),
        "sma_10": _sma(closes, 10),
        "sma_20": _sma(closes, 20),
    }

    return MarketSnapshot(
        symbol=sym, data_status=data_status, price=cur_price,
        open=open_p, high=high_p, low=low_p, previous_close=prev_c,
        volume=volume, avg_volume=avg_vol, trading_value=trading_value,
        vwap=vwap, rsi=compute_rsi(closes), macd=compute_macd(closes),
        moving_averages=moving_averages, gap_pct=gap_pct,
        minutes_since_open=mso, market_time_phase=phase,
        market_regime=(market_regime or (getattr(mi, "market_regime", None)
                                         if mi is not None else None)),
        price_age_seconds=price_age, reason_code=reason_code,
    )


__all__ = [
    "MarketSnapshot",
    "build_market_snapshot",
    "market_time_phase",
    "minutes_since_open",
    "compute_rsi",
    "compute_macd",
    "DATA_STATUS_OK",
    "DATA_STATUS_NO_MARKET_DATA",
    "DATA_STATUS_PRICE_STALE",
]
