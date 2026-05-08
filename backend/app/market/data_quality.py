"""MarketBar 데이터 품질 검사 (#21).

`scripts/check_data_quality.py` CLI에서 호출하는 순수 로직 모듈. 누락 / 중복 /
OHLC 이상 / volume 이상 / 장외 / fetched_at 이상을 한 번에 평가하고 일별
품질 점수를 산출한다.

본 모듈은:
- broker / RiskManager / PermissionGate / OrderExecutor를 import하지 않는다.
- 외부 네트워크를 호출하지 않는다 — DB MarketBar 행만 walk.
- 기존 candle_builder의 `expected_bar_count`를 재사용 — 중복 분기 0.

품질 점수 등급 (CLAUDE.md / docs/data_quality_report.md와 lockstep):
- GOOD     ≥ 90
- WARNING  75–89
- POOR     60–74
- EXCLUDE  < 60   (백테스트 / 승격 평가 제외)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketBar
from app.market.candle_builder import interval_to_seconds


# ---------- 정책 상수 ----------

# 점수 임계값. 호출자 분류는 `grade_for_score` 사용.
GRADE_GOOD     = 90
GRADE_WARNING  = 75
GRADE_POOR     = 60

# KRX 정규장 (KST). 장전/장후 단일가는 미지원 — 본 모듈은 정규장만 in-session으로 분류.
_KST = timezone(timedelta(hours=9))
SESSION_OPEN_KST  = time(9, 0)
SESSION_CLOSE_KST = time(15, 30)

# Volume spike 감지: 직전 N개 평균의 K배 초과.
VOLUME_SPIKE_LOOKBACK = 20
VOLUME_SPIKE_RATIO    = 10.0

# 가격 급등락 감지: 인접 봉 close 변화율 임계 (%). 정규장 기준 보수적 추정.
EXTREME_RETURN_PCT = 30.0

# fetched_at 정상 범위.
FETCHED_AT_FUTURE_TOLERANCE_SECONDS = 60        # 1분 이내 미래는 clock skew 허용
FETCHED_AT_TOO_OLD_DAYS              = 7         # 7일 초과면 stale로 분류


# ---------- DTO ----------


@dataclass(frozen=True)
class IssueCounts:
    """검사 항목별 카운트 — 점수 산출 + reporting 모두에 사용."""
    expected_count:               int = 0
    actual_count:                 int = 0
    missing_count:                int = 0
    duplicate_count:              int = 0
    ohlc_invalid_count:           int = 0
    nonpositive_price_count:      int = 0
    extreme_return_count:         int = 0
    negative_volume_count:        int = 0
    volume_spike_count:           int = 0
    zero_volume_streak_max:       int = 0
    out_of_session_count:         int = 0
    fetched_at_missing_count:     int = 0
    fetched_at_future_count:      int = 0
    fetched_at_stale_count:       int = 0


@dataclass
class DailyQualityReport:
    """단일 (symbol, interval, date)의 품질 리포트."""
    symbol:           str
    interval:         str
    report_date:      date
    issues:           IssueCounts
    score:            float          # 0~100
    grade:            str            # GOOD / WARNING / POOR / EXCLUDE / EMPTY
    missing_rate:     float | None
    coverage_score:   float | None
    notes:            list[str] = field(default_factory=list)
    include_out_of_session: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "interval":        self.interval,
            "date":            self.report_date.isoformat(),
            "score":           round(self.score, 2),
            "grade":           self.grade,
            "missing_rate":    self.missing_rate,
            "coverage_score":  (
                round(self.coverage_score, 2) if self.coverage_score is not None else None
            ),
            "include_out_of_session": self.include_out_of_session,
            "issues": {
                "expected_count":             self.issues.expected_count,
                "actual_count":               self.issues.actual_count,
                "missing_count":              self.issues.missing_count,
                "duplicate_count":            self.issues.duplicate_count,
                "ohlc_invalid_count":         self.issues.ohlc_invalid_count,
                "nonpositive_price_count":    self.issues.nonpositive_price_count,
                "extreme_return_count":       self.issues.extreme_return_count,
                "negative_volume_count":      self.issues.negative_volume_count,
                "volume_spike_count":         self.issues.volume_spike_count,
                "zero_volume_streak_max":     self.issues.zero_volume_streak_max,
                "out_of_session_count":       self.issues.out_of_session_count,
                "fetched_at_missing_count":   self.issues.fetched_at_missing_count,
                "fetched_at_future_count":    self.issues.fetched_at_future_count,
                "fetched_at_stale_count":     self.issues.fetched_at_stale_count,
            },
            "notes": list(self.notes),
        }


# ---------- helpers ----------


def grade_for_score(score: float, has_data: bool) -> str:
    """점수 → 등급. 데이터 없으면 EMPTY."""
    if not has_data:
        return "EMPTY"
    if score >= GRADE_GOOD:
        return "GOOD"
    if score >= GRADE_WARNING:
        return "WARNING"
    if score >= GRADE_POOR:
        return "POOR"
    return "EXCLUDE"


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_in_regular_session(ts: datetime) -> bool:
    """KRX 정규장(KST 평일 09:00–15:30)? 휴장일 캘린더는 후속 과제."""
    kst = _ensure_utc(ts).astimezone(_KST)
    if kst.weekday() >= 5:  # 토/일
        return False
    return SESSION_OPEN_KST <= kst.time() <= SESSION_CLOSE_KST


def _day_bounds_utc(report_date: date) -> tuple[datetime, datetime]:
    """KST 기준 하루의 UTC 경계 [start, end). 단순 시간범위라 휴장일 미반영."""
    start_kst = datetime.combine(report_date, time(0, 0), tzinfo=_KST)
    end_kst   = start_kst + timedelta(days=1)
    return start_kst.astimezone(timezone.utc), end_kst.astimezone(timezone.utc)


def _expected_for_session_day(interval: str, report_date: date) -> int:
    """KRX 정규장 09:00–15:30 = 6시간 30분 = 23,400초 기준 expected.

    간격이 day(`1d`)면 1, 아니면 (23400 / interval_seconds + 1).
    토/일은 0 — 하지만 본 모듈은 caller가 weekday를 골라 호출한다고 가정하지 않으므로
    weekday()>=5도 expected를 0으로 반환해 "데이터 자체가 없는 게 정상"임을 표현.
    """
    weekday = report_date.weekday()
    if weekday >= 5:
        return 0
    if interval == "1d":
        return 1
    secs = interval_to_seconds(interval)
    session_secs = (
        SESSION_CLOSE_KST.hour * 3600 + SESSION_CLOSE_KST.minute * 60
        - SESSION_OPEN_KST.hour * 3600  - SESSION_OPEN_KST.minute  * 60
    )
    return session_secs // secs + 1


# ---------- 검사 로직 ----------


def evaluate_day(
    bars:        list[MarketBar],
    *,
    symbol:      str,
    interval:    str,
    report_date: date,
    now:         datetime | None = None,
    include_out_of_session: bool = False,
) -> DailyQualityReport:
    """단일 (symbol, interval, date)의 품질 평가.

    `bars`는 호출자가 DB에서 미리 fetch한 그 날(KST 24h)의 모든 MarketBar 행.
    호출자가 timestamp 정렬을 보장하지 않아도 됨 — 본 함수가 재정렬한다.

    `include_out_of_session=True`이면 장외 데이터를 정상으로 간주 (점수 영향 없음,
    카운트는 여전히 기록).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    expected = _expected_for_session_day(interval, report_date)

    if not bars:
        # 빈 날 — weekday면 미보유는 비정상이지만 weekend면 정상.
        score = 100.0 if expected == 0 else 0.0
        return DailyQualityReport(
            symbol=symbol, interval=interval, report_date=report_date,
            issues=IssueCounts(expected_count=expected, actual_count=0,
                               missing_count=expected),
            score=score,
            grade="EMPTY",
            missing_rate=(None if expected == 0 else 1.0),
            coverage_score=(None if expected == 0 else 0.0),
            notes=[
                "no MarketBar rows for this date" if expected > 0
                else "weekend or holiday — no expected bars"
            ],
            include_out_of_session=include_out_of_session,
        )

    sorted_bars = sorted(bars, key=lambda b: _ensure_utc(b.timestamp))

    counts = IssueCounts(expected_count=expected, actual_count=len(sorted_bars),
                         missing_count=max(0, expected - len(sorted_bars)))

    duplicates       = _count_duplicates(sorted_bars)
    ohlc_bad         = _count_ohlc_invalid(sorted_bars)
    non_pos          = _count_nonpositive_price(sorted_bars)
    extreme          = _count_extreme_returns(sorted_bars)
    neg_vol          = _count_negative_volume(sorted_bars)
    vol_spike        = _count_volume_spikes(sorted_bars)
    zero_streak      = _max_zero_volume_streak(sorted_bars)
    oos              = _count_out_of_session(sorted_bars)
    fa_missing       = _count_fetched_at_missing(sorted_bars)
    fa_future        = _count_fetched_at_future(sorted_bars, now)
    fa_stale         = _count_fetched_at_stale(sorted_bars, now)

    counts = IssueCounts(
        expected_count=expected,
        actual_count=len(sorted_bars),
        missing_count=max(0, expected - len(sorted_bars)),
        duplicate_count=duplicates,
        ohlc_invalid_count=ohlc_bad,
        nonpositive_price_count=non_pos,
        extreme_return_count=extreme,
        negative_volume_count=neg_vol,
        volume_spike_count=vol_spike,
        zero_volume_streak_max=zero_streak,
        out_of_session_count=oos,
        fetched_at_missing_count=fa_missing,
        fetched_at_future_count=fa_future,
        fetched_at_stale_count=fa_stale,
    )

    missing_rate = (
        None if expected == 0
        else min(1.0, counts.missing_count / expected)
    )
    coverage_score = (
        None if expected == 0
        else max(0.0, 100.0 * (1.0 - missing_rate))
    )

    score, notes = _compute_score(counts, missing_rate, include_out_of_session)
    grade = grade_for_score(score, has_data=True)

    return DailyQualityReport(
        symbol=symbol, interval=interval, report_date=report_date,
        issues=counts,
        score=score, grade=grade,
        missing_rate=missing_rate,
        coverage_score=coverage_score,
        notes=notes,
        include_out_of_session=include_out_of_session,
    )


