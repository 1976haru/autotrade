import asyncio
import time

import pytest

from app.core.rate_limiter import SlidingWindowRateLimiter, get_kis_rate_limiter


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


# ── Step 3: 계좌 단위 공유 limiter (EGW00201 폭주 방지) ─────────────────────────

def test_shared_kis_limiter_is_singleton():
    a = get_kis_rate_limiter()
    b = get_kis_rate_limiter()
    assert a is b  # 모든 KisClient 가 동일 인스턴스를 공유 → 계좌 단위 한도.


def test_lazy_and_explicit_kis_clients_share_one_limiter():
    # lazy 경로(KisBrokerAdapter.client)도 공유 limiter 를 주입받아야 한다 —
    # 무제한 client 가 생기면 합산 호출이 KIS 한도를 넘어 EGW00201 폭주.
    from app.brokers.kis import KisBrokerAdapter
    a1 = KisBrokerAdapter(app_key="K", app_secret="S", account_no="1-01", is_paper=True)
    a2 = KisBrokerAdapter(app_key="K", app_secret="S", account_no="2-01", is_paper=True)
    assert a1.client._rate_limiter is get_kis_rate_limiter()
    assert a1.client._rate_limiter is a2.client._rate_limiter


def test_mock_load_aggregate_never_exceeds_limit():
    """mock 부하: 두 경로(스캔+폴링)에서 동시 30콜이 *하나의* 공유 limiter 를
    거치면, 어떤 window 에서도 max_calls 를 초과하지 않는다(KIS 한도 안전).
    가상 시계로 결정적 — 실제 대기 0초, KIS 실호출 0건."""
    clock = {"t": 0.0}
    def now():
        return clock["t"]
    async def vsleep(d):
        clock["t"] += max(d, 0.0)
    MAX, WIN = 2, 1.1
    lim = SlidingWindowRateLimiter(max_calls=MAX, window_seconds=WIN,
                                   now_fn=now, sleep_fn=vsleep)
    done = []
    async def one():
        await lim.acquire()
        done.append(clock["t"])
    async def run_all():
        await asyncio.gather(*(one() for _ in range(30)))
    run(run_all())
    assert len(done) == 30
    done.sort()
    # 불변식: (MAX)번째 다음 완료는 최소 ~window 뒤 — 어떤 1.1s 창에도 ≤ MAX.
    for i in range(len(done) - MAX):
        assert done[i + MAX] - done[i] >= WIN - 0.05, (
            f"window 내 {MAX}콜 초과: {done[i:i+MAX+1]}")
    # 30콜 / 2-per-1.1s → 가상 시계가 충분히 진행(스로틀 실제 동작 증거).
    assert clock["t"] >= WIN * (30 / MAX - 1) - 0.1


@pytest.fixture(autouse=True)
def _close_loop_lock():
    """asyncio.Lock instances created at module scope can leak across asyncio
    runs in some environments. Re-enter at the test level by creating fresh
    limiters per test (already done) — this fixture is a no-op stamp."""
    yield
