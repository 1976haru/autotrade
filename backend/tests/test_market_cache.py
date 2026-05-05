from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest.types import Bar
from app.db.base import Base
from app.db.models import MarketBar
from app.market.cache import BarCache


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _bar(symbol: str, day_offset: int, close: int) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_offset),
        open=close, high=close + 100, low=close - 100, close=close, volume=1000,
    )


def test_get_empty_returns_empty_list():
    Session = _session()
    with Session() as db:
        cache = BarCache(db)
        bars = cache.get(
            "005930", "1d",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
        assert bars == []


def test_save_and_get_round_trip():
    Session = _session()
    with Session() as db:
        cache = BarCache(db)
        bars = [_bar("005930", i, 75_000 + i * 100) for i in range(5)]
        assert cache.save(bars, "1d") == 5

        loaded = cache.get(
            "005930", "1d",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
        assert loaded == bars


def test_get_filters_by_range_symbol_and_interval():
    Session = _session()
    with Session() as db:
        cache = BarCache(db)
        cache.save([_bar("005930", i, 75_000) for i in range(10)], "1d")
        cache.save([_bar("000660", i, 185_000) for i in range(10)], "1d")
        cache.save([_bar("005930", i, 75_500) for i in range(10)], "1h")

        loaded = cache.get(
            "005930", "1d",
            datetime(2026, 1, 3, tzinfo=timezone.utc),
            datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
        assert len(loaded) == 3
        assert all(b.symbol == "005930" for b in loaded)


def test_save_replaces_existing_rows_for_same_keys():
    Session = _session()
    with Session() as db:
        cache = BarCache(db)
        cache.save([_bar("005930", 0, 70_000)], "1d")
        cache.save([_bar("005930", 0, 80_000)], "1d")

        rows = db.execute(select(MarketBar)).scalars().all()
        assert len(rows) == 1
        assert rows[0].close == 80_000


def test_save_empty_is_noop():
    Session = _session()
    with Session() as db:
        cache = BarCache(db)
        assert cache.save([], "1d") == 0
        assert db.execute(select(MarketBar)).scalars().all() == []