# ---------- 항목별 카운터 ----------


def _count_duplicates(bars: list[MarketBar]) -> int:
    seen: set = set()
    dup = 0
    for b in bars:
        key = (b.symbol, b.interval, _ensure_utc(b.timestamp))
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    return dup


def _count_ohlc_invalid(bars: list[MarketBar]) -> int:
    """high<low / open out of [low,high] / close out of [low,high]."""
    bad = 0
    for b in bars:
        if b.high < b.low:
            bad += 1
            continue
        if not (b.low <= b.open <= b.high):
            bad += 1
            continue
        if not (b.low <= b.close <= b.high):
            bad += 1
    return bad


def _count_nonpositive_price(bars: list[MarketBar]) -> int:
    bad = 0
    for b in bars:
        if b.open <= 0 or b.high <= 0 or b.low <= 0 or b.close <= 0:
            bad += 1
    return bad


def _count_extreme_returns(bars: list[MarketBar]) -> int:
    """인접 봉 close 변화율이 EXTREME_RETURN_PCT를 초과하면 1건."""
    extreme = 0
    prev = None
    for b in bars:
        if prev is not None and prev > 0:
            change_pct = abs(b.close - prev) / prev * 100.0
            if change_pct > EXTREME_RETURN_PCT:
                extreme += 1
        prev = b.close
    return extreme


