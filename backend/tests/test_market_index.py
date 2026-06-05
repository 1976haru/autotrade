"""P2/P4: 지수 비교 — limiter 경유(spy) / 캐시(2회 갱신) / 실패 부분표시 / 기간 수익률."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from app.brokers.kis_client import KisClient
import app.performance.market_index as mi
from app.risk.daily_pnl import today_kst


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_and_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(mi, "_history_path", lambda: tmp_path / "market_index_closes.json")
    mi.reset_market_index_cache_for_tests()
    yield
    mi.reset_market_index_cache_for_tests()


# ── ★limiter 경유 확인 ─────────────────────────────────────────────────────────

class _SpyLimiter:
    def __init__(self):
        self.calls = 0

    async def acquire(self):
        self.calls += 1


def _kis_handler(request):
    if request.url.path.endswith("/oauth2/tokenP"):
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
    if "inquire-index-price" in request.url.path:
        return httpx.Response(200, json={"output": {"bstp_nmix_prpr": "2700.55", "bstp_nmix_prdy_ctrt": "1.23"}})
    return httpx.Response(404, json={})


def test_index_quote_goes_through_shared_limiter():
    spy = _SpyLimiter()
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_kis_handler), rate_limiter=spy)
    raw = run(c.inquire_index_price("0001"))
    assert raw["output"]["bstp_nmix_prpr"] == "2700.55"
    assert spy.calls >= 1   # 토큰 + 지수 호출이 limiter 경유(_throttle)


def test_market_index_client_has_shared_limiter():
    # 운영 경로(default fetcher)가 쓰는 client 는 *계좌 단위 공유 limiter* 보유.
    from app.core.rate_limiter import get_kis_rate_limiter
    from app.brokers.kis import KisBrokerAdapter
    a = KisBrokerAdapter(app_key="K", app_secret="S", account_no="00000000", is_paper=True)
    assert a.client._rate_limiter is get_kis_rate_limiter()


# ── 캐시 (2회 갱신 / 매 요청 실조회 아님) ─────────────────────────────────────

def _fake_fetchers():
    calls = {"n": 0}
    async def fetch():
        calls["n"] += 1
        return {"KOSPI": {"value": 2700.0, "change_pct": 1.2}, "KOSDAQ": {"value": 900.0, "change_pct": 0.9}}
    async def eq():
        return 100_000_000
    return calls, fetch, eq


def test_cache_avoids_refetch_within_ttl():
    calls, fetch, eq = _fake_fetchers()
    today = today_kst()
    for _ in range(3):  # 같은 now_ts 3회 → 실조회 1회.
        run(mi.get_market_comparison_context(start=today, end=today, now_ts=1000.0,
                                             index_fetcher=fetch, equity_fetcher=eq))
    assert calls["n"] == 1
    # TTL 경과 → 재조회.
    run(mi.get_market_comparison_context(start=today, end=today, now_ts=1000.0 + 7 * 3600,
                                         index_fetcher=fetch, equity_fetcher=eq))
    assert calls["n"] == 2


def test_daily_uses_today_change_pct():
    _calls, fetch, eq = _fake_fetchers()
    today = today_kst()
    ctx = run(mi.get_market_comparison_context(start=today, end=today, now_ts=1.0,
                                               index_fetcher=fetch, equity_fetcher=eq))
    assert ctx["market"]["available"] is True
    assert ctx["market"]["kospi_return_pct"] == 1.2
    assert ctx["market"]["kosdaq_return_pct"] == 0.9
    assert ctx["current_equity_krw"] == 100_000_000


def test_multiday_uses_history_close(tmp_path, monkeypatch):
    hist = tmp_path / "market_index_closes.json"
    monkeypatch.setattr(mi, "_history_path", lambda: hist)
    today = today_kst()
    start = today - timedelta(days=3)
    hist.write_text(json.dumps({
        "KOSPI":  {start.isoformat(): 2600.0},
        "KOSDAQ": {start.isoformat(): 880.0},
    }), encoding="utf-8")
    _calls, fetch, eq = _fake_fetchers()
    ctx = run(mi.get_market_comparison_context(start=start, end=today, now_ts=1.0,
                                               index_fetcher=fetch, equity_fetcher=eq))
    assert ctx["market"]["kospi_return_pct"] == round((2700.0 - 2600.0) / 2600.0 * 100, 2)


def test_failure_returns_unavailable_not_exception():
    async def fetch():
        raise RuntimeError("KIS index down")
    async def eq():
        return None
    today = today_kst()
    ctx = run(mi.get_market_comparison_context(start=today, end=today, now_ts=1.0,
                                               index_fetcher=fetch, equity_fetcher=eq))
    assert ctx["market"]["available"] is False
    assert ctx["market"]["reason"] == "MARKET_FETCH_FAILED"


def test_persist_daily_close_written_once_fetched():
    _calls, fetch, eq = _fake_fetchers()
    today = today_kst()
    run(mi.get_market_comparison_context(start=today, end=today, now_ts=1.0,
                                         index_fetcher=fetch, equity_fetcher=eq))
    hist = mi._load_history()
    assert hist["KOSPI"][today.isoformat()] == 2700.0
