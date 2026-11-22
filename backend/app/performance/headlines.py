"""B2: 최신 경제 헤드라인 — RSS 상위 5개(제목+발행시각+원문 링크).

★선별·요약·재작성 0: 피드가 주는 순서·제목 *그대로*. 라벨은 "최신 경제 헤드라인"
(="AI 선별" 아님). 추천 문구 0. TTL 캐시(30분). RSS 실패 시 옛 캐시가 있으면
"HH:MM 기준" 명시 후 표시, 없으면 available=false.

RSS 파서는 표준 라이브러리(xml.etree)만 — 외부 의존성 추가 0.
"""

from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable
from xml.etree import ElementTree as ET

_log = logging.getLogger("autotrade.headlines")

# 신뢰 가능한 경제뉴스 RSS — 연합뉴스 경제(접근성·안정성). 변경 시 보고 필요.
RSS_SOURCE_NAME = "연합뉴스 경제"
RSS_SOURCE_URL = "https://www.yna.co.kr/rss/economy.xml"

_CACHE_TTL_SECONDS = 30 * 60
_MAX_HEADLINES = 5
_cache: dict[str, Any] = {"fetched_at": 0.0, "headlines": None, "asof_kst": None}


def _now_hm_kst() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M")


def _to_kst_hm(pubdate: str | None) -> str | None:
    if not pubdate:
        return None
    try:
        from datetime import timedelta, timezone
        dt = parsedate_to_datetime(pubdate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=9))).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return None


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    """RSS XML → [{title, link, published_kst}] 상위 N. 피드 순서·제목 그대로(재작성 0)."""
    root = ET.fromstring(xml_text)
    items = root.findall(".//item")
    out: list[dict[str, Any]] = []
    for it in items[:_MAX_HEADLINES]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        if not title:
            continue
        out.append({"title": title, "link": link, "published_kst": _to_kst_hm(pub)})
    return out


async def _default_fetcher() -> str:
    import httpx
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        r = await client.get(RSS_SOURCE_URL, headers={"User-Agent": "autotrade-briefing/1.0"})
        r.raise_for_status()
        return r.text


async def get_headlines(
    *, now_ts: float | None = None,
    fetcher: Callable[[], Awaitable[str]] | None = None,
) -> dict[str, Any]:
    now_ts = time.time() if now_ts is None else now_ts
    cached = _cache.get("headlines")
    if cached is not None and (now_ts - float(_cache.get("fetched_at", 0))) <= _CACHE_TTL_SECONDS:
        return {"headlines": cached, "source": RSS_SOURCE_NAME, "asof_kst": _cache["asof_kst"],
                "available": True, "stale": False}

    fetch = fetcher or _default_fetcher
    hm = _now_hm_kst()
    try:
        xml_text = await fetch()
        headlines = parse_rss(xml_text)
        if not headlines:
            raise ValueError("RSS 에 헤드라인 없음")
        _cache.update({"headlines": headlines, "fetched_at": now_ts, "asof_kst": hm})
        return {"headlines": headlines, "source": RSS_SOURCE_NAME, "asof_kst": hm,
                "available": True, "stale": False}
    except Exception as exc:  # noqa: BLE001
        _log.warning("[headlines] RSS 조회 실패: %s", exc)
        if cached is not None:  # 옛 캐시 있으면 기준시각 명시 후 표시 허용
            return {"headlines": cached, "source": RSS_SOURCE_NAME, "asof_kst": _cache["asof_kst"],
                    "available": True, "stale": True}
        return {"headlines": [], "source": RSS_SOURCE_NAME, "asof_kst": hm,
                "available": False, "stale": False}


def reset_headlines_cache_for_tests() -> None:
    _cache.update({"fetched_at": 0.0, "headlines": None, "asof_kst": None})
