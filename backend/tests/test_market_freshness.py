"""Data freshness 통합 helper 테스트 (#20).

검증 영역:
- quote freshness (fresh / stale / missing / disabled / future / naive / aware)
- reason 문구 (사람이 읽을 수 있음)
- bar cache freshness (staleness.py 재사용)
- DataFeedState (reconnecting / disconnected / missing / stale / fresh)
- RiskManager 기존 stale guard 회귀 (변화 없음 검증)

CI 정책: 외부 네트워크 호출 0건. 모든 시간 인자는 명시 (`now=...`).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import Balance, OrderRequest, OrderSide, OrderType
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import MarketBar
from app.market.freshness import (
    DataFeedState,
    FreshnessStatus,
    freshness_reason,
    is_bar_stale,
    is_quote_stale,
    should_block_buy_for_bar,
    should_block_buy_for_feed,
    should_block_buy_for_quote,
)
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _session_factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------- Quote freshness — basic ----------


def test_quote_fresh_under_threshold():
    status = is_quote_stale(
        symbol="005930", last_seen_at=_NOW - timedelta(seconds=10),
        max_age_seconds=60, now=_NOW,
    )
    assert isinstance(status, FreshnessStatus)
    assert status.is_stale is False
    assert status.age_seconds == 10.0
    assert status.reason is None


def test_quote_stale_when_age_exceeds_threshold():
    status = is_quote_stale(
        symbol="005930", last_seen_at=_NOW - timedelta(seconds=120),
        max_age_seconds=60, now=_NOW,
    )
    assert status.is_stale is True
    assert "stale" in status.reason
    assert status.age_seconds == 120.0


def test_quote_missing_last_seen_is_stale():
    status = is_quote_stale(
        symbol="005930", last_seen_at=None,
        max_age_seconds=60, now=_NOW,
    )
    assert status.is_stale is True
    assert status.age_seconds is None
    assert "missing" in status.reason


def test_quote_max_age_zero_disables_time_check():
    """max_age_seconds<=0이면 last_seen_at만 있으면 fresh."""
    status = is_quote_stale(
        symbol="005930", last_seen_at=_NOW - timedelta(seconds=99999),
        max_age_seconds=0, now=_NOW,
    )
    assert status.is_stale is False
    assert status.reason is None


def test_quote_max_age_zero_still_flags_missing():
    status = is_quote_stale(
        symbol="005930", last_seen_at=None,
        max_age_seconds=0, now=_NOW,
    )
    assert status.is_stale is True


def test_quote_future_timestamp_clamped_to_zero_age():
    """clock skew로 last_seen_at이 미래로 와도 음수 age가 되지 않게 clamp."""
    status = is_quote_stale(
        symbol="005930", last_seen_at=_NOW + timedelta(seconds=30),
        max_age_seconds=60, now=_NOW,
    )
    assert status.age_seconds == 0.0
    assert status.is_stale is False


def test_quote_naive_datetime_treated_as_utc():
    naive = datetime(2026, 5, 20, 11, 59, 30)  # _NOW - 30s in UTC
    status = is_quote_stale(
        symbol="005930", last_seen_at=naive,
        max_age_seconds=60, now=_NOW,
    )
    assert status.is_stale is False
    assert status.age_seconds == 30.0


def test_quote_aware_non_utc_datetime():
    """KST(+09:00) tz-aware도 안전하게 처리."""
    kst = timezone(timedelta(hours=9))
    last_seen = (_NOW - timedelta(seconds=20)).astimezone(kst)
    status = is_quote_stale(
        symbol="005930", last_seen_at=last_seen,
        max_age_seconds=60, now=_NOW,
    )
    assert status.is_stale is False
    assert status.age_seconds == 20.0


# ---------- Reason ----------


def test_freshness_reason_fresh_returns_none():
    assert freshness_reason(
        source="quote", is_stale=False, has_timestamp=True,
        age_seconds=10.0, max_age_seconds=60,
    ) is None


def test_freshness_reason_missing_data_message():
    msg = freshness_reason(
        source="bar:1m", is_stale=True, has_timestamp=False,
        age_seconds=None, max_age_seconds=60,
    )
    assert "missing" in msg
    assert "bar:1m" in msg


def test_freshness_reason_stale_includes_age_and_threshold():
    msg = freshness_reason(
        source="quote", is_stale=True, has_timestamp=True,
        age_seconds=120.0, max_age_seconds=60,
    )
    assert "120" in msg
    assert "60" in msg
    assert "stale" in msg


# ---------- Bar cache wrapper ----------


def test_is_bar_stale_returns_status_with_no_cache():
    """캐시에 row가 없으면 stale (안전 측)."""
    Session = _session_factory()
    with Session() as db:
        status = is_bar_stale(
            db, symbol="005930", interval="1m",
            max_age_seconds=60, now=_NOW,
        )
    assert status.is_stale is True
    assert status.age_seconds is None
    assert "missing" in status.reason


def test_is_bar_stale_fetched_recently_returns_fresh():
    Session = _session_factory()
    with Session() as db:
        db.add(MarketBar(
            symbol="005930", interval="1m",
            timestamp=_NOW - timedelta(minutes=1),
            open=100, high=110, low=90, close=105, volume=1000,
            fetched_at=_NOW - timedelta(seconds=10),
        ))
        db.commit()

        status = is_bar_stale(
            db, symbol="005930", interval="1m",
            max_age_seconds=60, now=_NOW,
        )
    assert status.is_stale is False
    assert status.age_seconds == 10.0


def test_is_bar_stale_fetched_long_ago_returns_stale():
    Session = _session_factory()
    with Session() as db:
        db.add(MarketBar(
            symbol="005930", interval="1m",
            timestamp=_NOW - timedelta(hours=1),
            open=100, high=110, low=90, close=105, volume=1000,
            fetched_at=_NOW - timedelta(minutes=5),
        ))
        db.commit()

        status = is_bar_stale(
            db, symbol="005930", interval="1m",
            max_age_seconds=60, now=_NOW,
        )
    assert status.is_stale is True
    assert "stale" in status.reason


# ---------- DataFeedState ----------


def test_feed_reconnecting_blocks_buy():
    feed = DataFeedState(connected=True, reconnecting=True,
                         last_message_at=_NOW - timedelta(seconds=5))
    block, reason, status = should_block_buy_for_feed(
        symbol="005930", feed=feed, max_age_seconds=60, now=_NOW,
    )
    assert block is True
    assert "reconnecting" in reason
    assert status.is_stale is True


def test_feed_disconnected_blocks_buy():
    feed = DataFeedState(connected=False, reconnecting=False,
                         last_message_at=_NOW - timedelta(seconds=5))
    block, reason, _ = should_block_buy_for_feed(
        symbol="005930", feed=feed, max_age_seconds=60, now=_NOW,
    )
    assert block is True
    assert "disconnected" in reason


def test_feed_missing_message_blocks_buy():
    feed = DataFeedState(connected=True, reconnecting=False,
                         last_message_at=None)
    block, reason, _ = should_block_buy_for_feed(
        symbol="005930", feed=feed, max_age_seconds=60, now=_NOW,
    )
    assert block is True
    assert "missing" in reason


def test_feed_stale_message_blocks_buy():
    feed = DataFeedState(connected=True, reconnecting=False,
                         last_message_at=_NOW - timedelta(seconds=120))
    block, reason, _ = should_block_buy_for_feed(
        symbol="005930", feed=feed, max_age_seconds=60, now=_NOW,
    )
    assert block is True
    assert "stale" in reason


def test_feed_fresh_does_not_block():
    feed = DataFeedState(connected=True, reconnecting=False,
                         last_message_at=_NOW - timedelta(seconds=10))
    block, reason, status = should_block_buy_for_feed(
        symbol="005930", feed=feed, max_age_seconds=60, now=_NOW,
    )
    assert block is False
    assert reason is None
    assert status.is_stale is False


# ---------- should_block_buy wrappers ----------


def test_should_block_buy_for_quote_returns_status():
    block, reason, status = should_block_buy_for_quote(
        symbol="005930", last_seen_at=None,
        max_age_seconds=60, now=_NOW,
    )
    assert block is True
    assert reason is not None
    assert isinstance(status, FreshnessStatus)


def test_should_block_buy_for_bar_returns_status():
    Session = _session_factory()
    with Session() as db:
        block, reason, status = should_block_buy_for_bar(
            db, symbol="005930", interval="1m",
            max_age_seconds=60, now=_NOW,
        )
    assert block is True
    assert isinstance(status, FreshnessStatus)


# ---------- RiskManager regression — 기존 #143 stale guard 동작 유지 ----------


def _basic_order():
    return OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=1,
        order_type=OrderType.MARKET,
    )


def _balance(cash=10_000_000):
    return Balance(cash=cash, equity=cash, buying_power=cash, currency="KRW")


def test_risk_manager_rejects_stale_latest_price():
    """latest_price_timestamp가 오래되면 #143대로 REJECTED — 변경 없음.

    RiskManager는 datetime.now()를 내부에서 호출하므로 본 테스트는 실시간을
    기준으로 충분히 과거인 timestamp를 사용한다.
    """
    real_now = datetime.now(timezone.utc)
    rm = RiskManager(RiskPolicy(stale_price_max_age_seconds=60))
    result = rm.evaluate_order(
        order=_basic_order(),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=real_now - timedelta(seconds=300),  # 5분 전
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("stale" in r for r in result.reasons)


def test_risk_manager_does_not_reject_fresh_latest_price():
    """현재 시각 직전 timestamp는 stale 아님 — stale reason 없음."""
    real_now = datetime.now(timezone.utc)
    rm = RiskManager(RiskPolicy(stale_price_max_age_seconds=60))
    result = rm.evaluate_order(
        order=_basic_order(),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=real_now - timedelta(seconds=5),
    )
    # stale 사유로는 거부되지 않아야 함 (다른 사유로 거부될 가능성은 별개).
    stale_reasons = [r for r in result.reasons if "stale" in r]
    assert stale_reasons == []


def test_risk_manager_disables_stale_check_when_threshold_zero():
    """stale_price_max_age_seconds<=0이면 stale 검사 비활성."""
    real_now = datetime.now(timezone.utc)
    rm = RiskManager(RiskPolicy(stale_price_max_age_seconds=0))
    result = rm.evaluate_order(
        order=_basic_order(),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=real_now - timedelta(days=10),  # 매우 오래됨
    )
    stale_reasons = [r for r in result.reasons if "stale" in r]
    assert stale_reasons == []
