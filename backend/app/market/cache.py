from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.backtest.types import Bar
from app.db.models import MarketBar


def _to_bar(row: MarketBar) -> Bar:
    """SQLite DateTime is naive on read; the cache contract treats stored timestamps as UTC."""
    ts = row.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Bar(
        symbol=row.symbol,
        timestamp=ts,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )


class BarCache:
    """`MarketBar` 테이블을 (symbol, interval) 단위로 read/save."""

    def __init__(self, db: Session):
        self.db = db

    def get(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[Bar]:
        rows = self.db.execute(
            select(MarketBar)
            .where(
                MarketBar.symbol == symbol,
                MarketBar.interval == interval,
                MarketBar.timestamp >= start,
                MarketBar.timestamp <= end,
            )
            .order_by(MarketBar.timestamp)
        ).scalars().all()
        return [_to_bar(r) for r in rows]

    def save(self, bars: list[Bar], interval: str) -> int:
        if not bars:
            return 0
        by_symbol: dict[str, list[Bar]] = {}
        for b in bars:
            by_symbol.setdefault(b.symbol, []).append(b)
        for symbol, sym_bars in by_symbol.items():
            timestamps = [b.timestamp for b in sym_bars]
            self.db.execute(
                delete(MarketBar).where(
                    MarketBar.symbol == symbol,
                    MarketBar.interval == interval,
                    MarketBar.timestamp.in_(timestamps),
                )
            )
            for b in sym_bars:
                self.db.add(MarketBar(
                    symbol=b.symbol,
                    interval=interval,
                    timestamp=b.timestamp,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                ))
        self.db.commit()
        return len(bars)
