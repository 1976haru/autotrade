from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_market_data
from app.backtest.types import Bar
from app.core.config import get_settings
from app.db.session import get_db
from app.market.base import Interval, MarketDataAdapter
from app.market.cache import BarCache
from app.market.freshness import is_bar_stale, is_quote_stale

router = APIRouter(prefix="/market", tags=["market-data"])


class BarOut(BaseModel):
    symbol:    str
    timestamp: datetime
    open:      int
    high:      int
    low:       int
    close:     int
    volume:    int


class BarsResponse(BaseModel):
    symbol:   str
    interval: str
    source:   str  # "cache" 또는 "upstream"
    count:    int
    bars:     list[BarOut]


def _to_out(b: Bar) -> BarOut:
    return BarOut(
        symbol=b.symbol, timestamp=b.timestamp,
        open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
    )


@router.get("/bars", response_model=BarsResponse)
async def get_bars(
    symbol:   str = Query(..., min_length=1),
    start:    datetime = Query(...),
    end:      datetime = Query(...),
    interval: Interval = Query(Interval.DAY_1),
    upstream: MarketDataAdapter = Depends(get_market_data),
    db:       Session = Depends(get_db),
) -> BarsResponse:
    if start > end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    cache = BarCache(db)
    cached = cache.get(symbol, interval.value, start, end)
    if cached:
        return BarsResponse(
            symbol=symbol, interval=interval.value,
            source="cache", count=len(cached),
            bars=[_to_out(b) for b in cached],
        )

    try:
        bars = await upstream.get_bars(symbol, start, end, interval)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cache.save(bars, interval.value)
    return BarsResponse(
        symbol=symbol, interval=interval.value,
        source="upstream", count=len(bars),
        bars=[_to_out(b) for b in bars],
    )


# ---------- Data Freshness (#20) ----------


class FreshnessResponse(BaseModel):
    symbol:          str
    source:          str
    is_stale:        bool
    age_seconds:     float | None = None
    last_seen_at:    datetime | None = None
    max_age_seconds: int
    reason:          str | None = None
    checked_at:      datetime


@router.get("/freshness", response_model=FreshnessResponse)
def get_freshness(
    symbol:          str = Query(..., min_length=1),
    source:          str = Query("quote", pattern="^(quote|bar)$"),
    last_seen_at:    datetime | None = Query(None),
    max_age_seconds: int | None      = Query(None, ge=0),
    interval:        Interval | None = Query(None),
    db:              Session         = Depends(get_db),
) -> FreshnessResponse:
    """Read-only freshness 상태. 신규 BUY 차단 의사결정의 근거가 되는 지표.

    `source=quote`: 호출자가 last_seen_at를 보내야 한다 (broker.get_price 응답
    timestamp 등). 미제공이면 missing으로 분류.

    `source=bar`: DB MarketBar 캐시의 fetched_at을 본다. interval 필수.

    실 broker API를 호출하지 않는다 — 단순 상태 계산 + DB 조회만.
    """
    settings = get_settings()
    threshold = (
        max_age_seconds if max_age_seconds is not None
        else settings.stale_price_max_age_seconds
    )
    now = datetime.now(timezone.utc)

    if source == "quote":
        status = is_quote_stale(
            symbol=symbol, last_seen_at=last_seen_at,
            max_age_seconds=threshold, now=now,
        )
    elif source == "bar":
        if interval is None:
            raise HTTPException(
                status_code=400,
                detail="interval is required when source=bar",
            )
        status = is_bar_stale(
            db, symbol=symbol, interval=interval.value,
            max_age_seconds=threshold, now=now,
        )
    else:  # pattern guard above prevents this, defensive only
        raise HTTPException(status_code=400, detail=f"unsupported source: {source}")

    return FreshnessResponse(
        symbol=status.symbol,
        source=status.source,
        is_stale=status.is_stale,
        age_seconds=status.age_seconds,
        last_seen_at=status.last_seen_at,
        max_age_seconds=status.max_age_seconds,
        reason=status.reason,
        checked_at=status.checked_at,
    )
