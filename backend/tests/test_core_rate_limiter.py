import asyncio
import time

import pytest

from app.core.rate_limiter import SlidingWindowRateLimiter


def run(coro):
    return asyncio.run(coro)


def test_allow_under_limit_returns_true():
    rl = SlidingWindowRateLimiter(max_calls=3, window_seconds=1.0)
    assert rl.allow() is True
    assert rl.allow() is True
    assert rl.allow() is True


def test_allow_over_limit_returns_false():
    rl = SlidingWindowRateLimiter(max_calls=2, window_seconds=10.0)
    assert rl.allow() is True
    assert rl.allow() is True
    assert rl.allow() is False


def test_allow_releases_after_window():
    rl = SlidingWindowRateLimiter(max_calls=1, window_seconds=0.1)
    assert rl.allow() is True
    assert rl.allow() is False
    time.sleep(0.15)
    assert rl.allow() is True


def test_acquire_returns_immediately_under_limit():
    rl = SlidingWindowRateLimiter(max_calls=5, window_seconds=1.0)

    async def driver():
        start = time.monotonic()
        await rl.acquire()
        await rl.acquire()
        await rl.acquire()
        return time.monotonic() - start

    elapsed = run(driver())
    assert elapsed < 0.05  # essentially instant


def test_acquire_blocks_when_at_limit():
    rl = SlidingWindowRateLimiter(max_calls=2, window_seconds=0.2)

    async def driver():
        await rl.acquire()
        await rl.acquire()
        start = time.monotonic()
        await rl.acquire()  # third one must wait until window aged out
        return time.monotonic() - start

    elapsed = run(driver())
    assert elapsed >= 0.15  # should wait ~window_seconds for the first call to age out


def test_acquire_is_concurrency_safe():
    """N concurrent acquirers see only max_calls slots in the first window."""
    rl = SlidingWindowRateLimiter(max_calls=2, window_seconds=0.3)

    async def driver():
        async def one():
            await rl.acquire()
            return time.monotonic()

        start = time.monotonic()
        timestamps = await asyncio.gather(*(one() for _ in range(4)))
        return [t - start for t in timestamps]

    relative = run(driver())
    # First two should be near-instant; later ones should be after >= window
    assert relative[0] < 0.05
    assert relative[1] < 0.05
    assert relative[2] >= 0.25
    assert relative[3] >= 0.25


@pytest.fixture(autouse=True)
def _close_loop_lock():
    """asyncio.Lock instances created at module scope can leak across asyncio
    runs in some environments. Re-enter at the test level by creating fresh
    limiters per test (already done) — this fixture is a no-op stamp."""
    yield
