import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class SlidingWindowRateLimiter:
    """간단한 in-memory 호출 제한기. 운영 단계에서는 Redis 기반으로 교체한다.

    `allow()` is the cheap non-blocking check. `acquire()` blocks the current
    coroutine until a slot is available, then records the call — useful when
    wrapping outbound HTTP calls (e.g. KIS) so the caller does not have to
    handle the rate-limit decision itself.

    `now_fn` / `sleep_fn` default to the real monotonic clock and asyncio.sleep
    but are injectable so the pacing can be exercised deterministically and
    without real wall-clock waits in tests (e.g. the KIS 2 req/s measurement).
    """

    max_calls: int
    window_seconds: float
    calls: deque[float] = field(default_factory=deque)
    now_fn: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def allow(self) -> bool:
        now = self.now_fn()
        while self.calls and now - self.calls[0] > self.window_seconds:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True

    async def acquire(self) -> None:
        """Block until under the limit, then record the call. Coroutine-safe."""
        async with self._lock:
            while True:
                now = self.now_fn()
                while self.calls and now - self.calls[0] > self.window_seconds:
                    self.calls.popleft()
                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return
                wait = self.window_seconds - (now - self.calls[0])
                await self.sleep_fn(max(wait, 0.01))


@lru_cache(maxsize=1)
def get_kis_rate_limiter() -> "SlidingWindowRateLimiter":
    """Process-wide *shared* KIS rate limiter.

    KIS 의 호출 제한은 *계좌(account) 단위* 다. KisClient 인스턴스마다 별도
    limiter 를 두면(per-instance) 동시에 도는 경로(유니버스 시세 스캔 + 체결
    폴링 + 잔고/포지션 UI 폴링)가 *각자* 한도 안이어도 *합산*으론 계좌 한도를
    초과해 EGW00201("초당 거래건수 초과") 폭주가 난다(2026-06-05: 4,556건).
    모든 KisClient 가 이 단일 limiter 를 공유해 aggregate 호출을 한도 이하로 묶는다.

    단일 asyncio 이벤트루프(uvicorn) 가정 — 내부 asyncio.Lock 은 첫 await 시점에
    현재 루프에 바인딩된다. (운영 단계 다중 프로세스는 Redis 기반으로 교체.)
    """
    from app.core.config import get_settings
    s = get_settings()
    return SlidingWindowRateLimiter(
        max_calls=int(s.kis_rate_limit_calls),
        window_seconds=float(s.kis_rate_limit_window_seconds),
    )
