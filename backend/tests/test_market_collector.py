"""MarketDataCollector 통합 테스트 (#19).

검증:
- adapter 결과 → validate → sort → dedupe → BarCache.save
- coverage / missing rate 계산
- 빈 결과 처리
- 다중 symbol collect_many
- 1m 수집 + 5m 파생 집계
- should_block_new_buy: WebSocket reconnect / stale cache / fresh cache

실 broker API 호출 0건 — 본 테스트는 in-memory Mock adapter만 사용한다.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app.backtest.types import Bar
from app.db.models import MarketBar
from app.market.base import Interval, MarketDataAdapter
from app.market.candle_builder import CandleValidationError
from app.market.collector import MarketDataCollector, should_block_new_buy


def _run(coro):
    return asyncio.run(coro)


class _FakeAdapter(MarketDataAdapter):
    """테스트용 in-memory adapter — 외부 호출 0건.

    `responses[(symbol, start, end, interval)]` 매핑이 있으면 그 봉을 반환.
    없으면 `default`를 반환. 호출 시 인자를 `calls`에 기록해 검증 가능.
    """

    def __init__(self, default: list[Bar] | None = None):
        self.default = default or []
        self.responses: dict[tuple, list[Bar]] = {}
        self.calls: list[tuple] = []

    async def get_bars(self, symbol, start, end, interval=Interval.DAY_1):
        self.calls.append((symbol, start, end, interval))
        key = (symbol, start, end, interval)
        return list(self.responses.get(key, self.default))


def _bar(symbol, minute, c=100):
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 5, 18, 9, minute, tzinfo=timezone.utc),
        open=c, high=c + 5, low=c - 5, close=c, volume=1000,
    )


# ---------- collect ----------


def test_collect_empty_adapter_returns_zero(client):
    adapter = _FakeAdapter(default=[])
    collector = MarketDataCollector(adapter)

    with client.test_db_factory() as db:
        result = _run(collector.collect(
            symbol="005930",
            start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 18, 9, 4, tzinfo=timezone.utc),
            interval=Interval.MINUTE_1,
            db=db,
        ))

    assert result.fetched_count == 0
    assert result.saved_count   == 0
    assert result.expected_count == 5
    assert result.missing_count == 5
    assert result.missing_rate  == 1.0
    assert result.coverage_score == 0.0


def test_collect_persists_bars_to_market_bar_table(client):
    bars = [_bar("005930", m) for m in range(5)]
    adapter = _FakeAdapter(default=bars)
    collector = MarketDataCollector(adapter)

    with client.test_db_factory() as db:
        result = _run(collector.collect(
            symbol="005930",
            start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 18, 9, 4, tzinfo=timezone.utc),
            interval=Interval.MINUTE_1,
            db=db,
        ))

        from sqlalchemy import select
        rows = db.execute(select(MarketBar)).scalars().all()
        assert len(rows) == 5
        assert {r.interval for r in rows} == {"1m"}

    assert result.fetched_count   == 5
    assert result.saved_count     == 5
    assert result.coverage_score  == 100.0


def test_collect_validates_and_sorts_unsorted_input(client):
    bars = [_bar("005930", 4), _bar("005930", 0), _bar("005930", 2)]
    adapter = _FakeAdapter(default=bars)
    collector = MarketDataCollector(adapter)

    with client.test_db_factory() as db:
        _run(collector.collect(
            symbol="005930",
            start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 18, 9, 4, tzinfo=timezone.utc),
            interval=Interval.MINUTE_1,
            db=db,
        ))

        from sqlalchemy import select
        rows = db.execute(select(MarketBar).order_by(MarketBar.timestamp)).scalars().all()
        assert [r.timestamp.minute for r in rows] == [0, 2, 4]


def test_collect_invalid_ohlcv_raises(client):
    bad = Bar(
        symbol="005930",
        timestamp=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        open=200, high=110, low=90, close=105, volume=1000,  # open > high
    )
    adapter = _FakeAdapter(default=[bad])
    collector = MarketDataCollector(adapter)

    with client.test_db_factory() as db:
        with pytest.raises(CandleValidationError):
            _run(collector.collect(
                symbol="005930",
                start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
                end=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
                interval=Interval.MINUTE_1,
                db=db,
            ))


def test_collect_end_before_start_returns_empty():
    adapter = _FakeAdapter(default=[_bar("005930", 0)])
    collector = MarketDataCollector(adapter)

    result = _run(collector.collect(
        symbol="005930",
        start=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 18,  9, 0, tzinfo=timezone.utc),
        interval=Interval.MINUTE_1,
    ))
    assert result.fetched_count == 0
    assert adapter.calls == []   # adapter는 호출되지 않는다


def test_collect_without_db_does_not_save():
    bars = [_bar("005930", m) for m in range(3)]
    adapter = _FakeAdapter(default=bars)
    collector = MarketDataCollector(adapter)

    result = _run(collector.collect(
        symbol="005930",
        start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 18, 9, 2, tzinfo=timezone.utc),
        interval=Interval.MINUTE_1,
        db=None,
    ))
    assert result.fetched_count == 3
    assert result.saved_count   == 0


# ---------- collect_many ----------


def test_collect_many_iterates_symbols(client):
    adapter = _FakeAdapter()
    adapter.responses = {
        ("005930", datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
         datetime(2026, 5, 18, 9, 4, tzinfo=timezone.utc), Interval.MINUTE_1):
            [_bar("005930", m) for m in range(5)],
        ("000660", datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
         datetime(2026, 5, 18, 9, 4, tzinfo=timezone.utc), Interval.MINUTE_1):
            [_bar("000660", m) for m in range(3)],
    }
    collector = MarketDataCollector(adapter)

    with client.test_db_factory() as db:
        results = _run(collector.collect_many(
            symbols=["005930", "000660"],
            start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 18, 9, 4, tzinfo=timezone.utc),
            interval=Interval.MINUTE_1,
            db=db,
        ))

    assert [r.symbol for r in results] == ["005930", "000660"]
    assert results[0].coverage_score == 100.0
    assert results[1].coverage_score == 60.0   # 3/5


# ---------- 1m → 5m derivation ----------


def test_collect_and_aggregate_1m_to_5m(client):
    one_min_bars = [_bar("005930", m, c=100 + m) for m in range(10)]
    adapter = _FakeAdapter(default=one_min_bars)
    collector = MarketDataCollector(adapter)

    with client.test_db_factory() as db:
        one_min, five_min = _run(collector.collect_and_aggregate_1m_to_5m(
            symbol="005930",
            start=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 18, 9, 9, tzinfo=timezone.utc),
            db=db,
        ))

        from sqlalchemy import select
        rows = db.execute(select(MarketBar).order_by(
            MarketBar.interval, MarketBar.timestamp,
        )).scalars().all()
        intervals = {r.interval for r in rows}

    assert intervals == {"1m", "5m"}
    assert one_min.fetched_count  == 10
    assert five_min.fetched_count == 2  # 두 5분 버킷


# ---------- should_block_new_buy ----------


def test_should_block_new_buy_returns_false_when_fresh(client):
    """캐시에 최근 fetched_at이 있으면 차단하지 않는다."""
    with client.test_db_factory() as db:
        db.add(MarketBar(
            symbol="005930", interval="1m",
            timestamp=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            open=100, high=110, low=90, close=105, volume=1000,
            fetched_at=datetime(2026, 5, 18, 9, 0, 30, tzinfo=timezone.utc),
        ))
        db.commit()

        block, reason = should_block_new_buy(
            db, symbol="005930", interval="1m",
            max_age_seconds=60,
            now=datetime(2026, 5, 18, 9, 0, 45, tzinfo=timezone.utc),  # 15s 경과
        )
    assert block is False
    assert reason is None


def test_should_block_new_buy_blocks_when_stale(client):
    with client.test_db_factory() as db:
        db.add(MarketBar(
            symbol="005930", interval="1m",
            timestamp=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            open=100, high=110, low=90, close=105, volume=1000,
            fetched_at=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        ))
        db.commit()

        block, reason = should_block_new_buy(
            db, symbol="005930", interval="1m",
            max_age_seconds=60,
            now=datetime(2026, 5, 18, 9, 5, tzinfo=timezone.utc),  # 5분 경과
        )
    assert block is True
    assert "stale" in reason


def test_should_block_new_buy_blocks_when_no_cache(client):
    with client.test_db_factory() as db:
        block, reason = should_block_new_buy(
            db, symbol="005930", interval="1m",
            max_age_seconds=60,
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        )
    assert block is True
    assert "비어있" in reason


def test_should_block_new_buy_blocks_during_websocket_reconnect(client):
    with client.test_db_factory() as db:
        # 캐시가 fresh하더라도 reconnect 중이면 차단.
        db.add(MarketBar(
            symbol="005930", interval="1m",
            timestamp=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            open=100, high=110, low=90, close=105, volume=1000,
            fetched_at=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        ))
        db.commit()

        block, reason = should_block_new_buy(
            db, symbol="005930", interval="1m",
            max_age_seconds=60,
            websocket_reconnecting=True,
            now=datetime(2026, 5, 18, 9, 0, 10, tzinfo=timezone.utc),
        )
    assert block is True
    assert "WebSocket" in reason