def _count_negative_volume(bars: list[MarketBar]) -> int:
    return sum(1 for b in bars if b.volume is not None and b.volume < 0)


def _count_volume_spikes(bars: list[MarketBar]) -> int:
    """직전 VOLUME_SPIKE_LOOKBACK 평균의 VOLUME_SPIKE_RATIO 배 초과면 1건.

    lookback 미만이면 검사 skip. 평균 0이면 비교 의미 없음 → skip.
    """
    spikes = 0
    history: list[int] = []
    for b in bars:
        v = b.volume or 0
        if len(history) >= VOLUME_SPIKE_LOOKBACK:
            avg = sum(history[-VOLUME_SPIKE_LOOKBACK:]) / VOLUME_SPIKE_LOOKBACK
            if avg > 0 and v > avg * VOLUME_SPIKE_RATIO:
                spikes += 1
        history.append(v)
    return spikes


def _max_zero_volume_streak(bars: list[MarketBar]) -> int:
    """volume=0 (또는 None) 연속 길이의 최댓값."""
    longest = 0
    cur = 0
    for b in bars:
        if not b.volume:  # 0 또는 None
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


def _count_out_of_session(bars: list[MarketBar]) -> int:
    return sum(1 for b in bars if not _is_in_regular_session(b.timestamp))


def _count_fetched_at_missing(bars: list[MarketBar]) -> int:
    return sum(1 for b in bars if b.fetched_at is None)


def _count_fetched_at_future(bars: list[MarketBar], now: datetime) -> int:
    cnt = 0
    tolerance = timedelta(seconds=FETCHED_AT_FUTURE_TOLERANCE_SECONDS)
    for b in bars:
        if b.fetched_at is None:
            continue
        fa = _ensure_utc(b.fetched_at)
        if fa - now > tolerance:
            cnt += 1
    return cnt


def _count_fetched_at_stale(bars: list[MarketBar], now: datetime) -> int:
    cnt = 0
    cutoff = now - timedelta(days=FETCHED_AT_TOO_OLD_DAYS)
    for b in bars:
        if b.fetched_at is None:
            continue
        fa = _ensure_utc(b.fetched_at)
        if fa < cutoff:
            cnt += 1
    return cnt


# ---------- 점수 산출 ----------


