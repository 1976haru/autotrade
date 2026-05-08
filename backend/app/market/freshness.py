"""Data freshness 통합 helper (#20).

여러 데이터 소스(quote / tick / bar cache / WebSocket feed)의 stale 여부를
한 인터페이스로 통합한다. 본 모듈은 *조회만* — 신규 BUY 차단을 호출자가
선택할 수 있도록 (block, reason)을 반환한다.

설계 원칙:
- broker / RiskManager / PermissionGate / OrderExecutor를 import하지 않는다.
- 기존 staleness.py(MarketBar 캐시 stale)를 재사용 — 중복 구현 금지.
- 기존 RiskManager.evaluate_order의 latest_price_timestamp guard(143)는
  그대로 유지 — 본 모듈은 그 위에 *추가* freshness 신호를 제공한다.
- SELL/청산 신호는 *자동으로 차단되지 않는다* — 위험 축소 목적이므로
  호출자가 별도 정책으로 결정 (`should_block_buy_*` 함수만 제공).

CLAUDE.md 절대 원칙 — 본 모듈은 broker live order 호출 / LIVE flag
변경과 무관하며, frontend에 secret을 노출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.market.staleness import is_bar_cache_stale


# ---------- DTOs ----------


@dataclass(frozen=True)
class FreshnessStatus:
    """단일 데이터 소스의 freshness 평가 결과."""
    symbol:           str
    source:           str           # "quote" | "bar" | "feed"
    is_stale:         bool
    age_seconds:      float | None  # None: timestamp 자체가 없음
    last_seen_at:     datetime | None
    max_age_seconds:  int
    reason:           str | None
    checked_at:       datetime

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "source":          self.source,
            "is_stale":        self.is_stale,
            "age_seconds":     self.age_seconds,
            "last_seen_at":    self.last_seen_at.isoformat() if self.last_seen_at else None,
            "max_age_seconds": self.max_age_seconds,
            "reason":          self.reason,
            "checked_at":      self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class DataFeedState:
    """WebSocket / push feed의 연결 + 마지막 메시지 상태.

    실 KIS / Kiwoom WebSocket 통합은 Phase 2 — 본 PR에서는 외부 입력으로
    받아 freshness 판단에만 사용한다 (운영자가 health-check endpoint나
    내부 monitoring에서 채워주는 모델).
    """
    connected:        bool
    reconnecting:     bool = False
    last_message_at:  datetime | None = None


# ---------- helpers ----------


def _ensure_utc(ts: datetime) -> datetime:
    """naive datetime은 UTC로 가정. tz-aware은 UTC로 변환."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _age_seconds(now: datetime, ts: datetime | None) -> float | None:
    """now - ts (UTC). ts가 None이면 None. future timestamp는 0으로 clamp."""
    if ts is None:
        return None
    age = (now - _ensure_utc(ts)).total_seconds()
    return max(0.0, age)


def freshness_reason(*, source: str, is_stale: bool, has_timestamp: bool,
                     age_seconds: float | None, max_age_seconds: int) -> str | None:
    """사람이 읽을 수 있는 freshness reason 문구. fresh면 None.

    - timestamp 자체 없음 → "{source} data missing"
    - 시간 기준 검사 비활성(max_age_seconds<=0)인데 stale=True → "{source} data unavailable"
    - 일반 stale → "{source} data stale (Xs > Ys threshold)"
    """
    if not is_stale:
        return None
    if not has_timestamp:
        return f"{source} data missing (no timestamp recorded)"
    if max_age_seconds <= 0 or age_seconds is None:
        return f"{source} data unavailable"
    return (
        f"{source} data stale ({age_seconds:.0f}s > {max_age_seconds}s threshold)"
    )


# ---------- quote freshness ----------


