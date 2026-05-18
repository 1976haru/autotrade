"""Data Quality 검사기 테스트 (#21).

영역:
- 정상 데이터 GOOD 등급
- 빈 데이터 / weekend / weekday-empty 분기
- expected/actual/missing rate 계산
- 중복, OHLC 이상, 음수/0 가격, volume 음수/spike/zero streak
- 장외 데이터 (정규장 외) — include_out_of_session 토글
- fetched_at 누락/미래/오래됨
- 점수→등급 (GOOD/WARNING/POOR/EXCLUDE/EMPTY)
- to_dict / summarize_reports
- CLI 스크립트 smoke (--format json)

CI 정책: 외부 네트워크 호출 0건. 모든 시간은 명시 (`now=...`).
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import MarketBar
from app.market.data_quality import (
    DailyQualityReport,
    evaluate_day,
    evaluate_range,
    fetch_bars_for_day,
    grade_for_score,
    summarize_reports,
)


_KST = timezone(timedelta(hours=9))
_DAY = date(2026, 5, 18)  # 월요일 (정규장 운영일)
_REF_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _utc_at(hour_kst: int, minute_kst: int) -> datetime:
    return datetime(_DAY.year, _DAY.month, _DAY.day,
                    hour_kst, minute_kst, tzinfo=_KST).astimezone(timezone.utc)


_DEFAULT_FETCHED = object()


def _bar(*, ts, o=100, h=110, low=90, c=105, v=1000,
         fetched_at=_DEFAULT_FETCHED, symbol="005930", interval="1m"):
    """fetched_at=None을 명시적으로 허용. 미지정 시 기본값(now-30s)."""
    if fetched_at is _DEFAULT_FETCHED:
        fetched_at = _REF_NOW - timedelta(seconds=30)
    return MarketBar(
        symbol=symbol, interval=interval, timestamp=ts,
        open=o, high=h, low=low, close=c, volume=v,
        fetched_at=fetched_at,
    )


def _session_factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------- grade boundaries ----------


def test_grade_good_above_90():
    assert grade_for_score(95.0, has_data=True) == "GOOD"


def test_grade_warning_75_to_89():
    assert grade_for_score(80.0, has_data=True) == "WARNING"


def test_grade_poor_60_to_74():
    assert grade_for_score(65.0, has_data=True) == "POOR"


def test_grade_exclude_below_60():
    assert grade_for_score(40.0, has_data=True) == "EXCLUDE"


def test_grade_empty_when_no_data():
    assert grade_for_score(0.0, has_data=False) == "EMPTY"


# ---------- empty data ----------


def test_evaluate_day_empty_weekday_returns_zero_score():
    rep = evaluate_day(
        [], symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.grade == "EMPTY"
    assert rep.score == 0.0
    assert rep.issues.actual_count == 0
    assert rep.issues.missing_count == rep.issues.expected_count
    assert rep.missing_rate == 1.0


def test_evaluate_day_empty_weekend_is_normal():
    weekend = date(2026, 5, 16)  # 토요일
    rep = evaluate_day(
        [], symbol="005930", interval="1m", report_date=weekend,
        now=_REF_NOW,
    )
    assert rep.grade == "EMPTY"
    assert rep.issues.expected_count == 0
    assert rep.score == 100.0
    assert rep.missing_rate is None


# ---------- normal/GOOD case ----------


def test_evaluate_day_full_session_minute_bars_is_good():
    """09:00–15:30 KST 사이 391개 1분봉 — 결측/이상 없음."""
    bars = []
    minutes = 0
    cur = _utc_at(9, 0)
    end = _utc_at(15, 30)
    while cur <= end:
        bars.append(_bar(ts=cur, o=100, h=101, low=99, c=100, v=1000))
        cur += timedelta(minutes=1)
        minutes += 1

    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.grade == "GOOD"
    assert rep.score >= 90
    assert rep.issues.actual_count == 391  # 09:00..15:30 inclusive
    assert rep.missing_rate == 0.0
    assert rep.coverage_score == 100.0


# ---------- missing ----------


def test_missing_bars_reduces_coverage_score():
    """절반 결측 → coverage_score≈50, missing_rate≈0.5."""
    bars = []
    cur = _utc_at(9, 0)
    for _ in range(196):  # 정확히 절반 약간 더
        bars.append(_bar(ts=cur))
        cur += timedelta(minutes=1)

    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.missing_rate is not None
    assert 0.4 < rep.missing_rate < 0.6
    assert rep.score < 90  # 누락 감점으로 GOOD 아님


# ---------- duplicates ----------


def test_duplicate_timestamps_are_counted():
    ts = _utc_at(9, 0)
    bars = [_bar(ts=ts), _bar(ts=ts), _bar(ts=ts + timedelta(minutes=1))]

    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.duplicate_count == 1


# ---------- OHLC anomaly ----------


def test_ohlc_high_below_low_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), o=100, h=80, low=90, c=85)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.ohlc_invalid_count == 1


def test_ohlc_open_outside_range_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), o=200, h=110, low=90, c=100)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.ohlc_invalid_count == 1


def test_ohlc_close_outside_range_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), o=100, h=110, low=90, c=200)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.ohlc_invalid_count == 1


def test_negative_price_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), o=-1, h=110, low=-5, c=100)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.nonpositive_price_count == 1


def test_zero_price_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), o=0, h=0, low=0, c=0)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.nonpositive_price_count == 1


def test_extreme_returns_detected():
    """직전 close 100 → 다음 close 200(100% 변화)이면 EXTREME_RETURN_PCT(30) 초과."""
    bars = [
        _bar(ts=_utc_at(9, 0), o=100, h=101, low=99, c=100),
        _bar(ts=_utc_at(9, 1), o=100, h=210, low=100, c=200),
    ]
    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.extreme_return_count == 1


# ---------- volume ----------


def test_negative_volume_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), v=-100)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.negative_volume_count == 1


def test_volume_spike_detected():
    """20개 평균=100, 다음이 5000 → spike."""
    bars = [_bar(ts=_utc_at(9, 0) + timedelta(minutes=i), v=100)
            for i in range(20)]
    bars.append(_bar(ts=_utc_at(9, 20), v=5000))

    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.volume_spike_count >= 1


def test_zero_volume_streak_recorded():
    bars = [
        _bar(ts=_utc_at(9, i), v=(0 if i < 8 else 1000))
        for i in range(10)
    ]
    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.zero_volume_streak_max == 8


# ---------- out-of-session ----------


def test_out_of_session_counted_when_not_included():
    """08:30(장전)과 16:00(장후) — 정규장 밖."""
    bars = [
        _bar(ts=_utc_at(8, 30)),
        _bar(ts=_utc_at(9, 0)),
        _bar(ts=_utc_at(16, 0)),
    ]
    rep = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW, include_out_of_session=False,
    )
    assert rep.issues.out_of_session_count == 2
    # 감점 발생.
    assert any("out_of_session" in n for n in rep.notes)


def test_include_out_of_session_skips_score_penalty():
    bars = [
        _bar(ts=_utc_at(8, 30)),
        _bar(ts=_utc_at(9, 0)),
    ]
    rep_strict  = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW, include_out_of_session=False,
    )
    rep_lenient = evaluate_day(
        bars, symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW, include_out_of_session=True,
    )
    # 카운트는 동일, 점수만 다름.
    assert rep_strict.issues.out_of_session_count == rep_lenient.issues.out_of_session_count
    assert rep_lenient.score >= rep_strict.score


# ---------- fetched_at ----------


def test_fetched_at_missing_detected():
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), fetched_at=None)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.fetched_at_missing_count == 1


def test_fetched_at_future_detected():
    future = _REF_NOW + timedelta(hours=1)
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), fetched_at=future)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.fetched_at_future_count == 1


def test_fetched_at_too_old_detected():
    long_ago = _REF_NOW - timedelta(days=30)
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0), fetched_at=long_ago)],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    assert rep.issues.fetched_at_stale_count == 1


# ---------- output / serialization ----------


def test_to_dict_is_json_serializable():
    import json
    rep = evaluate_day(
        [_bar(ts=_utc_at(9, 0))],
        symbol="005930", interval="1m", report_date=_DAY,
        now=_REF_NOW,
    )
    payload = rep.to_dict()
    s = json.dumps(payload, ensure_ascii=False)
    assert "005930" in s
    assert "score" in payload
    assert payload["grade"] in ("GOOD", "WARNING", "POOR", "EXCLUDE", "EMPTY")


def test_summarize_reports_aggregates():
    reps = [
        DailyQualityReport(symbol="005930", interval="1m",
                           report_date=_DAY,
                           issues=__import__("app.market.data_quality",
                                             fromlist=["IssueCounts"]).IssueCounts(),
                           score=95.0, grade="GOOD",
                           missing_rate=0.0, coverage_score=100.0),
        DailyQualityReport(symbol="005930", interval="1m",
                           report_date=_DAY + timedelta(days=1),
                           issues=__import__("app.market.data_quality",
                                             fromlist=["IssueCounts"]).IssueCounts(),
                           score=50.0, grade="EXCLUDE",
                           missing_rate=0.5, coverage_score=50.0),
    ]
    summary = summarize_reports(reps)
    assert summary["report_count"] == 2
    assert summary["by_grade"] == {"GOOD": 1, "EXCLUDE": 1}
    assert summary["avg_score"] == 72.5


# ---------- DB integration ----------


def test_fetch_bars_for_day_filters_by_kst_window():
    Session = _session_factory()
    with Session() as db:
        # 5/18 KST 24h 안과 그 이전 / 그 이후.
        for hour, expected_in in [(8, True),  # 5/18 08:00 KST → 5/17 23:00 UTC
                                  (12, True),
                                  (23, True)]:
            db.add(_bar(ts=_utc_at(hour, 0)))
        # 5/19 KST 0시 (다음 날) — 범위 밖.
        next_day = datetime(2026, 5, 19, 0, 0, tzinfo=_KST).astimezone(timezone.utc)
        db.add(_bar(ts=next_day))
        db.commit()

        rows = fetch_bars_for_day(
            db, symbol="005930", interval="1m", report_date=_DAY,
        )
    assert len(rows) == 3


def test_evaluate_range_iterates_days():
    Session = _session_factory()
    with Session() as db:
        # 3일치 한 봉씩.
        for d_offset in range(3):
            d = _DAY + timedelta(days=d_offset)
            ts = datetime(d.year, d.month, d.day, 9, 0, tzinfo=_KST).astimezone(timezone.utc)
            db.add(_bar(ts=ts))
        db.commit()

        reps = evaluate_range(
            db, symbol="005930", interval="1m",
            start_date=_DAY, end_date=_DAY + timedelta(days=2),
            now=_REF_NOW,
        )
    assert len(reps) == 3
    assert all(r.issues.actual_count == 1 for r in reps)


# ---------- CLI smoke ----------


def test_cli_runs_with_format_json(tmp_path, monkeypatch):
    """scripts/check_data_quality.py가 --format json + --output으로 정상 실행."""
    # 본 테스트는 default DATABASE_URL(sqlite:///./data/auto_trader.db)가 비어 있어도
    # SessionLocal이 빈 결과를 반환해 evaluate가 EMPTY report를 만들도록 한다.
    #
    # fix/ci-data-quality-cross-platform-cwd: 이전 cwd 가
    # "C:/trade/autotrade" 로 hardcoded 되어 있어 Linux CI / 다른 머신에서
    # FileNotFoundError. `__file__` 기준 repo root 를 계산해 OS 독립.
    import json as _json
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root  = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "check_data_quality.py"

    out_path = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(script_path),
         "--symbol", "005930", "--interval", "1m",
         "--date", "2026-05-18",
         "--format", "json",
         "--output", str(out_path)],
        cwd=str(repo_root),
        capture_output=True, text=True, timeout=30,
        env={**os.environ,
             "DATABASE_URL": f"sqlite:///{tmp_path}/empty.db"},
    )
    assert proc.returncode == 0, proc.stderr
    assert out_path.exists()

    payload = _json.loads(out_path.read_text(encoding="utf-8"))
    assert "reports" in payload
    assert "summary" in payload
    assert payload["reports"][0]["symbol"] == "005930"
