"""MarketBar cache staleness tests (171, MUST)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import MarketBar
from app.market.staleness import (
    is_bar_cache_stale,
    latest_bar_fetched_at,
    stale_symbols,
)


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _bar(symbol="005930", interval="1d", fetched_at=None,
          ts=None, close=100):
    return MarketBar(
        symbol=symbol, interval=interval,
        timestamp=ts or datetime(2026, 5, 1, tzinfo=timezone.utc),
        open=close, high=close + 1, low=close - 1,
        close=close, volume=10,
        fetched_at=fetched_at,
    )


# ---------- latest_bar_fetched_at ----------

def test_latest_returns_none_for_unknown_symbol():
    Session = _session()
    with Session() as db:
        assert latest_bar_fetched_at(
            db, symbol="UNKNOWN", interval="1d",
        ) is None


def test_latest_returns_most_recent_fetched():
    Session = _session()
    older  = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    newer  = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)
    with Session() as db:
        db.add_all([
            _bar(fetched_at=older, ts=datetime(2026, 4, 30, tzinfo=timezone.utc)),
            _bar(fetched_at=newer, ts=datetime(2026, 5, 1, tzinfo=timezone.utc)),
        ])
        db.commit()
        result = latest_bar_fetched_at(db, symbol="005930", interval="1d")
    assert result is not None
    assert result.replace(tzinfo=timezone.utc) == newer


def test_latest_separates_by_symbol_and_interval():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _bar(symbol="A", interval="1d", fetched_at=now - timedelta(hours=1)),
            _bar(symbol="A", interval="1m", fetched_at=now - timedelta(minutes=5)),
            _bar(symbol="B", interval="1d", fetched_at=now - timedelta(days=10)),
        ])
        db.commit()
        a_1d = latest_bar_fetched_at(db, symbol="A", interval="1d")
        a_1m = latest_bar_fetched_at(db, symbol="A", interval="1m")
        b_1d = latest_bar_fetched_at(db, symbol="B", interval="1d")
    # 각각 다른 시각.
    assert a_1d != a_1m
    assert b_1d != a_1d


# ---------- is_bar_cache_stale ----------

def test_stale_check_disabled_when_max_age_zero():
    Session = _session()
    with Session() as db:
        stale, age = is_bar_cache_stale(
            db, symbol="005930", interval="1d", max_age_seconds=0,
        )
    assert stale is False
    assert age is None


def test_stale_when_no_cache_row():
    """캐시 자체가 없으면 stale=True (안전 측)."""
    Session = _session()
    with Session() as db:
        stale, age = is_bar_cache_stale(
            db, symbol="UNKNOWN", interval="1d", max_age_seconds=60,
        )
    assert stale is True
    assert age is None


def test_fresh_cache_not_stale():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(_bar(fetched_at=now - timedelta(seconds=30)))
        db.commit()
        stale, age = is_bar_cache_stale(
            db, symbol="005930", interval="1d",
            max_age_seconds=60, now=now,
        )
    assert stale is False
    assert age is not None
    assert 25 <= age <= 35


def test_old_cache_stale():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(_bar(fetched_at=now - timedelta(seconds=120)))
        db.commit()
        stale, age = is_bar_cache_stale(
            db, symbol="005930", interval="1d",
            max_age_seconds=60, now=now,
        )
    assert stale is True
    assert age is not None
    assert age > 60


def test_naive_datetime_treated_as_utc():
    """SQLite는 timezone strip이라 read 시 naive — UTC로 가정."""
    Session = _session()
    now_utc = datetime.now(timezone.utc)
    naive_old = (now_utc - timedelta(seconds=120)).replace(tzinfo=None)
    with Session() as db:
        db.add(_bar(fetched_at=naive_old))
        db.commit()
        stale, age = is_bar_cache_stale(
            db, symbol="005930", interval="1d",
            max_age_seconds=60, now=now_utc,
        )
    assert stale is True


# ---------- stale_symbols ----------

def test_stale_symbols_empty_when_disabled():
    Session = _session()
    with Session() as db:
        assert stale_symbols(
            db, interval="1d", max_age_seconds=0,
        ) == []


def test_stale_symbols_lists_only_old():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _bar(symbol="A", fetched_at=now - timedelta(seconds=30)),   # fresh
            _bar(symbol="B", fetched_at=now - timedelta(seconds=120)),  # stale
            _bar(symbol="C", fetched_at=now - timedelta(seconds=300)),  # very stale
        ])
        db.commit()
        result = stale_symbols(
            db, interval="1d", max_age_seconds=60, now=now,
        )
    # B, C만 (A는 fresh).
    symbols = [s for s, _ in result]
    assert "A" not in symbols
    assert set(symbols) == {"B", "C"}
    # 가장 오래된 순.
    assert symbols[0] == "C"


def test_stale_symbols_uses_latest_per_symbol():
    """같은 symbol에 여러 row 있으면 가장 최근 fetched_at만 본다."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        # symbol A: 오래된 row + 최근 row.
        db.add_all([
            _bar(symbol="A", fetched_at=now - timedelta(days=10),
                  ts=datetime(2026, 4, 1, tzinfo=timezone.utc)),
            _bar(symbol="A", fetched_at=now - timedelta(seconds=30),
                  ts=datetime(2026, 5, 1, tzinfo=timezone.utc)),
        ])
        db.commit()
        result = stale_symbols(
            db, interval="1d", max_age_seconds=60, now=now,
        )
    # A의 최근 fetched는 30s ago — fresh — 결과에 없음.
    assert result == []