def is_quote_stale(
    *,
    symbol:          str,
    last_seen_at:    datetime | None,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> FreshnessStatus:
    """단일 quote 또는 tick의 freshness 평가.

    - last_seen_at is None → is_stale=True (data missing)
    - max_age_seconds <= 0 → 시간 기준 검사 비활성. last_seen_at이 있으면
      is_stale=False (아무 정보도 없으면 stale로 분류 — 안전 측)
    - age > max_age_seconds → is_stale=True
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    if last_seen_at is None:
        return FreshnessStatus(
            symbol=symbol, source="quote",
            is_stale=True, age_seconds=None,
            last_seen_at=None, max_age_seconds=max_age_seconds,
            reason=freshness_reason(
                source="quote", is_stale=True, has_timestamp=False,
                age_seconds=None, max_age_seconds=max_age_seconds,
            ),
            checked_at=now,
        )

    age = _age_seconds(now, last_seen_at)
    # age가 None일 수는 없음 (위에서 last_seen_at None을 처리함) — type-narrow.
    assert age is not None

    if max_age_seconds <= 0:
        # 시간 기준은 비활성 — last_seen_at이 있으니 fresh.
        return FreshnessStatus(
            symbol=symbol, source="quote",
            is_stale=False, age_seconds=age,
            last_seen_at=last_seen_at, max_age_seconds=max_age_seconds,
            reason=None, checked_at=now,
        )

    stale = age > max_age_seconds
    return FreshnessStatus(
        symbol=symbol, source="quote",
        is_stale=stale, age_seconds=age,
        last_seen_at=last_seen_at, max_age_seconds=max_age_seconds,
        reason=freshness_reason(
            source="quote", is_stale=stale, has_timestamp=True,
            age_seconds=age, max_age_seconds=max_age_seconds,
        ),
        checked_at=now,
    )


# ---------- bar freshness (wraps existing staleness.py) ----------


def is_bar_stale(
    db:              Session,
    *,
    symbol:          str,
    interval:        str,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> FreshnessStatus:
    """봉 캐시 freshness — `staleness.is_bar_cache_stale`에 위임.

    기존 staleness.py 로직을 재사용해 중복 분기 방지. 결과를 `FreshnessStatus`
    로 통일해 호출자가 quote / bar / feed를 동형으로 처리.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    stale, age = is_bar_cache_stale(
        db, symbol=symbol, interval=interval,
        max_age_seconds=max_age_seconds, now=now,
    )
    has_ts = age is not None
    return FreshnessStatus(
        symbol=symbol, source=f"bar:{interval}",
        is_stale=stale, age_seconds=age,
        last_seen_at=None,  # staleness.py는 fetched_at 자체를 노출하지 않음 — 호출자는 latest_bar_fetched_at 별도 사용
        max_age_seconds=max_age_seconds,
        reason=freshness_reason(
            source=f"bar:{interval}", is_stale=stale, has_timestamp=has_ts,
            age_seconds=age, max_age_seconds=max_age_seconds,
        ),
        checked_at=now,
    )


# ---------- WebSocket / feed freshness ----------


def is_feed_stale(
    *,
    symbol:          str,
    feed:            DataFeedState,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> FreshnessStatus:
    """WebSocket / push feed 상태 평가.

    우선순위 (가장 강한 신호부터):
    1. reconnecting → stale, "data feed reconnecting"
    2. connected=False → stale, "data feed disconnected"
    3. last_message_at None → stale, "feed data missing"
    4. age > max_age_seconds → stale (max_age_seconds<=0이면 검사 비활성)
    5. 그 외 → fresh
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)

    if feed.reconnecting:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=True, age_seconds=None,
            last_seen_at=feed.last_message_at,
            max_age_seconds=max_age_seconds,
            reason="data feed reconnecting",
            checked_at=now,
        )
    if not feed.connected:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=True, age_seconds=None,
            last_seen_at=feed.last_message_at,
            max_age_seconds=max_age_seconds,
            reason="data feed disconnected",
            checked_at=now,
        )

    if feed.last_message_at is None:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=True, age_seconds=None,
            last_seen_at=None, max_age_seconds=max_age_seconds,
            reason=freshness_reason(
                source="feed", is_stale=True, has_timestamp=False,
                age_seconds=None, max_age_seconds=max_age_seconds,
            ),
            checked_at=now,
        )

    age = _age_seconds(now, feed.last_message_at)
    assert age is not None

    if max_age_seconds <= 0:
        return FreshnessStatus(
            symbol=symbol, source="feed",
            is_stale=False, age_seconds=age,
            last_seen_at=feed.last_message_at,
            max_age_seconds=max_age_seconds,
            reason=None, checked_at=now,
        )

    stale = age > max_age_seconds
    return FreshnessStatus(
        symbol=symbol, source="feed",
        is_stale=stale, age_seconds=age,
        last_seen_at=feed.last_message_at,
        max_age_seconds=max_age_seconds,
        reason=freshness_reason(
            source="feed", is_stale=stale, has_timestamp=True,
            age_seconds=age, max_age_seconds=max_age_seconds,
        ),
        checked_at=now,
    )


# ---------- order pre-check helpers ----------


def should_block_buy_for_quote(
    *,
    symbol:          str,
    last_seen_at:    datetime | None,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> tuple[bool, str | None, FreshnessStatus]:
    """단일 quote 기반 신규 BUY 차단 결정. SELL/청산은 별도 정책 — 호출자가 분기."""
    status = is_quote_stale(
        symbol=symbol, last_seen_at=last_seen_at,
        max_age_seconds=max_age_seconds, now=now,
    )
    return status.is_stale, status.reason, status


def should_block_buy_for_bar(
    db:              Session,
    *,
    symbol:          str,
    interval:        str,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> tuple[bool, str | None, FreshnessStatus]:
    """봉 캐시 기반 신규 BUY 차단 결정."""
    status = is_bar_stale(
        db, symbol=symbol, interval=interval,
        max_age_seconds=max_age_seconds, now=now,
    )
    return status.is_stale, status.reason, status


def should_block_buy_for_feed(
    *,
    symbol:          str,
    feed:            DataFeedState,
    max_age_seconds: int,
    now:             datetime | None = None,
) -> tuple[bool, str | None, FreshnessStatus]:
    """WebSocket / push feed 기반 신규 BUY 차단 결정."""
    status = is_feed_stale(
        symbol=symbol, feed=feed,
        max_age_seconds=max_age_seconds, now=now,
    )
    return status.is_stale, status.reason, status
