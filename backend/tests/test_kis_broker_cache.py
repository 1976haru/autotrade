"""EGW00201 mitigation (2026-06-02): short-TTL read-only caching on the KIS
quote/balance endpoints + a deterministic 10-symbol scan measurement proving
the call rate stays <= 2 req/s with zero EGW00201.

Background: on 2026-06-02 the KIS paper auto loop logged 141 EGW00201 ("초당
거래건수 초과") errors — 138 of them on the balance/quote read endpoints —
because a multi-symbol scan re-reads balance per symbol/order and re-quotes the
same symbol across scan + route_order. These tests lock the two fixes:
  1. caching collapses duplicate balance/quote reads (volume), and
  2. the rate limiter paces the remaining distinct calls to <= 2 req/s (rate).
"""

import asyncio

import httpx

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.brokers.kis import KisBrokerAdapter
from app.brokers.kis_client import KisApiError, KisClient
from app.core.rate_limiter import SlidingWindowRateLimiter


def run(coro):
    return asyncio.run(coro)


# ---------- counting fake client (cache unit tests) ----------

class _CountingClient:
    """Duck-typed KIS client that counts read calls — no HTTP, no rate limit."""

    def __init__(self, price: str = "75000"):
        self.price = price
        self.price_calls: dict[str, int] = {}
        self.balance_calls = 0
        self.place_calls = 0

    async def get_price(self, symbol: str) -> dict:
        self.price_calls[symbol] = self.price_calls.get(symbol, 0) + 1
        return {"output": {"stck_prpr": self.price}}

    async def inquire_balance(self, cano: str, prdt: str) -> dict:
        self.balance_calls += 1
        return {
            "output1": [],
            "output2": [{"dnca_tot_amt": "5000000", "tot_evlu_amt": "10000000"}],
        }

    async def place_order(self, *args, **kwargs) -> dict:
        self.place_calls += 1
        return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "0000000123"}}


def _adapter(client, *, qttl=2.0, bttl=5.0, clock=None):
    return KisBrokerAdapter(
        app_key="k", app_secret="s", account_no="1234567801",
        client=client, quote_cache_ttl_seconds=qttl,
        balance_cache_ttl_seconds=bttl, clock=clock,
    )


# ---------- quote cache ----------

def test_quote_cache_hit_within_ttl_makes_one_call():
    vt = [1000.0]
    c = _CountingClient()
    a = _adapter(c, qttl=2.0, clock=lambda: vt[0])

    async def drive():
        q1 = await a.get_price("005930")
        q2 = await a.get_price("005930")   # within TTL → cache hit
        return q1, q2

    q1, q2 = run(drive())
    assert c.price_calls["005930"] == 1            # only one upstream call
    assert q2 is q1                                # same cached object


def test_quote_cache_preserves_original_timestamp_freshness_is_honest():
    """A cached quote keeps its original fetch timestamp, so its age is reported
    honestly — RiskManager's stale check still sees the real staleness, not a
    refreshed 'now'. TTL (2s) is far below STALE_PRICE_MAX_AGE_SECONDS (60s)."""
    vt = [1000.0]
    c = _CountingClient()
    a = _adapter(c, qttl=2.0, clock=lambda: vt[0])

    async def drive():
        q1 = await a.get_price("005930")
        vt[0] += 1.4                               # 1.4s later, still within TTL
        q2 = await a.get_price("005930")
        return q1, q2

    q1, q2 = run(drive())
    assert c.price_calls["005930"] == 1
    assert q2.timestamp == q1.timestamp            # NOT re-stamped to "now"


def test_quote_cache_miss_after_ttl_refetches():
    vt = [1000.0]
    c = _CountingClient()
    a = _adapter(c, qttl=2.0, clock=lambda: vt[0])

    async def drive():
        await a.get_price("005930")
        vt[0] += 2.5                               # past TTL
        await a.get_price("005930")

    run(drive())
    assert c.price_calls["005930"] == 2


def test_quote_cache_is_per_symbol():
    c = _CountingClient()
    a = _adapter(c, qttl=2.0, clock=lambda: 1000.0)

    async def drive():
        for sym in ("005930", "000660", "035420"):
            await a.get_price(sym)
            await a.get_price(sym)                  # second hit cached

    run(drive())
    assert c.price_calls == {"005930": 1, "000660": 1, "035420": 1}


def test_quote_cache_ttl_zero_disables_caching():
    c = _CountingClient()
    a = _adapter(c, qttl=0.0, clock=lambda: 1000.0)

    async def drive():
        await a.get_price("005930")
        await a.get_price("005930")

    run(drive())
    assert c.price_calls["005930"] == 2            # no caching → two calls


# ---------- balance cache (shared by get_balance + get_positions) ----------

