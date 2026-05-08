"""Candle/OHLCV 검증 및 변환 유틸 (#19).

순수 함수 모음 — broker / DB / network 의존성 없음. 모든 함수는:
- 빈 입력 → 빈 결과 (`[]`, 0)
- timezone-naive datetime은 UTC 가정
- OHLCV 무결성 위반은 즉시 ValueError (안전 측 거부)

candle 시간 정렬 / 집계는 전략 신호와 직결되므로 본 모듈의 검증을 우회하면
잘못된 봉이 strategy로 흘러갈 수 있다. 본 모듈을 통과한 봉은 invariant:
  high >= max(open, close, low), low <= min(open, close, high), volume >= 0

CLAUDE.md 절대 원칙 — 본 모듈은 broker API를 호출하지 않으며, RiskManager /
PermissionGate / OrderExecutor 분기와 분리되어 있다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtest.types import Bar


class CandleValidationError(ValueError):
    """OHLCV 또는 timestamp 무결성 위반."""


# 본 프로젝트가 다루는 단일 종목 단위. 다중 symbol 입력은 의도적으로 거부 —
# 호출자는 partition_by_symbol로 먼저 나눈 뒤 단일 symbol씩 처리해야 한다.
def assert_single_symbol(bars: list[Bar]) -> str | None:
    """모든 bar의 symbol이 같은지 확인. 빈 리스트면 None 반환."""
    if not bars:
        return None
    sym = bars[0].symbol
    for b in bars[1:]:
        if b.symbol != sym:
            raise CandleValidationError(
                f"mixed-symbol bars not supported here ({sym!r} vs {b.symbol!r}); "
                "call partition_by_symbol first"
            )
    return sym


def partition_by_symbol(bars: list[Bar]) -> dict[str, list[Bar]]:
    """다중 symbol 봉을 symbol별로 분리. 입력이 빈 리스트면 빈 dict."""
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.symbol, []).append(b)
    return out


def _ensure_utc(ts: datetime) -> datetime:
    """naive datetime은 UTC로 가정. tz-aware은 UTC로 변환."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def validate_bar(bar: Bar) -> None:
    """단일 bar의 OHLCV invariant 검증."""
    if bar.symbol is None or bar.symbol == "":
        raise CandleValidationError("bar.symbol is required")
    if bar.timestamp is None:
        raise CandleValidationError("bar.timestamp is required")
    if bar.volume < 0:
        raise CandleValidationError(
            f"bar volume must be non-negative; got {bar.volume} for {bar.symbol} @ {bar.timestamp}"
        )
    if bar.high < bar.low:
        raise CandleValidationError(
            f"bar high < low ({bar.high} < {bar.low}) for {bar.symbol} @ {bar.timestamp}"
        )
    if not (bar.low <= bar.open <= bar.high):
        raise CandleValidationError(
            f"bar open out of [low, high] ({bar.open} not in [{bar.low}, {bar.high}]) "
            f"for {bar.symbol} @ {bar.timestamp}"
        )
    if not (bar.low <= bar.close <= bar.high):
        raise CandleValidationError(
            f"bar close out of [low, high] ({bar.close} not in [{bar.low}, {bar.high}]) "
            f"for {bar.symbol} @ {bar.timestamp}"
        )


def validate_bars(bars: list[Bar]) -> list[Bar]:
    """모든 bar의 OHLCV invariant 검증. 통과한 원본 리스트를 그대로 반환 (식별성 유지)."""
    for b in bars:
        validate_bar(b)
    return bars


def sort_bars(bars: list[Bar]) -> list[Bar]:
    """timestamp 오름차순으로 새 리스트 반환. 입력 보존 (frozen dataclass라 어차피 안전)."""
    return sorted(bars, key=lambda b: _ensure_utc(b.timestamp))


def deduplicate_bars(bars: list[Bar]) -> list[Bar]:
    """같은 (symbol, timestamp) 쌍이 여러 번 들어오면 마지막 것만 유지.

    BarCache.save가 이미 같은 키 행을 delete-then-insert 패턴으로 덮어쓰지만,
    한 호출 안에서도 중복 timestamp가 들어오면 candle_builder 단에서 한 번
    정리한다 — 후속 집계가 잘못 동작하지 않도록.
    """
    seen: dict[tuple[str, datetime], Bar] = {}
    for b in bars:
        seen[(b.symbol, _ensure_utc(b.timestamp))] = b
    return sort_bars(list(seen.values()))


