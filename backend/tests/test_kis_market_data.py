"""KIS 실시간 read-only 시세 어댑터 테스트 (CONNECT-KIS-REALTIME-...-V2 §2).

실 KIS 호출 0건 — fake async client 주입. 주문 API 호출 0건 (정적 grep + 동작).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.market_data.kis_realtime as kr
from app.market_data.kis_realtime import (
    KIS_MARKET_DATA_UNAVAILABLE,
    KIS_PRICE_INVALID,
    KIS_PRICE_OK,
    KIS_PRICE_STALE,
    KisRealtimeQuote,
    build_kis_market_input,
    fetch_realtime_quote,
)

NOW = datetime(2026, 5, 27, 5, 0, 0, tzinfo=timezone.utc)


class FakeClient:
    """read-only 시세만 제공하는 fake — place_order 없음."""

    def __init__(self, *, price="75000", raise_price=False, bars=None):
        self._price = price
        self._raise = raise_price
        self._bars = bars

    async def get_price(self, symbol):
        if self._raise:
            raise RuntimeError("network down: Bearer ABCDEFG1234567890 leaked")
        return {"output": {
            "stck_prpr": self._price, "stck_oprc": "74000", "stck_hgpr": "76000",
            "stck_lwpr": "73500", "stck_sdpr": "74500", "acml_vol": "1200000",
            "wghn_avrg_stck_prc": "74800",
        }}

    async def inquire_time_dailychartprice(self, symbol, *, date, **kw):
        if self._bars is None:
            raise RuntimeError("no bars")
        return {"output2": self._bars}


def test_fetch_ok():
    q = asyncio.run(fetch_realtime_quote("005930", client=FakeClient(), now=NOW))
    assert q.status == KIS_PRICE_OK
    assert q.price == 75000.0
    assert q.price_source == "kis"
    assert q.ok is True
    assert q.is_live_authorization is False


def test_fetch_unavailable_on_exception():
    q = asyncio.run(fetch_realtime_quote("005930", client=FakeClient(raise_price=True), now=NOW))
    assert q.status == KIS_MARKET_DATA_UNAVAILABLE
    assert q.price is None
    assert q.ok is False


def test_fetch_invalid_price():
    q = asyncio.run(fetch_realtime_quote("005930", client=FakeClient(price="0"), now=NOW))
    assert q.status == KIS_PRICE_INVALID


def test_fetch_stale_when_market_closed():
    q = asyncio.run(fetch_realtime_quote("005930", client=FakeClient(), now=NOW,
                                         market_is_open=False))
    assert q.status == KIS_PRICE_STALE
    assert q.is_stale is True
    assert q.ok is False


def test_fetch_unavailable_when_price_missing():
    class _NoPrice:
        async def get_price(self, symbol):
            return {"output": {"stck_oprc": "100"}}
    q = asyncio.run(fetch_realtime_quote("005930", client=_NoPrice(), now=NOW))
    assert q.status == KIS_MARKET_DATA_UNAVAILABLE


def test_no_mock_fallback_on_failure():
    # 실패 시 mock 가격으로 대체하지 않는다 — price 는 None 이어야 한다.
    q = asyncio.run(fetch_realtime_quote("005930", client=FakeClient(raise_price=True), now=NOW))
    assert q.price is None


def test_build_market_input_with_bars():
    bars = [{"stck_cntg_hour": f"0900{i:02d}", "stck_prpr": str(74000 + i * 100)}
            for i in range(10)]
    mi, q = asyncio.run(build_kis_market_input(
        "005930", client=FakeClient(bars=bars), now=NOW))
    assert mi is not None
    assert mi.current_price == 75000.0
    assert mi.symbol == "005930"
    assert len(mi.recent_closes) >= 5
    assert q.price_source == "kis"


def test_build_market_input_returns_none_when_quote_fails():
    mi, q = asyncio.run(build_kis_market_input(
        "005930", client=FakeClient(raise_price=True), now=NOW))
    assert mi is None
    assert q.status == KIS_MARKET_DATA_UNAVAILABLE


def test_build_market_input_degrades_without_bars():
    # 분봉 미가용이어도 스냅샷으로 최소 구성 + 실패하지 않음.
    mi, q = asyncio.run(build_kis_market_input(
        "005930", client=FakeClient(bars=None), now=NOW))
    assert mi is not None
    assert mi.current_price == 75000.0


def test_quote_invariant_price_source_must_be_kis():
    with pytest.raises(ValueError):
        KisRealtimeQuote(symbol="005930", status=KIS_PRICE_OK, price=1.0,
                         price_source="mock")


def test_error_message_redacts_secret():
    q = asyncio.run(fetch_realtime_quote("005930", client=FakeClient(raise_price=True), now=NOW))
    # reason_message 는 표준 안내문 (Bearer 토큰 원문 0건).
    assert "Bearer" not in q.reason_message


def test_module_has_no_order_api_imports():
    # 주문 *호출* / 주문 모듈 *import* 0건 (docstring 의 설명 언급은 허용).
    src = Path(kr.__file__).read_text(encoding="utf-8")
    assert ".place_order(" not in src
    assert "route_order(" not in src
    assert "from app.execution" not in src
    assert "OrderExecutor(" not in src
    assert "import OrderExecutor" not in src
