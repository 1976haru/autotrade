"""예외 자동복구 — retry/backoff + 표준 reason_code + RecoveryEvent.

장중 무중단 운용을 위한 복구 정책:
  - KIS API timeout: 3회 재시도, exponential backoff 1→2→4초, 실패 시 tick skip.
  - 네트워크 단절: 대기 후 재연결, 복구 시 RECOVERED.
  - DB lock/busy: 5초 대기, 3회 재시도.
  - KIS token expired: 자동 재발급(콜백 주입), 성공/실패 기록.
  - 가격 stale: 주문 차단 + tick skip.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API import 0건 — 순수
복구 유틸. 실제 재시도 대상 함수(시세조회 등)는 caller 가 콜러블로 주입한다.
RecoveryEvent.is_live_authorization 항상 False.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable


class RecoveryReason(str, Enum):
    OK = "OK"
    KIS_TIMEOUT = "KIS_TIMEOUT"
    NET_DOWN = "NET_DOWN"
    DB_LOCK = "DB_LOCK"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    PRICE_STALE = "PRICE_STALE"
    RECOVERED = "RECOVERED"
    UNRECOVERED = "UNRECOVERED"
    KIS_PAPER_ORDER_REJECTED = "KIS_PAPER_ORDER_REJECTED"


# 기본 backoff 스케줄 (초).
KIS_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0)
DB_BACKOFF: tuple[float, ...] = (5.0, 5.0, 5.0)
NET_WAIT_SECONDS: float = 30.0


def classify_exception(exc: BaseException) -> RecoveryReason:
    """예외 → 표준 reason_code. (메시지/타입 기반 best-effort)."""
    msg = f"{type(exc).__name__} {exc}".upper()
    if "EGW00133" in msg or "TOKEN" in msg or "UNAUTHORIZED" in msg or "401" in msg:
        return RecoveryReason.TOKEN_EXPIRED
    if "EGW00201" in msg or "TIMEOUT" in msg or "TIMED OUT" in msg or "RATE LIMIT" in msg:
        return RecoveryReason.KIS_TIMEOUT
    if "LOCK" in msg or "BUSY" in msg or "OPERATIONALERROR" in msg or "DEADLOCK" in msg:
        return RecoveryReason.DB_LOCK
    if ("CONNECT" in msg or "NETWORK" in msg or "UNREACHABLE" in msg
            or "CONNECTIONERROR" in msg or "DNS" in msg or "GETADDRINFO" in msg):
        return RecoveryReason.NET_DOWN
    return RecoveryReason.UNRECOVERED


@dataclass(frozen=True)
class RecoveryEvent:
    """복구 시도 결과 — 로그/이벤트 기록용. is_live_authorization 항상 False."""

    reason_code: str
    error_type: str | None = None
    retry_count: int = 0
    recovered: bool = False
    tick_id: str | None = None
    decision_id: str | None = None
    symbol: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError("RecoveryEvent.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "reason_code": self.reason_code,
            "error_type": self.error_type,
            "retry_count": self.retry_count,
            "recovered": self.recovered,
            "created_at": self.created_at,
            "is_live_authorization": False,
        }


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    *,
    retries: int = 3,
    delays: tuple[float, ...] = KIS_BACKOFF,
    classify: Callable[[BaseException], RecoveryReason] = classify_exception,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tick_id: str | None = None,
    symbol: str | None = None,
) -> tuple[Any, RecoveryEvent]:
    """fn 을 최대 `retries`회 재시도. (result, RecoveryEvent) 반환.

    성공 시 result + recovered=(재시도 발생 여부). 전부 실패 시 result=None +
    reason_code=분류값 + recovered=False (caller 가 tick skip). broker 호출 0건.
    """
    last_exc: BaseException | None = None
    reason = RecoveryReason.UNRECOVERED
    for attempt in range(max(1, retries)):
        try:
            result = await fn()
            ev = RecoveryEvent(
                reason_code=(RecoveryReason.RECOVERED.value if attempt > 0
                             else RecoveryReason.OK.value),
                retry_count=attempt, recovered=(attempt > 0),
                tick_id=tick_id, symbol=symbol,
            )
            return result, ev
        except Exception as exc:  # noqa: BLE001 — 복구 대상.
            last_exc = exc
            reason = classify(exc)
            if attempt < retries - 1:
                await sleep(delays[min(attempt, len(delays) - 1)])
    ev = RecoveryEvent(
        reason_code=reason.value,
        error_type=type(last_exc).__name__ if last_exc else None,
        retry_count=retries, recovered=False,
        tick_id=tick_id, symbol=symbol,
    )
    return None, ev


def is_price_stale(price_ts: datetime | None, *, now: datetime,
                   max_age_seconds: float) -> bool:
    """가격 timestamp 가 max_age_seconds 초과로 오래됐으면 True (주문 차단)."""
    if price_ts is None:
        return True
    if price_ts.tzinfo is None:
        price_ts = price_ts.replace(tzinfo=timezone.utc)
    age = (now - price_ts).total_seconds()
    return age > float(max_age_seconds)
