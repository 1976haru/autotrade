"""REAL-DATA-STRATEGY-01 — OHLCV 데이터 품질 검증 (read-only).

백테스트/Walk-forward 입력 전, 실제/준실제 OHLCV bar 의 무결성과 표본 충분성을
점검한다. broker / 주문 API 와 무관 — 데이터 검증 전용.

품질 등급:
- FAIL: 데이터 없음 / 잘못된 OHLC(high<low 등) / 음수 가격 / 표본 심각 부족.
- WARN: 중복 timestamp / 음수 거래량 / 최소 bar·거래일 미달 (백테스트는 가능하나 표본 부족).
- OK: 위 문제 없음.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

DEFAULT_MIN_BARS = 100
DEFAULT_MIN_DAYS = 28
DEFAULT_MIN_SYMBOLS = 1


@dataclass(frozen=True)
class OhlcvQualityReport:
    status: str
    bar_count: int
    day_count: int
    symbol_count: int
    issues: dict[str, int] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    sufficient_for_backtest: bool = False    # bar 가 있고 OHLC 무결 (표본 부족은 WARN)
    meets_min_sample: bool = False           # min_bars + min_days + min_symbols 충족
    contains_secret: bool = False            # 항상 False (OHLCV 는 secret 아님)

    def __post_init__(self) -> None:
        if self.contains_secret:
            raise ValueError("OhlcvQualityReport.contains_secret must be False")


def _is_invalid_ohlc(b: Any) -> bool:
    o = float(getattr(b, "open", 0) or 0)
    h = float(getattr(b, "high", 0) or 0)
    lo = float(getattr(b, "low", 0) or 0)
    c = float(getattr(b, "close", 0) or 0)
    if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
        return True
    return h < lo or h < o or h < c or lo > o or lo > c


def sanitize_ohlcv_bars(
    bars: list[Any], *, max_drop_ratio: float = 0.05,
) -> tuple[list[Any], int, bool]:
    """구조적으로 잘못된(OHLC 무결성 위반) row 를 *제거* 한다 (데이터 hygiene).

    **값을 보정/조작하지 않는다** — 잘못된 row 를 *드롭* 만 한다 (task §5 "일부 결측 row
    제거" → WARN 정책과 일치). 드롭 비율이 `max_drop_ratio` 초과 시 데이터가 구조적으로
    손상된 것으로 보고 `too_many_bad=True` 반환(호출자가 FAIL 유지) — 억지 통과 방지.

    비거래일(주말) 필터는 적용하지 않는다 — yfinance timestamp 가 UTC 라 KST 거래일이
    UTC 주말로 보일 수 있어(예: 월요일 09:00 KST = 일요일 15:00 UTC) 오탐 위험.

    Returns: (clean_bars, dropped_count, too_many_bad).
    """
    if not bars:
        return [], 0, False
    clean: list[Any] = []
    dropped = 0
    for b in bars:
        if _is_invalid_ohlc(b):
            dropped += 1
            continue
        clean.append(b)
    too_many = (dropped / max(1, len(bars))) > max_drop_ratio
    return clean, dropped, too_many


def _to_date(ts: Any) -> Any:
    """OHLCVBar.timestamp(datetime) 또는 ISO str 에서 날짜 부분 추출."""
    if hasattr(ts, "date"):
        try:
            return ts.date()
        except Exception:  # noqa: BLE001
            return ts
    s = str(ts)
    return s[:10]


def check_ohlcv_quality(
    bars: list[Any],
    *,
    min_bars: int = DEFAULT_MIN_BARS,
    min_days: int = DEFAULT_MIN_DAYS,
    min_symbols: int = DEFAULT_MIN_SYMBOLS,
) -> OhlcvQualityReport:
    """OHLCVBar(또는 동등 속성 객체) 리스트의 품질을 평가한다.

    bar 는 .symbol / .timestamp / .open / .high / .low / .close / .volume 속성을
    갖는다고 가정 (strategy_council_backtest.OHLCVBar 호환).
    """
    issues: dict[str, int] = {}
    reasons: list[str] = []

    if not bars:
        return OhlcvQualityReport(
            status=FAIL, bar_count=0, day_count=0, symbol_count=0,
            issues={"no_data": 1}, reasons=("OHLCV 데이터가 없습니다",),
            sufficient_for_backtest=False, meets_min_sample=False)

    seen: set[tuple[str, Any]] = set()
    dates: set[Any] = set()
    symbols: set[str] = set()
    bad_ohlc = 0
    nonpositive = 0
    negative_volume = 0
    duplicate = 0

    for b in bars:
        sym = getattr(b, "symbol", "") or ""
        symbols.add(sym)
        ts = getattr(b, "timestamp", None)
        key = (sym, ts)
        if key in seen:
            duplicate += 1
        else:
            seen.add(key)
        dates.add(_to_date(ts))

        o = float(getattr(b, "open", 0) or 0)
        h = float(getattr(b, "high", 0) or 0)
        lo = float(getattr(b, "low", 0) or 0)
        c = float(getattr(b, "close", 0) or 0)
        v = float(getattr(b, "volume", 0) or 0)
        if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
            nonpositive += 1
        if h < lo or h < o or h < c or lo > o or lo > c:
            bad_ohlc += 1
        if v < 0:
            negative_volume += 1

    if duplicate:
        issues["duplicate_timestamp"] = duplicate
    if bad_ohlc:
        issues["bad_ohlc"] = bad_ohlc
    if nonpositive:
        issues["nonpositive_price"] = nonpositive
    if negative_volume:
        issues["negative_volume"] = negative_volume

    bar_count = len(bars)
    day_count = len(dates)
    symbol_count = len(symbols)

    # FAIL 조건: 무결성 위반.
    if bad_ohlc:
        reasons.append(f"잘못된 OHLC {bad_ohlc}건 (high<low 또는 high<open/close 등)")
    if nonpositive:
        reasons.append(f"0 이하 가격 {nonpositive}건")
    fatal = bool(bad_ohlc or nonpositive)

    # WARN 조건: 표본/중복.
    if duplicate:
        reasons.append(f"중복 timestamp {duplicate}건")
    if negative_volume:
        reasons.append(f"음수 거래량 {negative_volume}건")
        fatal = True  # 음수 거래량은 데이터 오류 → FAIL
        issues["negative_volume"] = negative_volume

    insufficient = False
    if bar_count < min_bars:
        reasons.append(f"bar {bar_count} < 최소 {min_bars}")
        insufficient = True
    if day_count < min_days:
        reasons.append(f"거래일 {day_count} < 최소 {min_days}")
        insufficient = True
    if symbol_count < min_symbols:
        reasons.append(f"종목 {symbol_count} < 최소 {min_symbols}")
        insufficient = True

    if fatal:
        status = FAIL
    elif duplicate or insufficient:
        status = WARN
    else:
        status = OK

    return OhlcvQualityReport(
        status=status,
        bar_count=bar_count,
        day_count=day_count,
        symbol_count=symbol_count,
        issues=issues,
        reasons=tuple(reasons),
        sufficient_for_backtest=(not fatal and bar_count > 0),
        meets_min_sample=(not fatal and not insufficient),
    )


def to_dict(report: OhlcvQualityReport) -> dict[str, Any]:
    return {
        "status": report.status,
        "bar_count": report.bar_count,
        "day_count": report.day_count,
        "symbol_count": report.symbol_count,
        "issues": report.issues,
        "reasons": list(report.reasons),
        "sufficient_for_backtest": report.sufficient_for_backtest,
        "meets_min_sample": report.meets_min_sample,
        "contains_secret": report.contains_secret,
    }
