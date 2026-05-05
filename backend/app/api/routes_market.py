from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_market_data
from app.backtest.types import Bar
from app.db.session import get_db
from app.market.base import Interval, MarketDataAdapter
from app.market.cache import BarCache

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
