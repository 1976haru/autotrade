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
    WARN_BACKEND_DOWN = "WARN_BACKEND_DOWN"
    RESTART_ENGINE = "RESTART_ENGINE"
    RESTART_BACKEND = "RESTART_BACKEND"


# engine STUCK 이 이 시간(초) 이상 지속되면 재시작.
DEFAULT_STUCK_THRESHOLD_SECONDS: float = 300.0
# tick 간격이 설정값의 이 배수 초과면 WARN.
DEFAULT_TICK_SLOW_FACTOR: float = 3.0
# health check 가 이 횟수 이상 *연속* 실패해야 재시작(hysteresis) — 미만은
# WARN_BACKEND_DOWN 만(재시작 아님). 단발성 응답 실패(GC pause, 순간 blip)로
# 즉시 재시작되던 오판(08-10 사고, results/watchdog_fix/design.md) 방지.
DEFAULT_HEALTH_FAIL_THRESHOLD: int = 3


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
    consecutive_health_fail_count: int = 1,
    health_fail_threshold: int = DEFAULT_HEALTH_FAIL_THRESHOLD,
) -> tuple[WatchdogAction, str]:
    """(action, reason) 결정. 우선순위: backend down(hysteresis) > engine stuck > tick slow > OK.

    health_ok=False 는 *단독으로는* 재시작을 일으키지 않는다 — 연속
    `health_fail_threshold` 회 이상 실패해야 RESTART_BACKEND, 미만이면
    WARN_BACKEND_DOWN(경고만, 재시작 아님). `consecutive_health_fail_count`
    는 *이번 호출을 포함한* 연속 실패 횟수로, caller(scripts/watchdog.py)가
    상태로 유지해 넘긴다 — health_ok=True 가 한 번이라도 뜨면 caller 가 즉시
    0으로 리셋 후 호출해야 한다(진짜 정상화는 지연 없이 인정).
    """
    now = now or datetime.now(timezone.utc)

    if not health_ok:
        if int(consecutive_health_fail_count) >= int(health_fail_threshold):
            return WatchdogAction.RESTART_BACKEND, "BACKEND_DOWN"
        return WatchdogAction.WARN_BACKEND_DOWN, "BACKEND_DOWN"

    stuck = _aware(stuck_since)
    if str(engine_state).upper() == "STUCK" and stuck is not None:
        if (now - stuck).total_seconds() >= float(stuck_threshold_seconds):
            return WatchdogAction.RESTART_ENGINE, "ENGINE_STUCK"

    if tick_is_stale(last_tick_at, interval_seconds=interval_seconds, now=now,
                     factor=tick_slow_factor):
        return WatchdogAction.WARN_TICK_SLOW, "TICK_SLOW"

    return WatchdogAction.OK, "OK"
