"""KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1 — 한국 장중 단타 세션 시계 (단일 진실).

모든 주문 진입점(스캔 tick / run-once / route_order / EOD 청산 coordinator / UI
status)이 *같은 서버 시계* 로 "지금 신규 BUY 가 가능한가 / 강제청산 구간인가" 를
판단하도록 한 곳에 모은 순수 모듈. 프론트 시간은 신뢰하지 않는다.

거래소 세션 (KRX 정규장 기준 — NXT 시간외/프리·애프터마켓은 *사용하지 않는다*):
- 정규장 09:00 ~ 15:30 KST (15:20 ~ 15:30 은 종가 단일가 매매).
- 특수일(새해 첫 거래일 10:00 개장, 수능일 10:00~16:30 등)은 세션 override.

단타 정책 (정규 세션 close 기준 상대 오프셋 — 특수일에도 같은 정책이 이동):
- entry_start  = max(window_start, open + 5분)           → 09:05
- buy_cutoff   = min(window_end, close − 60분)           → 14:30   (이후 신규 BUY 0)
- flatten_start= close − 15분                            → 15:15   (EOD 강제청산 시작)
- auction_start= close − 10분                            → 15:20   (종가 단일가)
- close                                                  → 15:30   (최종 대조)

휴장일 데이터: 아래 내장 목록 + 운영자 override JSON. 내장 목록은 공휴일 법령
기준 *추정* 이며 KRX 공식 휴장일 공지로 운영자가 확인해야 한다
(docs/kis_paper_intraday_flat_by_close.md). 달력이 커버하지 않는 연도는
`UNKNOWN` → 신규 BUY 차단(장 상태 불명확 시 보수적).

CLAUDE.md 가드: broker / OrderExecutor / route_order import 0건, 외부 HTTP 0건,
DB 0건(순수 함수), 안전 flag mutate 0건.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.scheduler.market_clock import (
    KOREAN_MARKET_CLOSE,
    KOREAN_MARKET_OPEN,
    to_kst,
)

_log = logging.getLogger("autotrade.intraday_session")

# ── KRX 휴장일 (내장, 운영자 확인 필요) ──────────────────────────────────────
# 2026: 신정 / 설(2/16~18) / 삼일절 대체(3/2) / 노동절(5/1) / 어린이날(5/5) /
#       부처님오신날 대체(5/25) / 지방선거(6/3) / 제헌절(7/17, 2026년부터 공휴일) /
#       광복절 대체(8/17) / 추석(9/24~25) + 대체(9/28) / 개천절 대체(10/5) /
#       한글날(10/9) / 성탄절(12/25) / 연말 휴장(12/31).
# 2027: 신정 / 설(2/8) + 대체(2/9) / 삼일절(3/1) / 노동절 대체(5/3, 5/1 토) / 어린이날(5/5) /
#       부처님오신날(5/13) / 제헌절 대체(7/19, 7/17 토) / 광복절 대체(8/16) / 추석(9/14~16) /
#       개천절 대체(10/4) / 한글날 대체(10/11) / 성탄절 대체(12/27) / 연말 휴장(12/31).
#       ← 2027 은 월력요항(2026-06 보도) 기준 *잠정* — KRX 2027 휴장일 공지(통상 12월)로 확인.
_BUILTIN_HOLIDAYS: frozenset[str] = frozenset({
    # 2025 (확정 실적): 신정 / 임시공휴일(1/27) / 설(1/28~30) / 삼일절 대체(3/3) /
    #   근로자의날 / 어린이날·부처님오신날(5/5) + 대체(5/6) / 대선(6/3) / 현충일 /
    #   광복절 / 개천절 / 추석(10/6~7) + 대체(10/8) / 한글날 / 성탄절 / 연말 휴장.
    "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-03-03", "2025-05-01", "2025-05-05", "2025-05-06", "2025-06-03",
    "2025-06-06", "2025-08-15", "2025-10-03", "2025-10-06", "2025-10-07",
    "2025-10-08", "2025-10-09", "2025-12-25", "2025-12-31",
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
    "2026-05-01", "2026-05-05", "2026-05-25", "2026-06-03", "2026-07-17", "2026-08-17",
    "2026-09-24", "2026-09-25", "2026-09-28", "2026-10-05", "2026-10-09",
    "2026-12-25", "2026-12-31",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-03", "2027-05-05",
    "2027-05-13", "2027-07-19", "2027-08-16", "2027-09-14", "2027-09-15", "2027-09-16",
    "2027-10-04", "2027-10-11", "2027-12-27", "2027-12-31",
})
# 특수 세션 (개장/폐장 시각 변경). 값: (open "HH:MM", close "HH:MM").
_BUILTIN_SPECIAL_SESSIONS: dict[str, tuple[str, str]] = {
    "2025-01-02": ("10:00", "15:30"),
    "2025-11-13": ("10:00", "16:30"),   # 2026학년도 수능
    "2026-01-02": ("10:00", "15:30"),   # 새해 첫 거래일 1시간 지연 개장
    "2026-11-19": ("10:00", "16:30"),   # (목) 2027학년도 수능 — 관행상 1시간 순연 (잠정)
    "2027-01-04": ("10:00", "15:30"),   # (월) 새해 첫 거래일 — 관행상 10:00 개장 (잠정)
}
# KRX 공식 공지로 *아직 확인되지 않은* 특수 세션 — 그날은 신규 BUY 전면 차단하고,
#   EOD 일정은 더 이른 마감(정규 15:30) 기준으로 잡는다(보수적: 어느 쪽이든 장중 청산).
#   운영자가 공지 확인 후 override JSON 의 "confirmed_special_sessions" 로 해제.
_BUILTIN_UNCONFIRMED_SPECIAL: frozenset[str] = frozenset({"2026-11-19", "2027-01-04"})
_BUILTIN_COVERED_YEARS: frozenset[int] = frozenset({2025, 2026, 2027})


class CalendarStatus(StrEnum):
    TRADING_DAY = "TRADING_DAY"
    HOLIDAY     = "HOLIDAY"
    WEEKEND     = "WEEKEND"
    UNKNOWN     = "UNKNOWN"   # 달력 미커버 연도 — 신규 BUY 차단.


class IntradayPhase(StrEnum):
    """장중 단타 정책 단계 — API / UI / log 에 그대로 emit."""
    NON_TRADING_DAY   = "NON_TRADING_DAY"     # 주말/휴장일
    CALENDAR_UNKNOWN  = "CALENDAR_UNKNOWN"    # 거래일 여부 불명확 (BUY 차단)
    PRE_OPEN          = "PRE_OPEN"            # 개장 전
    OPENING_BUFFER    = "OPENING_BUFFER"      # 개장 ~ entry_start (BUY 금지, SELL 금지 — 기존 창 유지)
    ENTRY_OPEN        = "ENTRY_OPEN"          # 신규 BUY + SELL 가능
    BUY_CUTOFF        = "BUY_CUTOFF"          # 신규 BUY 0, 일반 SELL/손절/익절 가능, 미체결 BUY 취소
    EOD_PRECHECK      = "EOD_PRECHECK"        # 15:00 사전점검 + 단계적 청산 (coordinator 단독)
    EOD_FLATTEN       = "EOD_FLATTEN"         # 강제청산 진행 (연속매매)
    CLOSING_AUCTION   = "CLOSING_AUCTION"     # 종가 단일가 (신규 주문은 플래그로만)
    POST_CLOSE        = "POST_CLOSE"          # 최종 대조, 주문 0


# 신규 BUY 차단 사유 (reason_code — UI / audit 정합).
BUY_ALLOWED                 = "BUY_ENTRY_ALLOWED"
BUY_BLOCKED_CUTOFF          = "BUY_CUTOFF_ACTIVE"
BUY_BLOCKED_NOT_TRADING_DAY = "BUY_BLOCKED_NON_TRADING_DAY"
BUY_BLOCKED_CALENDAR        = "BUY_BLOCKED_CALENDAR_UNKNOWN"
BUY_BLOCKED_BEFORE_ENTRY    = "BUY_BLOCKED_BEFORE_ENTRY_WINDOW"
BUY_BLOCKED_AFTER_CLOSE     = "BUY_BLOCKED_AFTER_CLOSE"
BUY_BLOCKED_SPECIAL_UNCONFIRMED = "BUY_BLOCKED_SPECIAL_SESSION_UNCONFIRMED"

_BUY_REASON_KO = {
    BUY_ALLOWED:                 "신규 매수 가능 시간입니다.",
    BUY_BLOCKED_CUTOFF:          "신규 매수 마감 시각이 지나 신규 매수를 하지 않습니다 (보유 청산만 진행).",
    BUY_BLOCKED_NOT_TRADING_DAY: "오늘은 휴장일/주말이라 신규 매수를 하지 않습니다.",
    BUY_BLOCKED_CALENDAR:        "거래일 여부를 확인할 수 없어(휴장일 달력 미등록) 신규 매수를 차단합니다.",
    BUY_BLOCKED_BEFORE_ENTRY:    "신규 매수 시작 시각 전입니다.",
    BUY_BLOCKED_AFTER_CLOSE:     "정규장이 끝나 신규 매수를 하지 않습니다.",
    BUY_BLOCKED_SPECIAL_UNCONFIRMED:
        "오늘은 거래시간이 바뀌는 특수일(수능일 등)인데 KRX 공지가 확인되지 않아 신규 매수를 차단합니다.",
}


def buy_reason_message_ko(code: str) -> str:
    return _BUY_REASON_KO.get(code, code)


def _parse_hhmm(s: Any) -> time | None:
    try:
        hh, mm = str(s).strip().split(":")
        return time(hour=int(hh), minute=int(mm))
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class KrxCalendar:
    holidays:         frozenset[str]
    special_sessions: dict[str, tuple[str, str]]
    covered_years:    frozenset[int]
    source:           str = "builtin"
    unconfirmed_special: frozenset[str] = frozenset()

    def special_unconfirmed(self, d: date) -> bool:
        return d.isoformat() in self.unconfirmed_special

    def status(self, d: date) -> CalendarStatus:
        if d.weekday() >= 5:
            return CalendarStatus.WEEKEND
        key = d.isoformat()
        if key in self.holidays:
            return CalendarStatus.HOLIDAY
        if d.year not in self.covered_years:
            return CalendarStatus.UNKNOWN
        return CalendarStatus.TRADING_DAY

    def session_hours(self, d: date) -> tuple[time, time]:
        if self.special_unconfirmed(d):
            # 미확인 특수일 — 정규 시각(더 이른 마감) 기준. 신규 BUY 는 별도로 차단된다.
            return KOREAN_MARKET_OPEN, KOREAN_MARKET_CLOSE
        sp = self.special_sessions.get(d.isoformat())
        if sp:
            o, c = _parse_hhmm(sp[0]), _parse_hhmm(sp[1])
            if o is not None and c is not None and o < c:
                return o, c
        return KOREAN_MARKET_OPEN, KOREAN_MARKET_CLOSE


def load_krx_calendar(override_path: str | None = None) -> KrxCalendar:
    """내장 달력 + (선택) 운영자 override JSON.

    JSON 형식: {"holidays": ["YYYY-MM-DD", ...],
               "special_sessions": {"YYYY-MM-DD": ["10:00", "16:30"]},
               "confirmed_special_sessions": ["YYYY-MM-DD"],   # KRX 공지 확인 후
               "covered_years": [2028]}
    override 의 special_sessions 는 운영자가 공지를 보고 넣은 값이므로 확인된 것으로 본다.
    파일 오류는 내장 달력으로 폴백(로그 경고) — 거래일을 *늘리는* 방향 폴백은 없다.
    """
    holidays = set(_BUILTIN_HOLIDAYS)
    special = dict(_BUILTIN_SPECIAL_SESSIONS)
    years = set(_BUILTIN_COVERED_YEARS)
    unconfirmed = set(_BUILTIN_UNCONFIRMED_SPECIAL)
    source = "builtin"
    if override_path:
        try:
            raw = json.loads(Path(override_path).read_text(encoding="utf-8"))
            holidays |= {str(x) for x in (raw.get("holidays") or [])}
            for k, v in (raw.get("special_sessions") or {}).items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    special[str(k)] = (str(v[0]), str(v[1]))
                    unconfirmed.discard(str(k))
            unconfirmed -= {str(x) for x in (raw.get("confirmed_special_sessions") or [])}
            years |= {int(y) for y in (raw.get("covered_years") or [])}
            source = "builtin+override"
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            _log.warning("[krx-calendar] override 로드 실패(내장 달력 사용): %s", exc)
    return KrxCalendar(frozenset(holidays), special, frozenset(years), source,
                       frozenset(unconfirmed))


@dataclass(frozen=True)
class IntradaySchedule:
    """하루 단타 일정 (KST). 거래일이 아니면 times 는 None."""
    trading_date:   str
    calendar:       CalendarStatus
    session_open:   time | None = None
    entry_start:    time | None = None
    buy_cutoff:     time | None = None
    precheck_start: time | None = None
    flatten_start:  time | None = None
    auction_start:  time | None = None
    session_close:  time | None = None
    special_session: bool = False
    special_session_unconfirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        def f(t: time | None) -> str | None:
            return t.strftime("%H:%M") if t is not None else None
        return {
            "trading_date":  self.trading_date,
            "calendar":      self.calendar.value,
            "session_open":  f(self.session_open),
            "entry_start":   f(self.entry_start),
            "buy_cutoff":    f(self.buy_cutoff),
            "precheck_start": f(self.precheck_start),
            "flatten_start": f(self.flatten_start),
            "auction_start": f(self.auction_start),
            "session_close": f(self.session_close),
            "special_session": self.special_session,
            "special_session_unconfirmed": self.special_session_unconfirmed,
            "exchange":      "KRX",
            "session_type":  "REGULAR_ONLY",   # NXT 시간외 미사용
            "timezone":      "Asia/Seoul",
        }


def _shift(t: time, minutes: int) -> time:
    base = datetime.combine(date(2000, 1, 3), t) + timedelta(minutes=minutes)
    return base.time()


@dataclass(frozen=True)
class IntradayPolicy:
    window_start:                  str = "09:05"
    window_end:                    str = "14:30"
    buy_cutoff_minutes_before_close: int = 60
    flatten_minutes_before_close:  int = 15
    auction_minutes_before_close:  int = 10
    precheck_minutes_before_close: int = 30
    calendar: KrxCalendar = field(default_factory=load_krx_calendar)

    @classmethod
    def from_settings(cls, settings: Any) -> "IntradayPolicy":
        return cls(
            window_start=str(getattr(settings, "kis_paper_auto_order_window_start", "09:05")),
            window_end=str(getattr(settings, "kis_paper_auto_order_window_end", "14:30")),
            buy_cutoff_minutes_before_close=int(getattr(
                settings, "kis_paper_buy_cutoff_minutes_before_close", 60) or 60),
            flatten_minutes_before_close=int(getattr(
                settings, "kis_paper_eod_flatten_minutes_before_close", 15) or 15),
            calendar=load_krx_calendar(
                str(getattr(settings, "krx_calendar_override_path", "") or "") or None),
        )

    def schedule(self, now: datetime) -> IntradaySchedule:
        kst = to_kst(now)
        d = kst.date()
        st = self.calendar.status(d)
        if st != CalendarStatus.TRADING_DAY:
            return IntradaySchedule(trading_date=d.isoformat(), calendar=st)
        o, c = self.calendar.session_hours(d)
        unconfirmed = self.calendar.special_unconfirmed(d)
        special = unconfirmed or (o, c) != (KOREAN_MARKET_OPEN, KOREAN_MARKET_CLOSE)
        ws = _parse_hhmm(self.window_start) or time(9, 5)
        we = _parse_hhmm(self.window_end) or time(14, 30)
        # 특수일(지연 개장)이면 설정 창도 개장 시각 기준으로 같이 이동.
        delay = (datetime.combine(d, o) - datetime.combine(d, KOREAN_MARKET_OPEN))
        delay_min = int(delay.total_seconds() // 60)
        entry_start = max(_shift(ws, delay_min), _shift(o, 5))
        cutoff_by_close = _shift(c, -max(0, self.buy_cutoff_minutes_before_close))
        buy_cutoff = min(_shift(we, delay_min), cutoff_by_close)
        # 강제청산 시작은 마감 12~30분 전으로 클램프 — 설정 0/과대값으로 EOD 를 사실상 끄거나
        #   단일가/장 밖으로 밀어내지 못하게 (조용한 비활성화 경로 차단).
        flatten = _shift(c, -min(30, max(12, int(self.flatten_minutes_before_close))))
        auction = _shift(c, -max(1, self.auction_minutes_before_close))
        precheck = _shift(c, -max(int(self.precheck_minutes_before_close),
                                  int(self.flatten_minutes_before_close)))
        # 방어: 단조 증가 보장 (잘못된 설정이 강제청산을 BUY 창 안으로 끌고 오지 않게).
        precheck = min(precheck, flatten)
        buy_cutoff = min(buy_cutoff, precheck)
        return IntradaySchedule(
            trading_date=d.isoformat(), calendar=st, session_open=o,
            entry_start=entry_start, buy_cutoff=buy_cutoff, precheck_start=precheck,
            flatten_start=flatten, auction_start=auction, session_close=c,
            special_session=special, special_session_unconfirmed=unconfirmed,
        )

    def phase(self, now: datetime) -> IntradayPhase:
        sch = self.schedule(now)
        if sch.calendar == CalendarStatus.UNKNOWN:
            return IntradayPhase.CALENDAR_UNKNOWN
        if sch.calendar != CalendarStatus.TRADING_DAY:
            return IntradayPhase.NON_TRADING_DAY
        t = to_kst(now).time()
        assert sch.session_open and sch.entry_start and sch.buy_cutoff
        assert sch.flatten_start and sch.auction_start and sch.session_close
        assert sch.precheck_start
        if t < sch.session_open:
            return IntradayPhase.PRE_OPEN
        if t < sch.entry_start:
            return IntradayPhase.OPENING_BUFFER
        if t < sch.buy_cutoff:
            return IntradayPhase.ENTRY_OPEN
        if t < sch.precheck_start:
            return IntradayPhase.BUY_CUTOFF
        if t < sch.flatten_start:
            return IntradayPhase.EOD_PRECHECK
        if t < sch.auction_start:
            return IntradayPhase.EOD_FLATTEN
        if t < sch.session_close:
            return IntradayPhase.CLOSING_AUCTION
        return IntradayPhase.POST_CLOSE

    def buy_entry_decision(self, now: datetime) -> tuple[bool, str]:
        """(allowed, reason_code) — 신규 BUY 의 *시간/달력* 판단 (단일 진실)."""
        ph = self.phase(now)
        if ph == IntradayPhase.ENTRY_OPEN:
            if self.calendar.special_unconfirmed(to_kst(now).date()):
                return False, BUY_BLOCKED_SPECIAL_UNCONFIRMED
            return True, BUY_ALLOWED
        if ph == IntradayPhase.CALENDAR_UNKNOWN:
            return False, BUY_BLOCKED_CALENDAR
        if ph == IntradayPhase.NON_TRADING_DAY:
            return False, BUY_BLOCKED_NOT_TRADING_DAY
        if ph in (IntradayPhase.PRE_OPEN, IntradayPhase.OPENING_BUFFER):
            return False, BUY_BLOCKED_BEFORE_ENTRY
        if ph == IntradayPhase.POST_CLOSE:
            return False, BUY_BLOCKED_AFTER_CLOSE
        return False, BUY_BLOCKED_CUTOFF

    def exit_window_open(self, now: datetime, *, forced: bool,
                         allow_closing_auction: bool) -> bool:
        """SELL(청산) 가능 시간대. 일반 청산: entry_start ~ auction_start.
        EOD 강제청산: flatten_start ~ auction_start (+ 단일가 구간은 플래그 ON 시만).
        달력 UNKNOWN 이어도 평일 정규장이면 청산은 허용(위험 축소 — KIS 가 최종 거부)."""
        ph = self.phase(now)
        if ph == IntradayPhase.CALENDAR_UNKNOWN:
            # 달력 불명 → 정규장 기본 시각 기준으로만 청산 허용.
            t = to_kst(now).time()
            return time(9, 5) <= t < time(15, 20)
        if forced:
            if ph in (IntradayPhase.EOD_PRECHECK, IntradayPhase.EOD_FLATTEN):
                return True
            return ph == IntradayPhase.CLOSING_AUCTION and allow_closing_auction
        return ph in (IntradayPhase.ENTRY_OPEN, IntradayPhase.BUY_CUTOFF,
                      IntradayPhase.EOD_PRECHECK, IntradayPhase.EOD_FLATTEN) or (
            ph == IntradayPhase.CLOSING_AUCTION and allow_closing_auction)


def policy_from_settings(settings: Any) -> IntradayPolicy:
    return IntradayPolicy.from_settings(settings)


__all__ = [
    "CalendarStatus", "IntradayPhase", "IntradaySchedule", "IntradayPolicy",
    "KrxCalendar", "load_krx_calendar", "policy_from_settings",
    "buy_reason_message_ko",
    "BUY_ALLOWED", "BUY_BLOCKED_CUTOFF", "BUY_BLOCKED_NOT_TRADING_DAY",
    "BUY_BLOCKED_CALENDAR", "BUY_BLOCKED_BEFORE_ENTRY", "BUY_BLOCKED_AFTER_CLOSE",
    "BUY_BLOCKED_SPECIAL_UNCONFIRMED",
]
