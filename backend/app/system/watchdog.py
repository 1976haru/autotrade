"""Watchdog 결정 로직 — 순수 함수 (테스트 용이).

엔진 멈춤 / backend 다운 / tick 지연을 감지해 행동을 결정한다. 실제 재시작/HTTP는
scripts/watchdog.py 가 본 결정 함수를 사용해 수행한다. 본 모듈은 broker /
OrderExecutor / route_order / KIS 주문 API import 0건.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum


class WatchdogAction(str, Enum):
    OK = "OK"
    WARN_TICK_SLOW = "WARN_TICK_SLOW"
    RESTART_ENGINE = "RESTART_ENGINE"
    RESTART_BACKEND = "RESTART_BACKEND"


# engine STUCK 이 이 시간(초) 이상 지속되면 재시작.
DEFAULT_STUCK_THRESHOLD_SECONDS: float = 300.0
# tick 간격이 설정값의 이 배수 초과면 WARN.
DEFAULT_TICK_SLOW_FACTOR: float = 3.0


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def tick_is_stale(
    last_tick_at: datetime | None,
    *,
    interval_seconds: float,
    now: datetime,
    factor: float = DEFAULT_TICK_SLOW_FACTOR,
) -> bool:
    """마지막 tick 이 interval*factor 초과로 지연됐으면 True."""
    last = _aware(last_tick_at)
    if last is None:
        return False  # 아직 tick 없음 — stale 아님 (시작 전).
    return (now - last).total_seconds() > float(interval_seconds) * float(factor)


def decide_action(
    *,
    health_ok: bool,
    engine_state: str | None = None,
    stuck_since: datetime | None = None,
    last_tick_at: datetime | None = None,
    interval_seconds: float = 30.0,
    now: datetime | None = None,
    stuck_threshold_seconds: float = DEFAULT_STUCK_THRESHOLD_SECONDS,
    tick_slow_factor: float = DEFAULT_TICK_SLOW_FACTOR,
) -> tuple[WatchdogAction, str]:
    """(action, reason) 결정. 우선순위: backend down > engine stuck > tick slow > OK."""
    now = now or datetime.now(timezone.utc)

    if not health_ok:
        return WatchdogAction.RESTART_BACKEND, "BACKEND_DOWN"

    stuck = _aware(stuck_since)
    if str(engine_state).upper() == "STUCK" and stuck is not None:
        if (now - stuck).total_seconds() >= float(stuck_threshold_seconds):
            return WatchdogAction.RESTART_ENGINE, "ENGINE_STUCK"

    if tick_is_stale(last_tick_at, interval_seconds=interval_seconds, now=now,
                     factor=tick_slow_factor):
        return WatchdogAction.WARN_TICK_SLOW, "TICK_SLOW"

    return WatchdogAction.OK, "OK"
