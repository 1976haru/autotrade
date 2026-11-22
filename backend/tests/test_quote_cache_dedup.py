"""분봉 dedup 캐시(2026-06-29) 단위 테스트.

_fetch_full_day_minutes_cached: 오늘자 분봉만 TTL 캐시 (진입 신호 전용).
★청산/손절용 현재가(inquire-price/get_price)는 본 캐시 미경유 — 손절 신선도 보존.
"""
from __future__ import annotations

import asyncio
from app.market_data import kis_realtime as kr


class _FakeClient:
    def __init__(self):
        self.calls = 0

    async def inquire_time_dailychartprice(self, symbol, *, date, hour):
        self.calls += 1
        # 090100 바 포함 → _fetch_full_day_minutes 가 1콜 후 break.
        return {"output2": [
            {"stck_bsop_date": date, "stck_cntg_hour": "090100", "stck_prpr": "1000",
             "stck_oprc": "1000", "stck_hgpr": "1010", "stck_lwpr": "990", "cntg_vol": "100"},
            {"stck_bsop_date": date, "stck_cntg_hour": "093000", "stck_prpr": "1005",
             "stck_oprc": "1000", "stck_hgpr": "1010", "stck_lwpr": "995", "cntg_vol": "120"},
        ]}


def _reset():
    kr._today_min_cache.clear()


def test_cache_hit_skips_underlying_fetch():
    """같은 (symbol,date) TTL 내 재호출 → KIS 재호출 0 (캐시 히트)."""
    _reset()
    c = _FakeClient()
    b1 = asyncio.run(kr._fetch_full_day_minutes_cached(c, "000990", "20260629"))
    after_first = c.calls
    assert after_first >= 1 and len(b1) >= 1
    b2 = asyncio.run(kr._fetch_full_day_minutes_cached(c, "000990", "20260629"))
    assert c.calls == after_first, "TTL 내 재호출은 캐시 히트 → underlying 호출 증가 0이어야"
    assert b1 == b2


def test_cache_miss_on_different_symbol():
    _reset()
    c = _FakeClient()
    asyncio.run(kr._fetch_full_day_minutes_cached(c, "000990", "20260629"))
    n = c.calls
    asyncio.run(kr._fetch_full_day_minutes_cached(c, "002380", "20260629"))
    assert c.calls > n, "다른 종목은 캐시 미스 → fetch"


def test_cache_miss_on_different_date():
    _reset()
    c = _FakeClient()
    asyncio.run(kr._fetch_full_day_minutes_cached(c, "000990", "20260629"))
    n = c.calls
    asyncio.run(kr._fetch_full_day_minutes_cached(c, "000990", "20260630"))
    assert c.calls > n, "날짜 바뀌면 캐시 미스 → fetch (전일 stale 방지)"


def test_empty_result_not_cached():
    """빈 분봉은 캐시 안 함 → 다음 틱 재시도(합성/고착 금지)."""
    _reset()

    class _Empty:
        def __init__(self): self.calls = 0
        async def inquire_time_dailychartprice(self, symbol, *, date, hour):
            self.calls += 1
            return {"output2": []}

    c = _Empty()
    r1 = asyncio.run(kr._fetch_full_day_minutes_cached(c, "X", "20260629"))
    n = c.calls
    r2 = asyncio.run(kr._fetch_full_day_minutes_cached(c, "X", "20260629"))
    assert r1 == [] and r2 == []
    assert c.calls > n, "빈 결과는 캐시되면 안 됨 → 재호출돼야"


def test_ttl_constant_is_180():
    assert kr._TODAY_MIN_TTL_SEC == 180.0
