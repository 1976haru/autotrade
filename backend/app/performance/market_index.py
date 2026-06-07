"""P2: 코스피/코스닥 지수 비교 — KIS 조회(공유 rate limiter 경유) + 캐싱 + 일별 종가 적재.

원칙:
  - ★KIS 지수 조회는 *반드시* 계좌 단위 공유 rate limiter(Step 3) 를 경유한다 —
    KisClient 가 `_throttle()` 에서 한 limiter 를 호출(deps/lazy 가 모두 공유 주입).
  - 캐싱: TTL 기반 — 하루 2회 갱신 수준. *매 요청 실조회 금지*.
  - 일별 지수 종가는 로컬 파일(`market_index_closes.json`)에 적재 — *유일한*
    reader/writer 는 본 모듈. 조회한 날 저장 → 과거 기준일 재조회 불필요.
  - 조회 실패 시 비교 영역만 unavailable — 봇 성과 표시는 호출자가 정상 유지.

읽기 전용. 주문/봇/리스크/config 쓰기 0건.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from app.risk.daily_pnl import KST, today_kst

_log = logging.getLogger("autotrade.market_index")

INDEX_CODES = {"KOSPI": "0001", "KOSDAQ": "1001"}
_CACHE_TTL_SECONDS = 6 * 3600  # 하루 ~2회 갱신 수준(매 요청 실조회 아님)
_HISTORY_FILENAME = "market_index_closes.json"

# 인메모리 캐시.
_cache: dict[str, Any] = {"fetched_at": 0.0, "quotes": None, "equity": None}


def _data_dir() -> Path:
    url = str(get_settings().database_url or "")
    if url.startswith("sqlite:///"):
        p = Path(url[len("sqlite:///"):])
        return p.parent if p.suffix else p
    return Path("./data")


def _history_path() -> Path:
    return _data_dir() / _HISTORY_FILENAME


def _load_history() -> dict[str, dict[str, float]]:
    path = _history_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        _log.warning("[market_index] %s 읽기 실패 — 빈 이력으로 진행: %s", path, exc)
        return {}


def _save_close(day: date, quotes: dict[str, Any]) -> None:
    """조회한 날의 지수 종가를 적재(있으면 갱신). 본 모듈만 write.

    V2: 휴장일(주말)은 적재하지 않는다 — 직전 세션 값을 휴장일자로 잘못 적재하면
    다기간 기준일 종가가 오염된다(기준일은 거래일이어야 함)."""
    if day.weekday() >= 5:  # 토(5)/일(6)
        return
    hist = _load_history()
    key = day.isoformat()
    for name in INDEX_CODES:
        v = quotes.get(name, {}).get("value")
        if v is not None:
            hist.setdefault(name, {})[key] = float(v)
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.warning("[market_index] %s 저장 실패: %s", _history_path(), exc)


async def _default_index_fetcher() -> dict[str, dict[str, float]]:
    """KIS 지수 조회 — 공유 limiter 경유(client._throttle)."""
    from app.api.deps import get_broker
    broker = get_broker()
    client = getattr(broker, "client", None)
    if client is None:
        raise RuntimeError("KIS client 없음 — 지수 조회 불가")
    out: dict[str, dict[str, float]] = {}
    for name, code in INDEX_CODES.items():
        raw = await client.inquire_index_price(code)
        o = (raw.get("output") or {}) if isinstance(raw, dict) else {}
        out[name] = {
            "value": float(o.get("bstp_nmix_prpr")),
            "change_pct": float(o.get("bstp_nmix_prdy_ctrt")),
        }
    return out


async def _default_equity_fetcher() -> int | None:
    """KIS 평가자산(공유 limiter 경유). 실패는 None(비교 수익률만 영향)."""
    try:
        from app.api.deps import get_broker
        bal = await get_broker().get_balance()
        return int(getattr(bal, "equity", 0) or 0) or None
    except Exception:  # noqa: BLE001
        return None


async def _refresh(now_ts: float,
                   index_fetcher: Callable[[], Awaitable[dict]],
                   equity_fetcher: Callable[[], Awaitable[int | None]]) -> None:
    quotes = await index_fetcher()       # 실패 시 예외 → 호출자에서 unavailable.
    equity = await equity_fetcher()
    _cache.update(fetched_at=now_ts, quotes=quotes, equity=equity)
    _save_close(today_kst(), quotes)


def _fetched_at_kst() -> str | None:
    ts = _cache.get("fetched_at") or 0
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KST).strftime("%H:%M")


async def get_market_comparison_context(
    *,
    start: date,
    end: date,
    now_ts: float | None = None,
    index_fetcher: Callable[[], Awaitable[dict]] | None = None,
    equity_fetcher: Callable[[], Awaitable[int | None]] | None = None,
) -> dict[str, Any]:
    """기간 [start, end] 의 지수 수익률 + 현재 평가자산. 캐시 우선, 실패 시 unavailable."""
    now_ts = time.time() if now_ts is None else now_ts
    idx_f = index_fetcher or _default_index_fetcher
    eq_f = equity_fetcher or _default_equity_fetcher

    try:
        if _cache["quotes"] is None or (now_ts - float(_cache["fetched_at"])) > _CACHE_TTL_SECONDS:
            await _refresh(now_ts, idx_f, eq_f)
    except Exception as exc:  # noqa: BLE001 — 조회 실패는 비교 영역만 영향.
        _log.warning("[market_index] 지수 조회 실패 — 비교 미표시: %s", exc)
        return {"market": {"available": False, "reason": "MARKET_FETCH_FAILED"},
                "current_equity_krw": _cache.get("equity")}

    quotes = _cache.get("quotes")
    if not quotes:
        return {"market": {"available": False, "reason": "MARKET_FETCH_FAILED"},
                "current_equity_krw": _cache.get("equity")}

    today = today_kst()
    hist = _load_history()
    # V2: 휴장일(주말)엔 prdy_ctrt 가 *직전 세션*(예: 금요일) 변동률이라 '오늘'로 쓰면
    #   오표기 — 오늘은 거래가 없으니 0%가 정답. (period 의 today 기준으로 판정.)
    market_closed_today = today.weekday() >= 5  # 토(5)/일(6)

    def _ret(name: str) -> float | None:
        q = quotes.get(name) or {}
        if start == end == today:
            if market_closed_today:
                return 0.0   # 휴장: 오늘 변동 없음(직전 세션 prdy_ctrt 오표기 방지)
            # 당일(개장): 전일대비율(오늘 변동%) 직접 사용 — 과거 이력 불필요.
            cp = q.get("change_pct")
            return round(float(cp), 2) if cp is not None else None
        # 다기간: 기간 시작일 종가(적재분) → 최신 지수.
        start_close = (hist.get(name) or {}).get(start.isoformat())
        cur = q.get("value")
        if start_close and start_close > 0 and cur is not None:
            return round((float(cur) - float(start_close)) / float(start_close) * 100, 2)
        return None  # 기준일 종가 미적재 → 비교 불가(부분).

    return {
        "market": {
            "available": True,
            "kospi_return_pct": _ret("KOSPI"),
            "kosdaq_return_pct": _ret("KOSDAQ"),
            "market_closed_today": market_closed_today,
            "fetched_at_kst": _fetched_at_kst(),
        },
        "current_equity_krw": _cache.get("equity"),
    }


def reset_market_index_cache_for_tests() -> None:
    _cache.update(fetched_at=0.0, quotes=None, equity=None)


__all__ = [
    "INDEX_CODES",
    "get_market_comparison_context",
    "reset_market_index_cache_for_tests",
]
