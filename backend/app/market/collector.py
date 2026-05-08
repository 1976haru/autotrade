"""Market data collector (#19).

`MarketDataAdapter` (Mock / Yfinance / 향후 KIS·Kiwoom)에서 OHLCV를 가져와
검증·정렬·중복 제거 후 `BarCache(MarketBar)`에 영구화한다.

본 단계 범위:
- 1m / 5m / 1h / 1d 봉 수집
- 데이터 무결성 검증 (`candle_builder.validate_bars`)
- timestamp 오름차순 정렬 + 중복 제거
- DB 저장 (`BarCache.save`)
- coverage / missing rate 계산
- staleness pre-check helper (read-only) — 호출자가 사용 여부 결정

본 단계 *비*범위 — Phase 2:
- 현재가 / 체결 / 호가 / 실시간 WebSocket 피드
- tick / orderbook 별도 테이블
- 실 broker API 라우팅 (KIS / Kiwoom adapter는 별도 옵트인 PR)

CLAUDE.md 절대 원칙 — 본 모듈은 broker.place_order / RiskManager /
PermissionGate / OrderExecutor를 import하지 않으며, 어떤 분기에도 영향을
주지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.market.base import Interval, MarketDataAdapter
from app.market.cache import BarCache
from app.market.candle_builder import (
    aggregate_1m_to_5m,
    compute_missing_rate,
    deduplicate_bars,
    expected_bar_count,
    sort_bars,
    validate_bars,
)
from app.market.staleness import is_bar_cache_stale


@dataclass(frozen=True)
class CollectionResult:
    """단일 (symbol, interval) 수집 결과."""
    symbol:         str
    interval:       str
    fetched_count:  int
    saved_count:    int
    expected_count: int
    missing_count:  int
    # expected_count == 0이면 None — 호출자가 표시 분기.
    missing_rate:   float | None
    coverage_score: float | None
    start:          datetime
    end:            datetime

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "interval":       self.interval,
            "fetched_count":  self.fetched_count,
            "saved_count":    self.saved_count,
            "expected_count": self.expected_count,
            "missing_count":  self.missing_count,
            "missing_rate":   self.missing_rate,
            "coverage_score": self.coverage_score,
            "start":          self.start.isoformat(),
            "end":            self.end.isoformat(),
        }


class MarketDataCollector:
    """`MarketDataAdapter`를 입력으로 받아 OHLCV를 수집·검증·저장한다.

    실 broker API를 직접 부르지 않는다 — 모든 외부 호출은 주입된 adapter를
    경유한다. 테스트는 Mock adapter를 주입해 외부 네트워크 0건으로 실행
    가능 (CI 정책).
    """

    def __init__(self, adapter: MarketDataAdapter):
        self.adapter = adapter

    async def collect(
        self,
        *,
        symbol:   str,
        start:    datetime,
        end:      datetime,
        interval: Interval,
        db:       Session | None = None,
    ) -> CollectionResult:
        """단일 (symbol, interval) 수집 + (db 주어지면) 저장 + coverage 산출."""
        if start > end:
            return _empty_result(symbol, interval.value, start, end)

        raw = await self.adapter.get_bars(symbol, start, end, interval)
        bars = validate_bars(raw)
        bars = deduplicate_bars(bars)  # 동일 timestamp 중복 정리 + 정렬
        # bars가 이미 sort_bars 통과하지만, 명시적 재정렬으로 가독성 보존.
        bars = sort_bars(bars)

        saved = 0
        if db is not None and bars:
            cache = BarCache(db)
            saved = cache.save(bars, interval.value)

        expected = expected_bar_count(start, end, interval.value)
        coverage = compute_missing_rate(
            expected_count=expected,
            actual_count=len(bars),
        )
        return CollectionResult(
            symbol=symbol,
            interval=interval.value,
            fetched_count=len(bars),
            saved_count=saved,
            expected_count=expected,
            missing_count=coverage["missing_count"],
            missing_rate=coverage["missing_rate"],
            coverage_score=coverage["coverage_score"],
            start=start,
            end=end,
        )

    async def collect_many(
        self,
        *,
        symbols:  list[str],
        start:    datetime,
        end:      datetime,
        interval: Interval,
        db:       Session | None = None,
    ) -> list[CollectionResult]:
        """여러 symbol을 직렬 수집. 한 symbol 실패가 전체를 막지 않도록
        adapter 예외는 호출자에게 전파 — 운영자가 retry / skip 정책을 결정한다.
        """
        out: list[CollectionResult] = []
        for sym in symbols:
            result = await self.collect(
                symbol=sym, start=start, end=end, interval=interval, db=db,
            )
            out.append(result)
        return out

    async def collect_and_aggregate_1m_to_5m(
        self,
        *,
        symbol: str,
        start:  datetime,
        end:    datetime,
        db:     Session | None = None,
    ) -> tuple[CollectionResult, CollectionResult]:
        """1분봉을 수집·저장 후 5분봉을 집계·저장.

        adapter가 1m을 직접 지원하면 본 메서드로 1m 캐시 → 5m 파생 캐시까지
        한 번에 만든다. 5m을 별도로 fetch하지 않아 호출/RPM 절약.
        """
        one_min = await self.collect(
            symbol=symbol, start=start, end=end, interval=Interval.MINUTE_1, db=db,
        )

        # 5m 집계는 adapter를 다시 부르지 않고 1m 결과에서 파생.
        if db is not None:
            cache = BarCache(db)
            one_min_bars = cache.get(symbol, Interval.MINUTE_1.value, start, end)
        else:
            one_min_bars = []  # db 없으면 5m 파생 불가 — 호출자에 알림.

        five_min_bars = aggregate_1m_to_5m(one_min_bars)
        saved_5m = 0
        if db is not None and five_min_bars:
            saved_5m = BarCache(db).save(five_min_bars, Interval.MINUTE_5.value)

        expected_5m = expected_bar_count(start, end, Interval.MINUTE_5.value)
        coverage_5m = compute_missing_rate(
            expected_count=expected_5m,
            actual_count=len(five_min_bars),
        )
        five_min = CollectionResult(
            symbol=symbol,
            interval=Interval.MINUTE_5.value,
            fetched_count=len(five_min_bars),
            saved_count=saved_5m,
            expected_count=expected_5m,
            missing_count=coverage_5m["missing_count"],
            missing_rate=coverage_5m["missing_rate"],
            coverage_score=coverage_5m["coverage_score"],
            start=start,
            end=end,
        )
        return one_min, five_min


def _empty_result(symbol: str, interval_str: str,
                  start: datetime, end: datetime) -> CollectionResult:
    return CollectionResult(
        symbol=symbol,
        interval=interval_str,
        fetched_count=0,
        saved_count=0,
        expected_count=0,
        missing_count=0,
        missing_rate=None,
        coverage_score=None,
        start=start,
        end=end,
    )


# ---------- order pre-check helper ----------
#
# 본 함수는 데이터 freshness 정책을 *조회만* 한다. 호출자(미래 LIVE order
# 라우팅 PR)가 이 결과를 보고 신규 BUY를 차단할 수 있다. 본 PR에서는
# `route_order` / RiskManager 흐름을 변경하지 않는다 — 정책과 helper만 제공.
#
# 자세한 정책: docs/market_data_collector.md "Staleness / Freshness 정책"


def should_block_new_buy(
    db:               Session,
    *,
    symbol:           str,
    interval:         str,
    max_age_seconds:  int,
    websocket_reconnecting: bool = False,
    now:              datetime | None = None,
) -> tuple[bool, str | None]:
    """신규 BUY를 차단해야 하는지 결정.

    - WebSocket reconnect 중: 무조건 차단 (피드가 멎은 상태에서 신호 위험)
    - bar cache stale: 차단
    - 그 외: 허용

    SELL/청산은 별도 정책 — 본 함수는 BUY pre-check 전용. 호출자는 매도/청산
    경로에서는 이 함수를 호출하지 않거나 `max_age_seconds`를 다르게 둔다.

    Returns: (block, reason). block=False면 reason=None.
    """
    if websocket_reconnecting:
        return True, "WebSocket reconnect 중 — 신규 BUY 차단 (시세 피드 단절)"

    if now is None:
        now = datetime.now(timezone.utc)

    stale, age = is_bar_cache_stale(
        db, symbol=symbol, interval=interval,
        max_age_seconds=max_age_seconds, now=now,
    )
    if stale:
        if age is None:
            return True, f"{symbol} {interval} 캐시가 비어있음 — 신규 BUY 차단"
        return True, (
            f"{symbol} {interval} 캐시가 stale ({age:.1f}s 경과, 한도 "
            f"{max_age_seconds}s) — 신규 BUY 차단"
        )

    return False, None
