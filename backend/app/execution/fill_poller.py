import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderStatus
from app.db.models import OrderAuditLog
from app.scheduler.market_clock import to_kst


logger = logging.getLogger(__name__)


_OPEN_STATUSES = ("RECEIVED", "PARTIALLY_FILLED")

# 2026-07-06 사고: 073240 매수(324주 중 1주만 체결)가 장마감 후에도 4시간+ 동안
# 5초 간격으로 재조회 → EGW00201(레이트리밋)에 매번 걸리며 1,294회 재시도.
# 근본원인: give-up 조건도 backoff도 없어 실패해도 그냥 다음 tick에 똑같이 재시도.
#
# 포기 조건 — 아래 중 하나라도 해당하면 더 조회하지 않고 CANCELED 로 확정한다
# (KRX 는 day order 라 미체결 잔량이 장마감에 자동 소멸 — 그 이상 조회해도 의미 없음):
#   1) 주문이 생성된 KST 거래일이 오늘(KST)과 다르다 (자정 넘어감)
#   2) 그 거래일의 정규장 마감(15:30 KST) + 유예 10분이 지났다
#   3) 어느 쪽도 아니어도 생성 후 4시간이 지났다 (장중에도 무한 매달림 방지 상한)
_SESSION_CLOSE = (15, 30)
_SESSION_CLOSE_GRACE = timedelta(minutes=10)
_MAX_POLL_AGE = timedelta(hours=4)

# 레이트리밋 등 조회 실패 시 지수 백오프 — 5초 고정 재시도로 API 를 두들기지 않는다.
_BACKOFF_BASE_SECONDS = 10
_BACKOFF_MAX_SECONDS = 300


def _order_stale(created_at: datetime, now: datetime) -> bool:
    """이 주문을 더 폴링할 이유가 없는지 — 세션 종료(day order 소멸) or 절대 상한."""
    created_kst = to_kst(created_at)
    now_kst = to_kst(now)
    if now_kst.date() > created_kst.date():
        return True
    close_kst = created_kst.replace(
        hour=_SESSION_CLOSE[0], minute=_SESSION_CLOSE[1], second=0, microsecond=0,
    ) + _SESSION_CLOSE_GRACE
    if now_kst >= close_kst:
        return True
    # to_kst() normalizes both to tz-aware KST regardless of whether the
    # inputs were naive (DB rows are naive-UTC by convention) or aware —
    # subtract the normalized values, not the raw args, to avoid a
    # naive/aware TypeError.
    return (now_kst - created_kst) >= _MAX_POLL_AGE


async def poll_once(
    broker: BrokerAdapter,
    db: Session,
    *,
    now: datetime | None = None,
    backoff: dict[int, tuple[int, float]] | None = None,
) -> int:
    """Update audit rows whose broker fill state may have advanced.

    Looks at OrderAuditLog rows where executed is True, broker_order_id is set,
    and broker_status is still RECEIVED or PARTIALLY_FILLED. Calls
    broker.get_order_status for each, writes back changed fields, and commits
    once at the end. Returns the number of rows actually changed.

    A NotImplementedError from the broker (e.g. a Mock that does not support
    get_order_status) ends the loop quietly — there is nothing to poll for
    that adapter. Other errors are logged and the next row is tried.

    Stale rows (see `_order_stale`) are given up on without calling the broker:
    marked CANCELED in place (whatever quantity had already filled stands),
    so they stop being a candidate on the next tick — this is what stops an
    unfillable/abandoned order from being retried forever.

    `backoff` is an optional caller-owned dict (audit.id -> (fail_count,
    retry_not_before_epoch_seconds)) used to space out retries after a
    failure with exponential backoff, capped at `_BACKOFF_MAX_SECONDS`. Pass
    None (default) to keep the old no-backoff behavior — existing callers/
    tests that don't pass it are unaffected.
    """
    now = now or datetime.now(timezone.utc)
    candidates = db.execute(
        select(OrderAuditLog).where(
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.broker_order_id.isnot(None),
            OrderAuditLog.broker_status.in_(_OPEN_STATUSES),
        )
    ).scalars().all()

    updated = 0
    for audit in candidates:
        if _order_stale(audit.created_at, now):
            logger.info(
                "fill poll giving up on audit %s (stale): status=%s filled=%s/%s",
                audit.id, audit.broker_status, audit.filled_quantity, audit.quantity,
            )
            audit.broker_status = OrderStatus.CANCELED.value
            updated += 1
            if backoff is not None:
                backoff.pop(audit.id, None)
            continue

        if backoff is not None:
            fail_count, retry_not_before = backoff.get(audit.id, (0, 0.0))
            if now.timestamp() < retry_not_before:
                continue  # 백오프 대기 중 — 이번 tick 은 건너뜀

        try:
            result = await broker.get_order_status(audit.broker_order_id)
        except NotImplementedError:
            return 0
        except Exception as e:
            logger.warning("fill poll failed for audit %s: %s", audit.id, e)
            if backoff is not None:
                fail_count, _ = backoff.get(audit.id, (0, 0.0))
                fail_count += 1
                delay = min(_BACKOFF_BASE_SECONDS * (2 ** (fail_count - 1)), _BACKOFF_MAX_SECONDS)
                backoff[audit.id] = (fail_count, now.timestamp() + delay)
            continue

        if backoff is not None:
            backoff.pop(audit.id, None)  # 성공 → 백오프 해제

        new_status = result.status.value
        new_filled = result.filled_quantity
        new_avg = result.avg_fill_price

        if (
            audit.broker_status == new_status
            and audit.filled_quantity == new_filled
            and (new_avg is None or audit.avg_fill_price == new_avg)
        ):
            continue

        audit.broker_status = new_status
        audit.filled_quantity = new_filled
        if new_avg is not None:
            audit.avg_fill_price = new_avg
        updated += 1

    if updated > 0:
        db.commit()
    return updated


class FillPoller:
    """Background task that calls poll_once on a fixed interval.

    The broker_factory and session_factory callables are invoked on every
    tick, so the poller naturally picks up the current configured broker
    (mock vs KIS) and an isolated DB session.
    """

    def __init__(
        self,
        broker_factory:  Callable[[], BrokerAdapter],
        session_factory: Callable[[], Session],
        interval:        int = 5,
        now_factory:     Callable[[], datetime] | None = None,
    ):
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.broker_factory  = broker_factory
        self.session_factory = session_factory
        self.interval        = interval
        self.now_factory     = now_factory or (lambda: datetime.now(timezone.utc))
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # audit.id -> (fail_count, retry_not_before_epoch_seconds). Lives only
        # for this poller instance's lifetime — a restart resets backoff state,
        # which is fine since _order_stale still bounds total retry time.
        self._backoff: dict[int, tuple[int, float]] = {}

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                broker = self.broker_factory()
                with self.session_factory() as db:
                    await poll_once(broker, db, now=self.now_factory(), backoff=self._backoff)
            except Exception as e:
                logger.warning("fill poller tick raised: %s", e)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=self.interval + 1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
