"""B1: 아침 브리핑 — 밤사이 미국 시세 5종(S&P500/나스닥/다우/SOX/원달러).

읽기 전용. ★KIS 해외지수/환율은 공유 rate limiter 경유(P2 market_index 패턴).
TTL 캐시(6h, 하루 1~2회 실조회) — 신규 폴링 0. *종목별 graceful-fail*: 막히는 칸만
available=false 로 표시(전체 실패 아님). 추천/판단 문구 0 — 사실(값·등락률·시각)만.

★모의(paper) 호스트에서 해외지수 조회가 막힐 수 있음(국내 FHPUP02100000 전례).
  막히면 해당 칸 available=false, 어떤 호출이 막혔는지 로그 — 대체 소스(yfinance 등)는
  운영자 결정 전까지 추가하지 않는다(멋대로 외부 API 금지).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from app.risk.daily_pnl import today_kst

_log = logging.getLogger("autotrade.briefing")

_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None}

# (key, 라벨, yfinance 티커). V5: 소스 = yfinance(운영자 승인) — KIS 해외지수는 모의
#   호스트에서 차단되어 graceful-fail 만 됐다.
INSTRUMENTS = [
    {"key": "sp500",  "label": "S&P500", "yf": "^GSPC"},
    {"key": "nasdaq", "label": "나스닥",  "yf": "^IXIC"},
    {"key": "dow",    "label": "다우",    "yf": "^DJI"},
    {"key": "sox",    "label": "필라델피아 반도체", "yf": "^SOX"},
    {"key": "usdkrw", "label": "원달러",  "yf": "USDKRW=X"},
]


def _now_hm_kst() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M")


def _yf_one(ticker: str) -> dict | None:
    """yfinance fast_info → {value, change_pct}. 동기 호출(스레드에서 실행)."""
    import yfinance as yf
    fi = yf.Ticker(ticker).fast_info
    last = float(getattr(fi, "last_price", None) or fi["lastPrice"])
    prev = float(getattr(fi, "previous_close", None) or fi["previousClose"])
    if last and prev:
        return {"value": last, "change_pct": (last - prev) / prev * 100.0}
    return None


async def _default_fetcher(instruments: list[dict]) -> dict[str, dict]:
    """V5: yfinance per-instrument 조회. 막히는 칸은 결과에서 누락 → available=false."""
    import asyncio
    out: dict[str, dict] = {}
    for ins in instruments:
        ticker = ins.get("yf")
        if not ticker:
            continue
        try:
            r = await asyncio.to_thread(_yf_one, ticker)
            if r:
                out[ins["key"]] = r
        except Exception as exc:  # noqa: BLE001 — 칸별 격리(전체 실패 아님)
            _log.warning("[briefing] %s(%s) yfinance 조회 실패: %s", ins["key"], ticker, exc)
    return out


async def get_briefing_markets(
    *, now_ts: float | None = None,
    fetcher: Callable[[list[dict]], Awaitable[dict[str, dict]]] | None = None,
) -> dict[str, Any]:
    now_ts = time.time() if now_ts is None else now_ts
    cached = _cache.get("data")
    if cached is not None and (now_ts - float(_cache.get("fetched_at", 0))) <= _CACHE_TTL_SECONDS:
        return cached

    fetch = fetcher or _default_fetcher
    hm = _now_hm_kst()
    try:
        fetched = await fetch(INSTRUMENTS)
    except Exception as exc:  # noqa: BLE001 — 전체 실패도 graceful(모든 칸 available=false)
        _log.warning("[briefing] markets fetch 전체 실패: %s", exc)
        fetched = {}

    markets = []
    for ins in INSTRUMENTS:
        got = fetched.get(ins["key"])
        if got and got.get("value"):
            markets.append({
                "key": ins["key"], "label": ins["label"],
                "value": round(float(got["value"]), 2),
                "change_pct": round(float(got.get("change_pct") or 0), 2),
                "asof_kst": hm, "available": True,
            })
        else:
            markets.append({
                "key": ins["key"], "label": ins["label"],
                "value": None, "change_pct": None, "asof_kst": hm, "available": False,
            })

    result = {
        "markets": markets,
        "asof_date_kst": today_kst().isoformat(),
        "note": "미국장 마감 기준 · KST",
        "is_live_authorization": False,
    }
    _cache["data"] = result
    _cache["fetched_at"] = now_ts
    return result


def reset_briefing_markets_cache_for_tests() -> None:
    _cache["fetched_at"] = 0.0
    _cache["data"] = None
