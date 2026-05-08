"""candle_builder 순수 함수 테스트 (#19).

검증:
- OHLCV invariant 위반 즉시 raise
- 빈 입력 → 빈 결과
- timestamp 오름차순 정렬
- timezone naive → UTC로 처리
- 1m → 5m 집계 (open/high/low/close/volume + bucket boundary)
- mixed-symbol 거부 + partition_by_symbol
- 누락률 / coverage 계산 (분모 0 / 음수 / clamp)
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar
from app.market.candle_builder import (
    CandleValidationError,
    aggregate_1m_to_5m,
    assert_single_symbol,
    compute_missing_rate,
    deduplicate_bars,
    expected_bar_count,
    interval_to_seconds,
    partition_by_symbol,
    sort_bars,
    validate_bar,
    validate_bars,
)


def _bar(symbol="005930", ts=None, o=100, h=110, low=90, c=105, v=1000):
    return Bar(symbol=symbol,
               timestamp=ts or datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
               open=o, high=h, low=low, close=c, volume=v)


# ---------- validate_bar ----------


def test_validate_bar_accepts_normal():
    validate_bar(_bar())


def test_validate_bar_rejects_high_below_low():
    with pytest.raises(CandleValidationError, match="high < low"):
        validate_bar(_bar(h=80, low=90))


def test_validate_bar_rejects_open_outside_range():
    with pytest.raises(CandleValidationError, match="open out of"):
        validate_bar(_bar(o=200, h=110, low=90))


def test_validate_bar_rejects_close_outside_range():
    with pytest.raises(CandleValidationError, match="close out of"):
        validate_bar(_bar(c=200, h=110, low=90))


def test_validate_bar_rejects_negative_volume():
    with pytest.raises(CandleValidationError, match="volume"):
        validate_bar(_bar(v=-1))


def test_validate_bars_empty_returns_empty():
    assert validate_bars([]) == []


def test_validate_bars_passes_through_valid():
    bars = [_bar(), _bar(ts=datetime(2026, 5, 18, 9, 1, tzinfo=timezone.utc))]
    out = validate_bars(bars)
    assert out is bars  # 식별성 유지


# ---------- sort_bars ----------


def test_sort_bars_empty():
    assert sort_bars([]) == []


def test_sort_bars_orders_by_timestamp():
    a = _bar(ts=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc))
    b = _bar(ts=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc))
    c = _bar(ts=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc))
    out = sort_bars([a, b, c])
    assert [x.timestamp.hour for x in out] == [9, 10, 11]


def test_sort_bars_treats_naive_as_utc():
    naive = _bar(ts=datetime(2026, 5, 18, 9, 0))            # naive → UTC
    aware = _bar(ts=datetime(2026, 5, 18, 9, 30, tzinfo=timezone.utc))
    out = sort_bars([aware, naive])
    assert out[0].timestamp.minute == 0
    assert out[1].timestamp.minute == 30


# ---------- deduplicate ----------


def test_deduplicate_keeps_last_per_timestamp():
    ts = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    a = _bar(ts=ts, c=100)
    b = _bar(ts=ts, c=999)  # 같은 (symbol, ts) — b가 win
    out = deduplicate_bars([a, b])
    assert len(out) == 1
    assert out[0].close == 999


# ---------- single-symbol / partition ----------


def test_assert_single_symbol_empty_returns_none():
    assert assert_single_symbol([]) is None


def test_assert_single_symbol_uniform_returns_symbol():
    assert assert_single_symbol([_bar(symbol="005930"), _bar(symbol="005930")]) == "005930"


def test_assert_single_symbol_mixed_raises():
    with pytest.raises(CandleValidationError, match="mixed-symbol"):
        assert_single_symbol([_bar(symbol="005930"), _bar(symbol="000660")])


def test_partition_by_symbol_splits():
    bars = [_bar(symbol="005930"), _bar(symbol="000660"), _bar(symbol="005930")]
    out = partition_by_symbol(bars)
    assert set(out.keys()) == {"005930", "000660"}
    assert len(out["005930"]) == 2
    assert len(out["000660"]) == 1


def test_partition_by_symbol_empty():
    assert partition_by_symbol([]) == {}


# ---------- 1m → 5m aggregation ----------


def _bar_min(minute, **kw):
    return _bar(ts=datetime(2026, 5, 18, 9, minute, tzinfo=timezone.utc), **kw)


def test_aggregate_empty_returns_empty():
    assert aggregate_1m_to_5m([]) == []


def test_aggregate_5_minutes_into_one_bucket():
    bars = [
        _bar_min(0, o=100, h=105, low=98,  c=102, v=10),
        _bar_min(1, o=102, h=108, low=101, c=107, v=20),
        _bar_min(2, o=107, h=110, low=104, c=106, v=15),
        _bar_min(3, o=106, h=109, low=103, c=104, v=12),
        _bar_min(4, o=104, h=106, low=100, c=103, v=18),
    ]
    out = aggregate_1m_to_5m(bars)
    assert len(out) == 1
    five = out[0]
    assert five.timestamp == datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    assert five.open  == 100  # first
    assert five.close == 103  # last
    assert five.high  == 110
    assert five.low   ==  98
    assert five.volume == 75


def test_aggregate_partial_bucket_still_emits_bar():
    """KRX 분봉 결측이 흔하므로, 5개 모두 채워지지 않아도 봉을 만든다."""
    bars = [_bar_min(0), _bar_min(2)]  # 1, 3, 4분 결측
    out = aggregate_1m_to_5m(bars)
    assert len(out) == 1


def test_aggregate_spans_multiple_buckets():
    bars = [_bar_min(0), _bar_min(4), _bar_min(5), _bar_min(9), _bar_min(10)]
    out = aggregate_1m_to_5m(bars)
    assert [b.timestamp.minute for b in out] == [0, 5, 10]


def test_aggregate_rejects_mixed_symbol():
    bars = [_bar_min(0, symbol="005930"), _bar_min(1, symbol="000660")]
    with pytest.raises(CandleValidationError):
        aggregate_1m_to_5m(bars)


def test_aggregate_handles_unsorted_input():
    bars = [_bar_min(4), _bar_min(0), _bar_min(2)]
    out = aggregate_1m_to_5m(bars)
    assert len(out) == 1


# ---------- Coverage / Missing Rate ----------


def test_interval_to_seconds_known():
    assert interval_to_seconds("1m") == 60
    assert interval_to_seconds("5m") == 300
    assert interval_to_seconds("1h") == 3600
    assert interval_to_seconds("1d") == 86400


def test_interval_to_seconds_unknown_raises():
    with pytest.raises(CandleValidationError):
        interval_to_seconds("3m")


def test_expected_bar_count_inclusive():
    """09:00 ~ 09:04 (5분, 1분 간격) → 5개."""
    start = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    end   = start + timedelta(minutes=4)
    assert expected_bar_count(start, end, "1m") == 5


def test_expected_bar_count_end_before_start_returns_zero():
    start = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    end   = start - timedelta(minutes=1)
    assert expected_bar_count(start, end, "1m") == 0


def test_compute_missing_rate_zero_expected_returns_none():
    out = compute_missing_rate(expected_count=0, actual_count=0)
    assert out["missing_rate"]   is None
    assert out["coverage_score"] is None


def test_compute_missing_rate_normal():
    out = compute_missing_rate(expected_count=10, actual_count=8)
    assert out["missing_count"]  == 2
    assert out["missing_rate"]   == pytest.approx(0.2)
    assert out["coverage_score"] == pytest.approx(80.0)


def test_compute_missing_rate_full_coverage():
    out = compute_missing_rate(expected_count=10, actual_count=10)
    assert out["missing_rate"]   == 0.0
    assert out["coverage_score"] == 100.0


def test_compute_missing_rate_zero_actual():
    out = compute_missing_rate(expected_count=10, actual_count=0)
    assert out["missing_rate"]   == 1.0
    assert out["coverage_score"] == 0.0


def test_compute_missing_rate_clamps_when_actual_exceeds_expected():
    """피드가 expected보다 더 많이 보낸 경우 — missing_count는 0, score는 100."""
    out = compute_missing_rate(expected_count=10, actual_count=12)
    assert out["missing_count"]  == 0
    assert out["missing_rate"]   == 0.0
    assert out["coverage_score"] == 100.0


def test_compute_missing_rate_negative_inputs_raise():
    with pytest.raises(CandleValidationError):
        compute_missing_rate(expected_count=-1, actual_count=0)
    with pytest.raises(CandleValidationError):
        compute_missing_rate(expected_count=10, actual_count=-1)
