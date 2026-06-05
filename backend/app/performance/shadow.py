"""AG2: 기각 신호 그림자 추적 — *읽기 전용 집계* + 방식 B(일별 종가 1회 적재).

기각된 매수 신호(council BUY 신호였으나 미진입)의 종목이 그 후 어떻게 됐나를 추적.
  가상 손익 = (추적 시점 가격 − 기각 시점 가격) × 가상 수량(종목당 투자금 기준).
  양수 = '놓친 이익', 음수 = '회피한 손실'.

★가격 소스(방식 B): 기각 *고유 종목*의 종가를 *하루 1회* 공유 rate limiter 경유로
  적재(market_index 패턴). 호출 수 = 활성 추적 윈도 내 고유 종목 수(소량). 신규
  '폴링' 0 — TTL 캐시로 매 요청 실조회 금지. limiter 우회 신설 0.

추적 기간: 기각 후 N영업일(_DEFAULT_TRACK_DAYS=5, 상수) — 무한 추적 금지.
정직성: 추적 미완(N일 미경과)=추적 중, 가격 미확보=제외(+제외 수).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from app.core.runtime_config import effective_per_stock_budget
from app.db.models import AgentDecisionLog
from app.risk.daily_pnl import KST, today_kst

_log = logging.getLogger("autotrade.shadow")

_DEFAULT_TRACK_DAYS = 5            # 기각 후 N 영업일
_CACHE_TTL_SECONDS = 6 * 3600     # 하루 ~2회 수준(매 요청 실조회 아님)
_HISTORY_FILENAME = "shadow_prices.json"

_cache: dict[str, Any] = {"fetched_at": 0.0}


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
        _log.warning("[shadow] %s 읽기 실패 — 빈 이력: %s", path, exc)
        return {}


def _save_closes(day: date, prices: dict[str, float]) -> None:
    hist = _load_history()
    key = day.isoformat()
    for sym, px in prices.items():
        if px:
            hist.setdefault(sym, {})[key] = float(px)
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.warning("[shadow] %s 저장 실패: %s", _history_path(), exc)


def _add_business_days(d: date, n: int) -> date:
    cur, added = d, 0
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:   # 월~금
            added += 1
    return cur


def _kst_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def _rejected_signals(db) -> list[dict[str, Any]]:
    """council BUY 신호였으나 미진입(broker_order_sent=False)인 기각 결정.

    AG1 의 signal_price(기각 시점 가격) 보유분만(없으면 가상손익 불가 → 제외)."""
    out = []
    rows = db.query(AgentDecisionLog).filter(AgentDecisionLog.meta.isnot(None)).all()
    for r in rows:
        meta = r.meta or {}
        final = str(meta.get("final_action", "") or "").upper()
        has_buy_vote = any(str(v.get("signal", "")).upper() == "BUY" for v in (meta.get("votes") or []))
        if not (final == "BUY" or has_buy_vote):
            continue
        if bool(meta.get("broker_order_sent")):
            continue  # 진입함 → 기각 아님
        price = int(meta.get("signal_price") or 0)
        if price <= 0:
            continue
        out.append({
            "symbol":        r.symbol,
            "signal_price":  price,
            "rejected_date": _kst_date(r.created_at),
        })
    return out


async def _default_price_fetcher(symbols: list[str]) -> dict[str, float]:
    """방식 B: 고유 종목 종가 — 공유 limiter 경유(broker.get_price)."""
    from app.api.deps import get_broker
    broker = get_broker()
    out: dict[str, float] = {}
    for sym in symbols:
        try:
            q = await broker.get_price(sym)
            out[sym] = float(getattr(q, "price", 0) or 0)
        except Exception:  # noqa: BLE001 — 일부 실패는 나머지 진행(가격 미확보 처리).
            continue
    return out


async def refresh_shadow_prices(
    db, *, now_ts: float | None = None,
    price_fetcher: Callable[[list[str]], Awaitable[dict[str, float]]] | None = None,
    track_days: int = _DEFAULT_TRACK_DAYS,
) -> None:
    """활성 추적 윈도(기각 후 N영업일 이내) 고유 종목의 *오늘 종가*를 1회 적재."""
    now_ts = time.time() if now_ts is None else now_ts
    last = float(_cache.get("fetched_at", 0))
    if last > 0 and (now_ts - last) <= _CACHE_TTL_SECONDS:
        return  # 캐시 — 매 요청 실조회 금지(최초 1회는 적재)
    today = today_kst()
    active = set()
    for sig in _rejected_signals(db):
        if sig["rejected_date"] <= today <= _add_business_days(sig["rejected_date"], track_days):
            active.add(sig["symbol"])
    if not active:
        _cache["fetched_at"] = now_ts
        return
    fetch = price_fetcher or _default_price_fetcher
    prices = await fetch(sorted(active))
    _save_closes(today, prices)
    _cache["fetched_at"] = now_ts


def compute_shadow(db, *, start: date, end: date, today: date | None = None,
                   track_days: int = _DEFAULT_TRACK_DAYS) -> dict[str, Any]:
    """기간 내 기각 신호의 그림자 추적 집계. 가격 적재(refresh)는 호출자가 먼저 수행."""
    today = today or today_kst()
    hist = _load_history()
    budget = int(effective_per_stock_budget())

    rejected = [s for s in _rejected_signals(db) if start <= s["rejected_date"] <= end]
    tracking = 0
    price_unavailable = 0
    completed = []  # virtual_pnl 목록
    for s in rejected:
        target = _add_business_days(s["rejected_date"], track_days)
        if target > today:
            tracking += 1
            continue
        close = (hist.get(s["symbol"]) or {}).get(target.isoformat())
        if not close:
            price_unavailable += 1
            continue
        qty = max(1, budget // max(1, s["signal_price"]))
        vpnl = int((float(close) - s["signal_price"]) * qty)
        completed.append(vpnl)

    n_completed = len(completed)
    avoided_loss = -sum(v for v in completed if v < 0)   # 회피한 손실(양수로 표기)
    missed_gain = sum(v for v in completed if v > 0)     # 놓친 이익
    correct = sum(1 for v in completed if v <= 0)        # 기각이 옳았던(가상손익 ≤ 0)

    return {
        "rejected_count":    len(rejected),
        "tracking_count":    tracking,
        "completed_count":   n_completed,
        "price_unavailable_count": price_unavailable,
        "correct_rejection_count": correct,
        "correct_rate":      (round(correct / n_completed, 4) if n_completed else None),
        "avoided_loss_krw":  int(avoided_loss),
        "missed_gain_krw":   int(missed_gain),
        "track_days":        track_days,
        "no_data":           n_completed == 0,
        "small_sample":      0 < n_completed < 10,
        "period_start_kst":  start.isoformat(),
        "period_end_kst":    end.isoformat(),
    }


def reset_shadow_cache_for_tests() -> None:
    _cache["fetched_at"] = 0.0