def test_balance_cache_shared_by_balance_and_positions():
    c = _CountingClient()
    a = _adapter(c, bttl=5.0, clock=lambda: 1000.0)

    async def drive():
        await a.get_balance()
        await a.get_positions()
        await a.get_balance()

    run(drive())
    assert c.balance_calls == 1                     # one inquire-balance for all three


def test_balance_cache_invalidated_after_place_order():
    vt = [1000.0]
    c = _CountingClient()
    a = _adapter(c, bttl=5.0, clock=lambda: vt[0])
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET)

    async def drive():
        await a.get_balance()                       # call 1, cached
        await a.place_order(order)                  # invalidates balance cache
        await a.get_balance()                       # must refetch → call 2

    run(drive())
    assert c.balance_calls == 2


def test_balance_cache_ttl_zero_disables_caching():
    c = _CountingClient()
    a = _adapter(c, bttl=0.0, clock=lambda: 1000.0)

    async def drive():
        await a.get_balance()
        await a.get_balance()

    run(drive())
    assert c.balance_calls == 2


# ---------- 10-symbol scan measurement: <= 2 req/s, zero EGW00201 ----------

_KIS_LIMIT_PER_SEC = 2          # KIS paper documented quote limit = 2 req/s
_WINDOW = 1.0
_SYMBOLS = ["005930", "000660", "035420", "005380", "000270",
            "068270", "207940", "373220", "005490", "005935"]


def _virtual_clock():
    vt = [0.0]
    def now() -> float:
        return vt[0]
    async def sleep(dt: float) -> None:
        vt[0] += max(dt, 0.0)
    return vt, now, sleep


def _kis_mock_transport(now_fn, call_log):
    """httpx transport that records each non-token call's virtual time and
    returns an EGW00201 500 if > 2 calls land within any rolling 1.0s window."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        t = now_fn()
        call_log.append(t)
        recent = [x for x in call_log if t - x < _WINDOW]
        if len(recent) > _KIS_LIMIT_PER_SEC:
            return httpx.Response(500, json={
                "rt_cd": "1", "msg_cd": "EGW00201",
                "msg1": "초당 거래건수를 초과하였습니다.",
            })
        if path.endswith("/inquire-price"):
            return httpx.Response(200, json={"output": {"stck_prpr": "75000"}})
        if path.endswith("/inquire-balance"):
            return httpx.Response(200, json={
                "output1": [],
                "output2": [{"dnca_tot_amt": "5000000", "tot_evlu_amt": "10000000"}],
            })
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def _max_calls_in_any_window(call_log) -> int:
    worst = 0
    for i, t in enumerate(call_log):
        n = sum(1 for x in call_log if t - _WINDOW < x <= t)
        worst = max(worst, n)
    return worst


def _build_adapter(*, calls, window, qttl, bttl):
    vt, now, sleep = _virtual_clock()
    call_log: list[float] = []
    limiter = SlidingWindowRateLimiter(
        max_calls=calls, window_seconds=window, now_fn=now, sleep_fn=sleep,
    )
    client = KisClient("k", "s", is_paper=True,
                       transport=_kis_mock_transport(now, call_log),
                       rate_limiter=limiter)
    adapter = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801",
                               client=client, quote_cache_ttl_seconds=qttl,
                               balance_cache_ttl_seconds=bttl, clock=now)
    return adapter, call_log


async def _scan_tick(adapter):
    """Mimic one scan tick: per symbol quote it (council input) and read balance
    (route_order pre-trade check). Returns EGW count seen by callers."""
    egw = 0
    for sym in _SYMBOLS:
        try:
            await adapter.get_price(sym)
            await adapter.get_balance()
        except KisApiError as e:
            if "EGW00201" in str(e):
                egw += 1
    return egw


def test_scan_with_fix_stays_under_2_per_sec_and_zero_egw():
    """Production config (2/1.1s limiter + caching): a 10-symbol scan never
    exceeds 2 req/s and triggers zero EGW00201."""
    adapter, call_log = _build_adapter(calls=2, window=1.1, qttl=1.5, bttl=5.0)
    egw = run(_scan_tick(adapter))

    assert egw == 0
    assert _max_calls_in_any_window(call_log) <= _KIS_LIMIT_PER_SEC
    # caching collapsed 10 per-symbol balance reads into a single upstream call.
    balance_calls = sum(1 for _ in call_log)  # sanity: see explicit count below
    # 10 distinct quotes + 1 balance = 11 upstream calls (not 20).
    assert len(call_log) == 11


def test_scan_without_fix_would_burst_and_trip_egw():
    """Contrast: the pre-fix config (old 5/1.0s limiter, no caching) bursts past
    2 req/s and trips EGW00201 — proving the fix is what prevents it."""
    adapter, call_log = _build_adapter(calls=5, window=1.0, qttl=0.0, bttl=0.0)
    egw = run(_scan_tick(adapter))

    assert _max_calls_in_any_window(call_log) > _KIS_LIMIT_PER_SEC
    assert egw > 0