# ---------- 1m → 5m 집계 ----------


_FIVE_MIN = timedelta(minutes=5)


def _five_min_bucket_start(ts: datetime) -> datetime:
    """timestamp가 속한 5분 버킷의 시작 시각 (UTC, 분 단위 floor)."""
    ts = _ensure_utc(ts)
    minute = (ts.minute // 5) * 5
    return ts.replace(minute=minute, second=0, microsecond=0)


def aggregate_1m_to_5m(bars_1m: list[Bar]) -> list[Bar]:
    """단일 symbol의 1분봉 → 5분봉 집계.

    - open: 첫 1분봉의 open
    - high: max
    - low:  min
    - close: 마지막 1분봉의 close
    - volume: sum
    - timestamp: 5분 버킷 시작 (UTC)

    버킷 안에 1분봉이 1개 이상 있으면 봉을 만든다 — 5개 모두 있어야 한다는
    가정은 KRX 분봉 데이터에서 결측 행이 흔해 비현실적. 결측은
    `compute_missing_rate`로 별도 표면화한다.
    """
    if not bars_1m:
        return []
    symbol = assert_single_symbol(bars_1m)
    bars_1m = sort_bars(bars_1m)

    buckets: dict[datetime, list[Bar]] = {}
    for b in bars_1m:
        bucket = _five_min_bucket_start(b.timestamp)
        buckets.setdefault(bucket, []).append(b)

    out: list[Bar] = []
    for bucket_start in sorted(buckets.keys()):
        group = buckets[bucket_start]
        # group은 이미 sort_bars 순서를 유지 (Python sort는 stable이고 dict 삽입
        # 순서가 timestamp 순서이므로). 명시적 재정렬은 비용만 추가.
        out.append(Bar(
            symbol=symbol,
            timestamp=bucket_start,
            open=group[0].open,
            high=max(g.high for g in group),
            low=min(g.low for g in group),
            close=group[-1].close,
            volume=sum(g.volume for g in group),
        ))
    return out


# ---------- Coverage / Missing Rate ----------


_INTERVAL_SECONDS: dict[str, int] = {
    "1m":  60,
    "5m":  300,
    "1h":  3600,
    "1d":  86400,
}


def interval_to_seconds(interval: str) -> int:
    """알려진 interval → 초. 미지원 interval은 ValueError."""
    if interval not in _INTERVAL_SECONDS:
        raise CandleValidationError(f"unsupported interval for coverage: {interval!r}")
    return _INTERVAL_SECONDS[interval]


def expected_bar_count(start: datetime, end: datetime, interval: str) -> int:
    """단순 시간 범위 기반 예상 봉 수.

    KRX 정규장(09:00–15:30 KST 평일)이나 휴장일을 반영하지 않는다 — 본 단순
    추정은 데이터 피드 정상성의 *상한*이며, 정확한 calendar 반영은 후속 작업
    (TBD: KRX calendar 통합).

    end < start이거나 interval 단위가 0이면 0.
    """
    if end < start:
        return 0
    seconds = interval_to_seconds(interval)
    span = (end - start).total_seconds()
    if span < 0 or seconds <= 0:
        return 0
    # +1 — start와 end가 모두 포함된 봉 경계라고 가정 (inclusive).
    return int(span // seconds) + 1


def compute_missing_rate(
    *,
    expected_count: int,
    actual_count:   int,
) -> dict:
    """누락률 / coverage 점수 계산.

    expected_count == 0: missing_rate=None, coverage_score=None — 분모 0이라
                       통계적 의미가 없으므로 None으로 명시. 호출자가 표시 분기.
    actual > expected: missing_count는 0으로 clamp (피드가 더 많은 봉을 보낸
                     경우 — 실패가 아니라 expected 추정이 보수적인 경우).
    """
    if expected_count < 0 or actual_count < 0:
        raise CandleValidationError("expected_count and actual_count must be non-negative")
    if expected_count == 0:
        return {
            "expected_count":  0,
            "actual_count":    actual_count,
            "missing_count":   0,
            "missing_rate":    None,
            "coverage_score":  None,
        }
    missing = max(0, expected_count - actual_count)
    rate = missing / expected_count
    score = max(0.0, min(100.0, 100.0 * (1.0 - rate)))
    return {
        "expected_count":  expected_count,
        "actual_count":    actual_count,
        "missing_count":   missing,
        "missing_rate":    rate,
        "coverage_score":  score,
    }
