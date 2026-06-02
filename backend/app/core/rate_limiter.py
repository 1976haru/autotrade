import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


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
