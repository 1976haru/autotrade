import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowRateLimiter:
    """간단한 in-memory 호출 제한기. 운영 단계에서는 Redis 기반으로 교체한다."""

    max_calls: int
    window_seconds: float
    calls: deque[float] = field(default_factory=deque)

    def allow(self) -> bool:
        now = time.monotonic()
        while self.calls and now - self.calls[0] > self.window_seconds:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True
