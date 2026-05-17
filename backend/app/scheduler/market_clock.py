"""Korean stock market clock — phase of day calculator.

feat/step2-market-waiting-mode: Auto Paper Loop 시작 시점에 시장 시간을
판단해 *장 시작 전* 이면 RUNNING 으로 즉시 진입하지 않고 `WAITING_MARKET`
상태로 대기. 09:00 KST 가 되면 자동으로 RUNNING 으로 promote.

본 모듈은 *순수 함수* — DB / 외부 API / broker / OrderExecutor 의존 0건.
- 입력: `datetime` (tz-aware 권장, naive 면 UTC 가정)
- 출력: `MarketPhase` enum

한국 시장 시간 (KST = UTC+9, DST 없음):
- 평일 00:00 ~ 09:00 → PRE_OPEN  (장 시작 대기)
- 평일 09:00 ~ 15:30 → OPEN      (정규장)
- 평일 15:30 ~ 24:00 → CLOSED    (장 종료 후)
- 토/일               → WEEKEND   (휴장)

공휴일 처리는 *후속 항목* — 본 PR 시점에는 평일/주말 만 구분.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건
- 외부 HTTP / DB 호출 0건 (순수 함수)
- 안전 flag mutate 0건
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from enum import StrEnum


# KST = UTC+9, DST 없음. 표준 라이브러리 `zoneinfo` 가 없는 환경 (PyInstaller
# stripped runtime 등) 도 안전하게 동작하도록 고정 offset 사용.
KST_OFFSET = timedelta(hours=9)
KST_TZ = timezone(KST_OFFSET, name="KST")

# 한국 정규장 시간 (장 시작 09:00, 장 종료 15:30 KST).
KOREAN_MARKET_OPEN  = time(hour=9,  minute=0)
KOREAN_MARKET_CLOSE = time(hour=15, minute=30)


class MarketPhase(StrEnum):
    """한국 주식시장 시점 단계.

    값은 안정된 문자열 — frontend / log / API 응답에 그대로 emit.
    """
    PRE_OPEN = "PRE_OPEN"   # 평일 장 시작 전 (00:00 ~ 09:00 KST)
    OPEN     = "OPEN"       # 평일 정규장 (09:00 ~ 15:30 KST)
    CLOSED   = "CLOSED"     # 평일 장 종료 후 (15:30 ~ 24:00 KST)
    WEEKEND  = "WEEKEND"    # 토/일


def _ensure_tz_aware(dt: datetime) -> datetime:
    """naive datetime 은 UTC 로 간주 — 호출자 실수 방어."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_kst(now: datetime) -> datetime:
    """UTC / 임의 timezone → KST 변환."""
    return _ensure_tz_aware(now).astimezone(KST_TZ)


def current_market_phase(now: datetime | None = None) -> MarketPhase:
    """현재 한국 시장 단계.

    Args:
        now: 시점 (tz-aware 권장). None 이면 datetime.now(utc).

    Returns:
        MarketPhase 중 하나. 공휴일 미고려 (평일/주말 만 구분).

    Examples:
        >>> from datetime import datetime
        >>> # 평일 08:50 KST → PRE_OPEN
        >>> dt = datetime(2026, 1, 5, 23, 50, tzinfo=timezone.utc)  # Mon UTC 23:50 = Tue 08:50 KST
        >>> current_market_phase(dt) == MarketPhase.PRE_OPEN
        True
    """
    if now is None:
        now = datetime.now(timezone.utc)
    kst = to_kst(now)

    # 평일 / 주말 판정. Mon=0, Sun=6.
    if kst.weekday() >= 5:
        return MarketPhase.WEEKEND

    kst_time = kst.time()
    if kst_time < KOREAN_MARKET_OPEN:
        return MarketPhase.PRE_OPEN
    if kst_time < KOREAN_MARKET_CLOSE:
        return MarketPhase.OPEN
    return MarketPhase.CLOSED


def is_market_open(now: datetime | None = None) -> bool:
    """편의 — current_market_phase(now) == OPEN."""
    return current_market_phase(now) == MarketPhase.OPEN
