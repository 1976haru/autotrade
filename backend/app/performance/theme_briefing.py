"""테마 브리핑 2단계 — 전일 미국 테마 ETF/지수 등락률(정보 표시 전용).

읽기 전용. 추천/판단 문구 없음 — 사실(등락률·기준일·매핑 품질)만 노출한다.
한국 테마 ON/OFF를 자동 변경하지 않는다(`used_for_theme_toggle=False`).
매핑 출처: results/theme_filter/advanced_design.md §4.2 (kr-theme-v2, 20개 대분류).

★전체 fetch 실패(네트워크 등) 시 이전 성공 데이터를 유지한다(`stale=True`) —
과거엔 실패 시 전 종목 FETCH_ERROR로 덮어써 어제자 유효한 값까지 지웠다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from app.theme_filter.catalog import get_theme_catalog

_log = logging.getLogger("autotrade.briefing")

_CACHE_TTL_SECONDS = 12 * 3600
_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None}

# theme_id -> (mapping_quality, [ticker, ...]). 매핑 없음은 빈 리스트 + "NONE".
THEME_PROXIES: dict[str, tuple[str, list[str]]] = {
    "semiconductor":       ("DIRECT",  ["^SOX"]),
    "bio":                 ("PARTIAL", ["IBB"]),
    "automobile":          ("PARTIAL", ["DRIV"]),
    "finance":             ("DIRECT",  ["XLF"]),
    "secondary_battery":   ("DIRECT",  ["LIT"]),
    "internet":            ("DIRECT",  ["FDN"]),
    "defense":             ("DIRECT",  ["ITA"]),
    "shipbuilding":        ("NONE",    []),
    "power_nuclear":       ("PARTIAL", ["URA", "XLU"]),
    "entertainment_media": ("PARTIAL", ["XLC"]),
    "cosmetics":           ("NONE",    []),
    "food_beverage":       ("DIRECT",  ["PBJ"]),
    "steel_materials":     ("PARTIAL", ["SLX"]),
    "construction_infra":  ("PARTIAL", ["PAVE"]),
    "telecom_network":     ("DIRECT",  ["IYZ"]),
    "gaming":              ("DIRECT",  ["ESPO"]),
    "robot_ai":            ("DIRECT",  ["BOTZ"]),
    "retail_consumer":     ("DIRECT",  ["XRT"]),
    "energy_chemical":     ("PARTIAL", ["XLE", "XLB"]),
    "transport_logistics": ("PARTIAL", ["IYT"]),
    "other":               ("NONE",    []),
}


def _unique_tickers() -> list[str]:
    seen: list[str] = []
    for _, tickers in THEME_PROXIES.values():
        for t in tickers:
            if t not in seen:
                seen.append(t)
    return seen


def _yf_one(ticker: str) -> dict | None:
    """yfinance 일봉 2건 → 전일 대비 등락률 + 실제 미국 session 기준일.

    fast_info(당일 시세)가 아니라 history를 쓰는 이유: 브리핑은 '전일' 값이 필요하고
    session 날짜를 표시에 함께 써야 하므로(옛 데이터 오인 방지) 봉의 index 날짜가 필요하다.
    """
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
    if hist is None or len(hist) < 2:
        return None
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    if not prev:
        return None
    idx = closes.index[-1]
    session_date = idx.date().isoformat() if hasattr(idx, "date") else str(idx)
    return {"change_pct": (last - prev) / prev * 100.0, "session_date": session_date}


async def _default_fetcher(tickers: list[str]) -> dict[str, dict]:
    """ticker별 graceful 조회. 막히는 칸은 결과에서 누락 → FETCH_ERROR로 표시."""
    import asyncio
    out: dict[str, dict] = {}
    for ticker in tickers:
        try:
            r = await asyncio.to_thread(_yf_one, ticker)
            if r:
                out[ticker] = r
        except Exception as exc:  # noqa: BLE001 — ticker별 격리(전체 실패 아님)
            _log.warning("[theme-briefing] %s yfinance 조회 실패: %s", ticker, exc)
    return out


async def get_theme_briefing(
    *, now_ts: float | None = None,
    fetcher: Callable[[list[str]], Awaitable[dict[str, dict]]] | None = None,
) -> dict[str, Any]:
    now_ts = time.time() if now_ts is None else now_ts
    cached = _cache.get("data")
    if cached is not None and (now_ts - float(_cache.get("fetched_at", 0))) <= _CACHE_TTL_SECONDS:
        return cached

    fetch = fetcher or _default_fetcher
    try:
        fetched = await fetch(_unique_tickers())
    except Exception as exc:  # noqa: BLE001 — 전체 실패도 graceful(모든 칸 FETCH_ERROR)
        _log.warning("[theme-briefing] themes fetch 전체 실패: %s", exc)
        fetched = {}

    if not fetched and cached is not None:
        # ★전체 fetch 실패(네트워크 등) + 이전 성공 데이터 있음 → 이전 값 유지.
        #   캐시(_cache)는 갱신하지 않는다 — fetched_at을 그대로 둬서 다음 호출에서
        #   다시 시도하게 한다(실패 상태로 12h TTL 내내 고착되는 것 방지).
        _log.error("[theme-briefing] 전체 fetch 실패 — 이전 데이터 유지(stale)")
        return {**cached, "stale": True, "stale_reason": "FETCH_FAILED"}

    session_dates = sorted({v["session_date"] for v in fetched.values() if v.get("session_date")})
    session_date_us = session_dates[-1] if session_dates else None

    catalog = get_theme_catalog()
    themes = []
    for theme in catalog.themes:
        quality, tickers = THEME_PROXIES.get(theme.id, ("NONE", []))
        if not tickers:
            themes.append({
                "theme_id": theme.id, "label": theme.label,
                "mapping_quality": "NONE", "proxies": [], "status": "NO_MAPPING",
            })
            continue
        proxies = []
        any_ok = False
        for ticker in tickers:
            got = fetched.get(ticker)
            if got and got.get("change_pct") is not None:
                proxies.append({
                    "ticker": ticker, "change_pct": round(float(got["change_pct"]), 2), "status": "OK",
                })
                any_ok = True
            else:
                proxies.append({"ticker": ticker, "change_pct": None, "status": "FETCH_ERROR"})
        themes.append({
            "theme_id": theme.id, "label": theme.label,
            "mapping_quality": quality, "proxies": proxies,
            "status": "OK" if any_ok else "FETCH_ERROR",
        })

    result = {
        "session_date_us": session_date_us,
        "themes": themes,
        "used_for_order": False,
        "used_for_theme_toggle": False,
        "is_live_authorization": False,
        "stale": False,
    }
    _cache["data"] = result
    _cache["fetched_at"] = now_ts
    return result


def reset_theme_briefing_cache_for_tests() -> None:
    _cache["fetched_at"] = 0.0
    _cache["data"] = None
