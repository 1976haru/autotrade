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

# (key, 라벨, KIS 해외 거래소코드 EXCD, 심볼). FX 는 별도 처리(kind=fx).
INSTRUMENTS = [
    {"key": "sp500",  "label": "S&P500", "kind": "index", "excd": "NAS", "symb": "SPX"},
    {"key": "nasdaq", "label": "나스닥",  "kind": "index", "excd": "NAS", "symb": "COMP"},
    {"key": "dow",    "label": "다우",    "kind": "index", "excd": "NYS", "symb": "DJI"},
    {"key": "sox",    "label": "필라델피아 반도체", "kind": "index", "excd": "NAS", "symb": "SOX"},
    {"key": "usdkrw", "label": "원달러",  "kind": "fx",    "excd": "",    "symb": "FX@KRW"},
]


def _now_hm_kst() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M")


async def _default_fetcher(instruments: list[dict]) -> dict[str, dict]:
    """KIS 해외지수 per-instrument 조회(공유 limiter 경유). 막히는 칸은 결과에서 누락
    → 호출자가 available=false 처리. FX/환율 전용 KIS 엔드포인트 미검증 → 현재 누락."""
    from app.api.deps import get_broker
    out: dict[str, dict] = {}
    broker = get_broker()
    client = getattr(broker, "_client", None) or getattr(broker, "client", None)
    inquire = getattr(client, "inquire_overseas_index", None)
    if inquire is None:
        return out
    for ins in instruments:
        if ins["kind"] != "index":
            continue  # FX 는 검증된 KIS 엔드포인트 미확보 → graceful-fail
        try:
            raw = await inquire(excd=ins["excd"], symb=ins["symb"])
            o = (raw or {}).get("output") or {}
            price = float(o.get("ovrs_nmix_prpr") or o.get("prpr") or 0)
            chg = float(o.get("prdy_ctrt") or 0)
            if price:
                out[ins["key"]] = {"value": price, "change_pct": chg}
        except Exception as exc:  # noqa: BLE001 — 칸별 격리(전체 실패 아님)
            _log.warning("[briefing] %s(%s/%s) 해외지수 조회 실패: %s",
                         ins["key"], ins["excd"], ins["symb"], exc)
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
