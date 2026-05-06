"""Market bar cache staleness (171, MUST).

143은 broker quote의 timestamp 기반 stale 검사를 다뤘다. 본 모듈은 같은
원칙을 *데이터 피드(MarketBar 캐시)*에 적용한다 — yfinance / KIS bar fetch
가 멈춘 상태에서 strategy가 stale 캐시로 신호를 만드는 사고 방지.

read-only 함수만 제공. 호출자가 결정 (route_order pre-check, 운영자 경보 등).
LiveStrategyEngine 통합은 별도 follow-up — 본 PR은 util + 운영자 도구.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketBar


def latest_bar_fetched_at(
    db:        Session,
    *,
    symbol:    str,
    interval:  str,
) -> datetime | None:
    """주어진 (symbol, interval)의 가장 최근 MarketBar의 fetched_at. 없으면 None."""
    stmt = (
        select(MarketBar.fetched_at)
        .where(MarketBar.symbol == symbol, MarketBar.interval == interval)
        .order_by(MarketBar.fetched_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def is_bar_cache_stale(
    db:                Session,
    *,
    symbol:            str,
    interval:          str,
    max_age_seconds:   int,
    now:               datetime | None = None,
) -> tuple[bool, float | None]:
    """봉 캐시가 stale인지. (stale, age_seconds) 반환.

    - max_age_seconds <= 0: 검사 비활성 → (False, None)
    - 캐시 row 없음: (True, None) — 데이터 자체가 없으면 stale로 분류 (안전 측)
    - age > max_age_seconds: (True, age) — stale
    - else: (False, age)

    naive datetime은 UTC로 가정 (DB가 _utcnow() 사용).
    """
    if max_age_seconds <= 0:
        return False, None
    if now is None:
        now = datetime.now(timezone.utc)

    fetched = latest_bar_fetched_at(db, symbol=symbol, interval=interval)
    if fetched is None:
        return True, None

    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = (now - fetched).total_seconds()
    return age > max_age_seconds, age


def stale_symbols(
    db:                Session,
    *,
    interval:          str,
    max_age_seconds:   int,
    now:               datetime | None = None,
) -> list[tuple[str, float | None]]:
    """동일 interval 캐시에서 stale인 symbol 리스트 (운영자 일괄 모니터링용).

    DB의 모든 distinct symbol을 walk — 캐시 규모가 크면 비용. 단타 운영
    환경(symbol 수십~수백)에서 부담 없는 비용.
    """
    if max_age_seconds <= 0:
        return []
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max_age_seconds)

    # 같은 symbol의 가장 최근 fetched_at만 본다.
    rows = db.execute(
        select(MarketBar.symbol, MarketBar.fetched_at)
        .where(MarketBar.interval == interval)
        .order_by(MarketBar.fetched_at.desc())
    ).all()

    seen: dict[str, datetime] = {}
    for symbol, fetched in rows:
        if symbol not in seen:
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            seen[symbol] = fetched

    out: list[tuple[str, float | None]] = []
    for symbol, fetched in seen.items():
        if fetched < cutoff:
            age = (now - fetched).total_seconds()
            out.append((symbol, age))
    out.sort(key=lambda x: x[1] or 0.0, reverse=True)  # 가장 오래된 순.
    return out