def _compute_score(
    counts: IssueCounts,
    missing_rate: float | None,
    include_out_of_session: bool,
) -> tuple[float, list[str]]:
    """검사 카운트 → 0~100 점수 + 사유 노트.

    감점 정책 (중첩 가능):
      - missing_rate × 40        (최대 40점 감점)
      - duplicate_count × 5      (최대 20점)
      - ohlc_invalid_count × 5   (최대 30점)
      - nonpositive × 10         (최대 30점)
      - extreme_return × 3       (최대 15점)
      - negative_volume × 10     (최대 20점)
      - volume_spike × 2         (최대 10점)
      - zero_volume_streak >5    (점진 감점)
      - out_of_session ×0.5      (include 시 0점)
      - fetched_at_future × 5    (최대 15점)
      - fetched_at_stale × 1     (최대 10점)
      - fetched_at_missing × 0.5 (최대 10점)
    """
    score = 100.0
    notes: list[str] = []

    if missing_rate is not None and missing_rate > 0:
        deduct = min(40.0, missing_rate * 40.0 * 1.0)
        # missing_rate=1.0 → 40점 감점, 0.25 → 10점.
        score -= deduct
        notes.append(f"missing_rate={missing_rate:.2%} (-{deduct:.1f})")

    def _ded(val: int, per: float, cap: float, label: str) -> None:
        nonlocal score
        if val <= 0:
            return
        d = min(cap, val * per)
        score -= d
        notes.append(f"{label}={val} (-{d:.1f})")

    _ded(counts.duplicate_count,         5.0, 20.0, "duplicates")
    _ded(counts.ohlc_invalid_count,      5.0, 30.0, "ohlc_invalid")
    _ded(counts.nonpositive_price_count, 10.0, 30.0, "nonpositive_price")
    _ded(counts.extreme_return_count,    3.0, 15.0, "extreme_returns")
    _ded(counts.negative_volume_count,   10.0, 20.0, "negative_volume")
    _ded(counts.volume_spike_count,      2.0, 10.0, "volume_spike")

    if counts.zero_volume_streak_max > 5:
        d = min(15.0, (counts.zero_volume_streak_max - 5) * 1.5)
        score -= d
        notes.append(f"zero_volume_streak={counts.zero_volume_streak_max} (-{d:.1f})")

    if counts.out_of_session_count > 0 and not include_out_of_session:
        d = min(20.0, counts.out_of_session_count * 0.5)
        score -= d
        notes.append(f"out_of_session={counts.out_of_session_count} (-{d:.1f})")

    _ded(counts.fetched_at_future_count,  5.0, 15.0, "fetched_at_future")
    _ded(counts.fetched_at_stale_count,   1.0, 10.0, "fetched_at_stale")
    _ded(counts.fetched_at_missing_count, 0.5, 10.0, "fetched_at_missing")

    score = max(0.0, min(100.0, score))
    return score, notes


# ---------- DB 조회 helper ----------


def fetch_bars_for_day(
    db:          Session,
    *,
    symbol:      str,
    interval:    str,
    report_date: date,
) -> list[MarketBar]:
    """KST 기준 하루의 (symbol, interval) MarketBar 행."""
    start, end = _day_bounds_utc(report_date)
    rows = db.execute(
        select(MarketBar)
        .where(
            MarketBar.symbol == symbol,
            MarketBar.interval == interval,
            MarketBar.timestamp >= start,
            MarketBar.timestamp < end,
        )
        .order_by(MarketBar.timestamp)
    ).scalars().all()
    return list(rows)


def evaluate_range(
    db:        Session,
    *,
    symbol:    str,
    interval:  str,
    start_date: date,
    end_date:   date,
    now:       datetime | None = None,
    include_out_of_session: bool = False,
) -> list[DailyQualityReport]:
    """기간 내 모든 일자에 대한 일별 리포트.

    end_date inclusive. weekday/weekend 모두 포함 — caller가 필터링.
    """
    if start_date > end_date:
        return []
    out: list[DailyQualityReport] = []
    cur = start_date
    while cur <= end_date:
        bars = fetch_bars_for_day(db, symbol=symbol, interval=interval, report_date=cur)
        out.append(evaluate_day(
            bars,
            symbol=symbol, interval=interval, report_date=cur,
            now=now, include_out_of_session=include_out_of_session,
        ))
        cur += timedelta(days=1)
    return out


# ---------- summary helper for CLI ----------


def summarize_reports(reports: Iterable[DailyQualityReport]) -> dict:
    """기간 리포트의 등급별 카운트 + 평균 점수."""
    by_grade: dict[str, int] = {}
    scores: list[float] = []
    for r in reports:
        by_grade[r.grade] = by_grade.get(r.grade, 0) + 1
        scores.append(r.score)
    return {
        "report_count":  len(scores),
        "by_grade":      by_grade,
        "avg_score":     (round(sum(scores) / len(scores), 2) if scores else None),
        "min_score":     (round(min(scores), 2) if scores else None),
        "max_score":     (round(max(scores), 2) if scores else None),
    }
