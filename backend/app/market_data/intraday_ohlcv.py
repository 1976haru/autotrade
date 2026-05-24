"""INTRADAY-DATA-01 — 분봉(intraday) OHLCV 품질검증 + 로더 (read-only).

ORB / VWAP / Momentum / Gap / Agent Council 은 *장중 단타* 전략으로 **분봉** 데이터가
필요하다(일봉으로는 진입 조건이 트리거되지 않음 — REAL-DATA-INPUT-01 §0 참조). 본 모듈은
분봉 CSV 를 적재하고 *분봉 특화* 품질(시간프레임 / 장중 시간 / 일별 bar 수)을 검증한다.

기존 OHLC 무결성 검사(`ohlcv_quality._is_invalid_ohlc` / `sanitize_ohlcv_bars`)를 재사용한다.
본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import Any

from app.market_data.ohlcv_quality import _is_invalid_ohlc, sanitize_ohlcv_bars

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

_KST = timezone(timedelta(hours=9))

# 분봉 표본 기준 (단타 — 일봉보다 짧은 기간 허용).
DEFAULT_MIN_BARS = 100
DEFAULT_MIN_DAYS = 5
SESSION_START_MIN = 9 * 60          # 09:00 KST
SESSION_END_MIN = 15 * 60 + 30      # 15:30 KST
# 일중 평균 bar 수가 이 미만이면 분봉이 아니라 일봉으로 간주.
MIN_BARS_PER_DAY_FOR_INTRADAY = 5


@dataclass(frozen=True)
class IntradayQualityReport:
    status: str
    bar_count: int
    day_count: int
    bars_per_day: float
    bar_size_minutes: float | None
    intraday_detected: bool
    out_of_session: int = 0
    issues: dict[str, int] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    sufficient_for_backtest: bool = False
    meets_min_sample: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.contains_secret:
            raise ValueError("IntradayQualityReport.contains_secret must be False")


def _kst_minutes(ts: Any) -> int | None:
    try:
        kst = ts.astimezone(_KST)
        return kst.hour * 60 + kst.minute
    except Exception:  # noqa: BLE001
        return None


def _date_of(ts: Any) -> Any:
    try:
        return ts.astimezone(_KST).date()
    except Exception:  # noqa: BLE001
        return str(ts)[:10]


def _bar_size_minutes(bars: list[Any]) -> float | None:
    """같은 날 연속 bar 간 간격(분)의 중앙값."""
    deltas: list[float] = []
    prev = None
    prev_day = None
    for b in bars:
        ts = getattr(b, "timestamp", None)
        if ts is None or not hasattr(ts, "timestamp"):
            continue
        day = _date_of(ts)
        if prev is not None and day == prev_day:
            dt = (ts - prev).total_seconds() / 60.0
            if dt > 0:
                deltas.append(dt)
        prev, prev_day = ts, day
    if not deltas:
        return None
    return round(statistics.median(deltas), 2)


def check_intraday_quality(
    bars: list[Any],
    *,
    min_bars: int = DEFAULT_MIN_BARS,
    min_days: int = DEFAULT_MIN_DAYS,
) -> IntradayQualityReport:
    """분봉 OHLCV 품질검증. 일봉이거나 OHLC 손상이면 FAIL, 표본/세션 문제는 WARN."""
    issues: dict[str, int] = {}
    reasons: list[str] = []

    if not bars:
        return IntradayQualityReport(
            FAIL, 0, 0, 0.0, None, False, 0, {"no_data": 1},
            ("분봉 데이터가 없습니다",), False, False)

    bad_ohlc = sum(1 for b in bars if _is_invalid_ohlc(b))
    dup = 0
    seen: set[tuple[str, Any]] = set()
    out_of_session = 0
    dates: set[Any] = set()
    parse_fail = 0
    for b in bars:
        ts = getattr(b, "timestamp", None)
        if ts is None or not hasattr(ts, "timestamp"):
            parse_fail += 1
            continue
        key = (getattr(b, "symbol", ""), ts)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
        dates.add(_date_of(ts))
        m = _kst_minutes(ts)
        if m is not None and (m < SESSION_START_MIN or m > SESSION_END_MIN):
            out_of_session += 1

    bar_count = len(bars)
    day_count = len(dates)
    bars_per_day = round(bar_count / max(1, day_count), 2)
    bar_size = _bar_size_minutes(bars)
    intraday_detected = bars_per_day >= MIN_BARS_PER_DAY_FOR_INTRADAY

    if bad_ohlc:
        issues["bad_ohlc"] = bad_ohlc
        reasons.append(f"잘못된 OHLC {bad_ohlc}건")
    if parse_fail:
        issues["timestamp_parse_fail"] = parse_fail
        reasons.append(f"timestamp 파싱 불가 {parse_fail}건")
    if dup:
        issues["duplicate_timestamp"] = dup
        reasons.append(f"중복 timestamp {dup}건")
    if out_of_session:
        issues["out_of_session"] = out_of_session
        reasons.append(f"장중(09:00~15:30) 밖 bar {out_of_session}건")

    # FAIL: OHLC 손상 / 파싱 과다 / 분봉 아님(일봉).
    fatal = False
    if bad_ohlc:
        fatal = True
    if parse_fail > bar_count * 0.1:
        fatal = True
    if not intraday_detected:
        reasons.append(
            f"일중 평균 bar {bars_per_day} < {MIN_BARS_PER_DAY_FOR_INTRADAY} — "
            "분봉이 아니라 일봉으로 보임(단타 전략은 분봉 필요)")
        fatal = True

    insufficient = False
    if bar_count < min_bars:
        reasons.append(f"bar {bar_count} < 최소 {min_bars}")
        insufficient = True
    if day_count < min_days:
        reasons.append(f"거래일 {day_count} < 최소 {min_days}")
        insufficient = True

    if fatal:
        status = FAIL
    elif dup or out_of_session or insufficient:
        status = WARN
    else:
        status = OK

    return IntradayQualityReport(
        status=status, bar_count=bar_count, day_count=day_count,
        bars_per_day=bars_per_day, bar_size_minutes=bar_size,
        intraday_detected=intraday_detected, out_of_session=out_of_session,
        issues=issues, reasons=tuple(reasons),
        sufficient_for_backtest=(not fatal and bar_count > 0 and intraday_detected),
        meets_min_sample=(not fatal and not insufficient))


def load_intraday_csv(path: str) -> tuple[list[Any], dict[str, Any]]:
    """분봉 CSV 적재 → (bars, meta). 기존 OHLCV 로더 재사용."""
    from app.market_data.real_ohlcv_loader import load_from_csv
    lo = load_from_csv(path)
    return list(lo.bars), {
        "data_source": lo.data_source,
        "real_data_used": lo.real_data_used,
        "sample_fixture_only": lo.sample_fixture_only,
        "symbols": list(lo.symbols),
    }


def quality_to_dict(r: IntradayQualityReport) -> dict[str, Any]:
    return {
        "status": r.status, "bar_count": r.bar_count, "day_count": r.day_count,
        "bars_per_day": r.bars_per_day, "bar_size_minutes": r.bar_size_minutes,
        "intraday_detected": r.intraday_detected, "out_of_session": r.out_of_session,
        "issues": r.issues, "reasons": list(r.reasons),
        "sufficient_for_backtest": r.sufficient_for_backtest,
        "meets_min_sample": r.meets_min_sample, "contains_secret": r.contains_secret,
    }


# sanitize 는 일봉과 동일 (OHLC 무결성 위반 row 드롭). intraday 재노출.
__all__ = [
    "IntradayQualityReport", "check_intraday_quality", "load_intraday_csv",
    "quality_to_dict", "sanitize_ohlcv_bars",
]
