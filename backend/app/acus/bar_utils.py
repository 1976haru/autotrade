"""ACUS bar 유틸 — KST 일자 분리 + 일봉 집계 (read-only, 시세 변형 없음)."""

from __future__ import annotations

from datetime import date
from typing import Any


def bar_date(bar: Any) -> date | None:
    """bar.timestamp(+09:00 aware datetime)의 KST 캘린더 날짜."""
    ts = getattr(bar, "timestamp", None)
    if ts is None:
        return None
    try:
        return ts.date()
    except AttributeError:
        return None


def sorted_unique_dates(bars: list[Any]) -> list[date]:
    return sorted({d for b in bars if (d := bar_date(b)) is not None})


def split_train_validate(
    bars: list[Any], *, train_days: int = 40, validate_days: int = 20
) -> "tuple[list[Any], list[Any], list[date], list[date]]":
    """앞 train_days 거래일 = train, 그 다음 validate_days 거래일 = validate.

    미래 데이터가 train 에 섞이지 않도록 날짜 정렬 후 분리(look-ahead 방지).
    거래일이 부족하면 가능한 만큼만 채운다(빈 validate 가능).
    """
    dates = sorted_unique_dates(bars)
    train_set = set(dates[:train_days])
    validate_set = set(dates[train_days : train_days + validate_days])
    train = [b for b in bars if bar_date(b) in train_set]
    validate = [b for b in bars if bar_date(b) in validate_set]
    return train, validate, sorted(train_set), sorted(validate_set)


def filter_bars_by_dates(bars: list[Any], days: "set[date]") -> list[Any]:
    return [b for b in bars if bar_date(b) in days]


def daily_aggregates(bars: list[Any]) -> "dict[str, list[float]]":
    """5분봉 → 일봉 집계.

    반환: {"closes", "ranges", "highs", "lows", "turnovers", "volumes"} (일자 정렬).
      - closes: 일 마지막 bar 종가
      - ranges: (일중 high - 일중 low) / 일중 종가  (정규화 변동폭)
      - turnovers: sum(close*volume) 일별 (거래대금 추정)
      - volumes: 일별 거래량 합
    """
    by_day: dict[date, list[Any]] = {}
    for b in bars:
        d = bar_date(b)
        if d is None:
            continue
        by_day.setdefault(d, []).append(b)

    closes: list[float] = []
    ranges: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    turnovers: list[float] = []
    volumes: list[float] = []
    for d in sorted(by_day):
        day_bars = sorted(by_day[d], key=lambda x: getattr(x, "timestamp", None))
        hi = max(float(getattr(x, "high", 0.0)) for x in day_bars)
        lo = min(float(getattr(x, "low", 0.0)) for x in day_bars)
        close = float(getattr(day_bars[-1], "close", 0.0))
        turn = sum(float(getattr(x, "close", 0.0)) * float(getattr(x, "volume", 0.0))
                   for x in day_bars)
        vol = sum(float(getattr(x, "volume", 0.0)) for x in day_bars)
        closes.append(close)
        highs.append(hi)
        lows.append(lo)
        ranges.append((hi - lo) / close if close > 0 else 0.0)
        turnovers.append(turn)
        volumes.append(vol)
    return {
        "closes": closes,
        "ranges": ranges,
        "highs": highs,
        "lows": lows,
        "turnovers": turnovers,
        "volumes": volumes,
    }
