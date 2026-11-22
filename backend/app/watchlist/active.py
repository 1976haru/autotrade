"""활성 watchlist → 스캔 유니버스 종목 (read-only).

universe_mode="watchlist" 일 때 `_scan_universe_symbols` 가 호출. 활성(is_active)
watchlist 의 종목 코드를 반환한다. *후보군*일 뿐 주문 신호가 아니며, broker /
route_order / RiskManager 를 호출하지 않는다(읽기 전용).

- 활성 watchlist 가 여러 개면 가장 최근 생성분 1개.
- 활성 없음 / 비어있음 / 조회 실패 → 빈 리스트(호출부가 auto 폴백).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import Watchlist, WatchlistItem
from app.db.session import SessionLocal

_log = logging.getLogger("autotrade.watchlist.active")


def get_active_watchlist_symbols() -> list[str]:
    """활성 watchlist 의 종목 코드 리스트(중복 제거, 등록 순). 없으면 빈 리스트."""
    try:
        with SessionLocal() as db:
            wl = db.execute(
                select(Watchlist)
                .where(Watchlist.is_active.is_(True))
                .order_by(Watchlist.created_at.desc())
            ).scalars().first()
            if wl is None:
                return []
            rows = db.execute(
                select(WatchlistItem.symbol)
                .where(WatchlistItem.watchlist_id == wl.id)
                .order_by(WatchlistItem.created_at.asc())
            ).scalars().all()
            seen: set[str] = set()
            out: list[str] = []
            for s in rows:
                c = str(s or "").strip()
                if c and c not in seen:
                    seen.add(c)
                    out.append(c)
            return out
    except Exception as exc:  # noqa: BLE001 — 조회 실패는 빈 리스트(auto 폴백), raise 금지.
        _log.warning("[watchlist] active 종목 조회 실패(무시→auto 폴백): %s", exc)
        return []


__all__ = ["get_active_watchlist_symbols"]
